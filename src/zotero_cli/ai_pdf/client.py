"""MinerU 云端客户端：上传、轮询、下载与归档解析。"""

from __future__ import annotations

import io
import json
import shutil
import sys
import time
import zipfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import urljoin

from requests import Session

from zotero_cli.ai_pdf.request_utils import (
    _RETRYABLE_EXCEPTIONS,
    _as_mineru_error,
    _retry,
    _url_origin,
    _validated_model_version,
)
from zotero_cli.ai_pdf.types import (
    _MAX_ARCHIVE_BYTES,
    _MAX_ARCHIVE_ENTRIES,
    _MAX_ARCHIVE_MEMBER_BYTES,
    _MAX_ARCHIVE_TOTAL_BYTES,
    _MAX_FILE_BYTES,
    MinerUError,
    ProgressCallback,
)


@dataclass(frozen=True)
class _RemoteParse:
    markdown: str
    content_list: Any
    archive: bytes
    archive_members: list[str]
    batch_id: str


class MinerUClient:
    """官方 MinerU v4 上传、轮询、下载流程的最小客户端。"""

    _RATE_LIMIT = 50
    _RATE_WINDOW = 60.0
    _UPLOAD_TIMEOUT = 600

    def __init__(
        self,
        token: str,
        *,
        api_base: str = "https://mineru.net/api/v4",
        model_version: str = "vlm",
        session: Session | None = None,
    ) -> None:
        if not token.strip():
            raise MinerUError("MinerU token not configured. Set [pdf].mineru_token in .zot/config.toml.")
        self.token = token.strip()
        self.api_base = api_base.rstrip("/")
        self.model_version = _validated_model_version(model_version)
        if session is None:
            session = Session()
            session.trust_env = False
        self._session = session
        self._rate_limiter = _RateLimiter(self._RATE_LIMIT, self._RATE_WINDOW)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def parse_many(
        self,
        pdf_paths: list[Path],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[Path, _RemoteParse | Exception]:
        results: dict[Path, _RemoteParse | Exception] = {}
        valid: list[tuple[Path, str, str]] = []
        total = len(pdf_paths)

        for index, pdf_path in enumerate(pdf_paths, 1):
            if not pdf_path.exists():
                results[pdf_path] = FileNotFoundError(f"PDF not found: {pdf_path}")
            elif pdf_path.stat().st_size > _MAX_FILE_BYTES:
                results[pdf_path] = MinerUError("PDF exceeds MinerU's 200 MB file limit")
            else:
                remote_name = _remote_file_name(pdf_path)
                valid.append((pdf_path, remote_name, Path(remote_name).stem[:50]))
            if progress_callback:
                progress_callback("validate", index, total, 0)

        uploaded = 0
        processed = 0
        downloaded = 0
        for start in range(0, len(valid), 50):
            batch = valid[start : start + 50]

            def upload_progress(done: int) -> None:
                nonlocal uploaded
                uploaded = start + done
                if progress_callback:
                    progress_callback("upload", uploaded, len(valid), 0)

            try:
                batch_id = self._upload_batch(batch, upload_progress)
            except Exception as exc:
                for pdf_path, _name, _data_id in batch:
                    results[pdf_path] = _as_mineru_error(exc)
                continue

            def poll_progress(done: int, _batch_total: int) -> None:
                nonlocal processed
                processed = start + done
                if progress_callback:
                    progress_callback("process", processed, len(valid), 0)

            try:
                states = self._poll_batch(batch_id, len(batch), poll_progress)
            except Exception as exc:
                for pdf_path, _name, _data_id in batch:
                    results[pdf_path] = _as_mineru_error(exc)
                continue

            for pdf_path, remote_name, _data_id in batch:
                state, zip_url, error_message = states.get(remote_name, ("", "", "missing result"))
                if state != "done" or not zip_url:
                    results[pdf_path] = MinerUError(
                        f"MinerU extraction failed for {pdf_path.name}: {error_message or state or 'unknown state'}"
                    )
                    continue
                try:
                    archive = self._download_archive(zip_url)
                    markdown, content_list, members = _read_archive(archive)
                    results[pdf_path] = _RemoteParse(
                        markdown=markdown,
                        content_list=content_list,
                        archive=archive,
                        archive_members=members,
                        batch_id=batch_id,
                    )
                except Exception as exc:
                    results[pdf_path] = _as_mineru_error(exc)
                downloaded += 1
                if progress_callback:
                    progress_callback("download", downloaded, len(valid), 0)
        return results

    def _upload_batch(
        self,
        files: list[tuple[Path, str, str]],
        progress_callback: Callable[[int], None] | None = None,
    ) -> str:
        file_entries: list[dict[str, str]] = [{"name": name, "data_id": data_id} for _path, name, data_id in files]
        payload: dict[str, Any] = {"files": file_entries, "model_version": self.model_version}
        self._rate_limiter.acquire()
        # 创建批次不是幂等操作：响应丢失后重试会在服务端再建一个批次，
        # 重复 MinerU 的工作。让瞬时错误使本批次失败；下面的逐文件上传
        # PUT 是幂等的，会进行重试。
        response = self._session.post(
            f"{self.api_base}/file-urls/batch", json=payload, headers=self._headers, timeout=30
        )
        data = _response_data(response, "request upload URLs")
        batch_id = str(data.get("batch_id") or "")
        file_urls = data.get("file_urls") or []
        if not batch_id or len(file_urls) != len(files):
            raise MinerUError("MinerU returned an incomplete upload batch")

        for index, ((pdf_path, _name, _data_id), upload_url) in enumerate(zip(files, file_urls), 1):
            self._rate_limiter.acquire()

            def upload() -> Any:
                with pdf_path.open("rb") as source:
                    return self._session.put(upload_url, data=source, timeout=self._UPLOAD_TIMEOUT)

            upload_response = _retry(upload)
            if upload_response.status_code not in (200, 201):
                raise MinerUError(
                    f"Failed to upload {pdf_path.name}: HTTP {upload_response.status_code} {upload_response.text}"
                )
            if progress_callback:
                progress_callback(index)
        return batch_id

    def _poll_batch(
        self,
        batch_id: str,
        expected_count: int,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, tuple[str, str, str]]:
        for _attempt in range(360):
            self._rate_limiter.acquire()
            response = _retry(
                lambda: self._session.get(
                    f"{self.api_base}/extract-results/batch/{batch_id}", headers=self._headers, timeout=30
                )
            )
            data = _response_data(response, "poll extraction results")
            raw_results = data.get("extract_result") or []
            if not raw_results:
                raise MinerUError("MinerU response did not contain extract_result")

            states: dict[str, tuple[str, str, str]] = {}
            pending = 0
            done = 0
            for item in raw_results:
                name = str(item.get("file_name") or "")
                state = str(item.get("state") or "")
                zip_url = str(item.get("full_zip_url") or "")
                error_message = str(item.get("err_msg") or "")
                states[name] = (state, zip_url, error_message)
                if state in {"waiting-file", "pending", "running"}:
                    pending += 1
                elif state == "done":
                    done += 1
            if progress_callback:
                progress_callback(done, expected_count)
            if pending == 0:
                return states
            time.sleep(5)
        raise MinerUError(f"Timed out waiting for MinerU batch {batch_id}")

    def _download_archive(self, zip_url: str) -> bytes:
        self._rate_limiter.acquire()
        for _redirect in range(31):
            trusted = _url_origin(zip_url) == _url_origin(self.api_base)
            headers: dict[str, str | None] = {"Authorization": self._headers["Authorization"] if trusted else None}
            # None 会移除继承的 session 头；显式 auth 绕过 session/netrc/URL
            # 凭证，包括预签名下载场景。
            response = _retry(
                lambda: self._session.get(
                    zip_url,
                    headers=headers,  # ty: ignore[invalid-argument-type]  # None 会移除继承的请求头
                    auth=lambda request: request,
                    timeout=300,
                    allow_redirects=False,
                )
            )
            # requests 在某些跨源重定向时会保留 auth（尤其是 http -> https），
            # 因此每次实际 GET 前都要重新核对源。
            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                zip_url = urljoin(zip_url, response.headers["Location"])
                response.close()
                continue
            if response.status_code != 200:
                raise MinerUError(
                    f"Failed to download MinerU result: HTTP {response.status_code} {_error_text(response)}"
                )
            try:
                return _read_limited_body(response)
            except _RETRYABLE_EXCEPTIONS:
                # 在流式读取大体积响应时连接可能中断，而这发生在上面请求级
                # 重试已经成功之后。下载是幂等的，因此重新发起恰好一次 GET
                # 并使用其响应体。
                retried = _retry(
                    lambda: self._session.get(
                        zip_url,
                        headers=headers,  # ty: ignore[invalid-argument-type]  # None 会移除继承的请求头
                        auth=lambda request: request,
                        timeout=300,
                        allow_redirects=False,
                    )
                )
                if retried.status_code != 200:
                    raise MinerUError(
                        f"Failed to download MinerU result: HTTP {retried.status_code} {_error_text(retried)}"
                    )
                return _read_limited_body(retried)
        raise MinerUError("Too many redirects while downloading MinerU result")


def _format_byte_limit(limit: int) -> str:
    """精确格式化字节上限（例如 100 B、1.5 KB、200 MB、1 GB）。"""
    for unit, size in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if limit >= size:
            amount = limit / size
            return f"{int(amount)} {unit}" if amount.is_integer() else f"{amount:.1f} {unit}"
    return f"{limit} B"


def _error_text(response: Any) -> str:
    text = getattr(response, "text", "") or ""
    return text[:500]


def _read_limited_body(response: Any) -> bytes:
    """读取下载响应体，缓冲量不超过归档上限。"""
    headers = getattr(response, "headers", None) or {}
    header_length = headers.get("Content-Length")
    if header_length is not None:
        try:
            declared = int(header_length)
        except (TypeError, ValueError):
            declared = None
        if declared is not None and declared > _MAX_ARCHIVE_BYTES:
            raise MinerUError(f"MinerU result archive exceeds the {_format_byte_limit(_MAX_ARCHIVE_BYTES)} limit")
    iter_content = getattr(response, "iter_content", None)
    if iter_content is None:
        data = bytes(response.content)
        if len(data) > _MAX_ARCHIVE_BYTES:
            raise MinerUError(f"MinerU result archive exceeds the {_format_byte_limit(_MAX_ARCHIVE_BYTES)} limit")
        return data
    chunks: list[bytes] = []
    total = 0
    for chunk in iter_content(chunk_size=1024 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > _MAX_ARCHIVE_BYTES:
            raise MinerUError(f"MinerU result archive exceeds the {_format_byte_limit(_MAX_ARCHIVE_BYTES)} limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _remote_file_name(pdf_path: Path) -> str:
    path_hash = sha256(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"{pdf_path.stem}.{path_hash}{pdf_path.suffix or '.pdf'}"


def _response_data(response: Any, action: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise MinerUError(f"Failed to {action}: HTTP {response.status_code} {_error_text(response)}")
    try:
        payload = response.json()
    except Exception as exc:
        raise MinerUError(f"MinerU returned invalid JSON while trying to {action}") from exc
    if not isinstance(payload, dict):
        raise MinerUError(f"MinerU returned an invalid response while trying to {action}")
    if payload.get("code") not in (0, "0", None):
        raise MinerUError(f"MinerU rejected {action}: {payload.get('msg') or payload.get('message') or payload}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MinerUError(f"MinerU returned invalid data while trying to {action}")
    return data


def _validate_archive_members(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > _MAX_ARCHIVE_ENTRIES:
        raise MinerUError(f"MinerU ZIP contains too many entries ({len(infos)})")
    total = 0
    for info in infos:
        if info.is_dir():
            continue
        if info.file_size > _MAX_ARCHIVE_MEMBER_BYTES:
            raise MinerUError(
                f"MinerU ZIP member size exceeds the {_format_byte_limit(_MAX_ARCHIVE_MEMBER_BYTES)} limit"
            )
        total += info.file_size
        if total > _MAX_ARCHIVE_TOTAL_BYTES:
            raise MinerUError(
                f"MinerU ZIP uncompressed size exceeds the {_format_byte_limit(_MAX_ARCHIVE_TOTAL_BYTES)} limit"
            )


def _read_archive(archive_bytes: bytes) -> tuple[str, Any, list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = archive.namelist()
            _validate_archive_members(archive)
            markdown_name = _find_member(members, "full.md", suffix=".md")
            content_name = _find_member(members, "content_list.json", suffix="_content_list.json")
            if markdown_name is None:
                raise MinerUError(f"MinerU ZIP has no Markdown file: {members}")
            if content_name is None:
                raise MinerUError(f"MinerU ZIP has no content_list JSON: {members}")
            markdown = archive.read(markdown_name).decode("utf-8", errors="replace")
            content_list = json.loads(archive.read(content_name).decode("utf-8", errors="replace"))
            if not isinstance(content_list, (list, dict)):
                raise MinerUError("MinerU content_list JSON must be an object or array")
            return markdown, content_list, members
    except zipfile.BadZipFile as exc:
        raise MinerUError("MinerU result is not a valid ZIP archive") from exc
    except json.JSONDecodeError as exc:
        raise MinerUError("MinerU content_list JSON is invalid") from exc


def _find_member(members: list[str], exact_basename: str, *, suffix: str) -> str | None:
    for member in members:
        if Path(member).name == exact_basename:
            return member
    for member in members:
        if Path(member).name.lower().endswith(suffix.lower()):
            return member
    return None


def _safe_extract_zip(archive: zipfile.ZipFile, output_dir: Path) -> None:
    root = output_dir.resolve()
    extracted = 0
    total = 0
    for member in archive.infolist():
        if member.is_dir():
            continue
        if Path(member.filename).name.lower().endswith("_origin.pdf"):
            continue
        if member.file_size > _MAX_ARCHIVE_MEMBER_BYTES:
            raise MinerUError(
                f"MinerU ZIP member size exceeds the {_format_byte_limit(_MAX_ARCHIVE_MEMBER_BYTES)} limit"
            )
        total += member.file_size
        if total > _MAX_ARCHIVE_TOTAL_BYTES:
            raise MinerUError(
                f"MinerU ZIP uncompressed size exceeds the {_format_byte_limit(_MAX_ARCHIVE_TOTAL_BYTES)} limit"
            )
        extracted += 1
        if extracted > _MAX_ARCHIVE_ENTRIES:
            raise MinerUError(f"MinerU ZIP contains too many entries (> {_MAX_ARCHIVE_ENTRIES})")
        target = (output_dir / member.filename).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


class _RateLimiter:
    """仅作用于本进程/本实例的滑动窗口限流器。

    此处不做跨进程的 MinerU 请求限流：并发的 CLI 调用各自维护窗口，
    而解析缓存锁可防止同一 PDF 的重复工作。
    """

    def __init__(self, limit: int, window: float) -> None:
        self._limit = limit
        self._window = window
        self._timestamps: deque[float] = deque()
        self._lock = Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.time()
                while self._timestamps and self._timestamps[0] <= now - self._window:
                    self._timestamps.popleft()
                if len(self._timestamps) < self._limit:
                    self._timestamps.append(now)
                    return
                sleep_for = self._timestamps[0] - (now - self._window)
            if sleep_for > 0:
                sys.stderr.write(f"\r{' ' * 60}\r    [rate-limit] sleeping {sleep_for:.1f}s")
                sys.stderr.flush()
                time.sleep(sleep_for)
