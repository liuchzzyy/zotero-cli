"""MinerU cloud client: upload, poll, download, and archive parsing."""

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

from requests import Session  # type: ignore[import-untyped]

from zotero_cli.core.mineru.models import _MAX_FILE_BYTES, MinerUError, ProgressCallback
from zotero_cli.core.mineru.utils import _as_mineru_error, _retry, _url_origin, _validated_model_version


@dataclass(frozen=True)
class _RemoteParse:
    markdown: str
    content_list: Any
    archive: bytes
    archive_members: list[str]
    batch_id: str


class MinerUClient:
    """Minimal client for the official MinerU v4 upload, poll, and download flow."""

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
        payload = {
            "files": [{"name": name, "data_id": data_id} for _path, name, data_id in files],
            "model_version": self.model_version,
        }
        self._rate_limiter.acquire()
        response = _retry(
            lambda: self._session.post(
                f"{self.api_base}/file-urls/batch", json=payload, headers=self._headers, timeout=30
            )
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
            # None removes inherited session headers; explicit auth bypasses
            # session/netrc/URL credentials, including on presigned downloads.
            response = _retry(
                lambda: self._session.get(
                    zip_url, headers=headers, auth=lambda request: request, timeout=300, allow_redirects=False
                )
            )
            # requests preserves auth on some cross-origin redirects (notably
            # http -> https), so recheck the origin before every actual GET.
            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                zip_url = urljoin(zip_url, response.headers["Location"])
                response.close()
                continue
            if response.status_code != 200:
                raise MinerUError(f"Failed to download MinerU result: HTTP {response.status_code} {response.text}")
            return bytes(response.content)
        raise MinerUError("Too many redirects while downloading MinerU result")


def _remote_file_name(pdf_path: Path) -> str:
    path_hash = sha256(str(pdf_path.resolve()).encode("utf-8")).hexdigest()[:12]
    return f"{pdf_path.stem}.{path_hash}{pdf_path.suffix or '.pdf'}"


def _response_data(response: Any, action: str) -> dict[str, Any]:
    if response.status_code != 200:
        raise MinerUError(f"Failed to {action}: HTTP {response.status_code} {response.text}")
    try:
        payload = response.json()
    except Exception as exc:
        raise MinerUError(f"MinerU returned invalid JSON while trying to {action}") from exc
    if payload.get("code") not in (0, "0", None):
        raise MinerUError(f"MinerU rejected {action}: {payload.get('msg') or payload.get('message') or payload}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise MinerUError(f"MinerU returned invalid data while trying to {action}")
    return data


def _read_archive(archive_bytes: bytes) -> tuple[str, Any, list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = archive.namelist()
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
    for member in archive.infolist():
        if member.is_dir():
            continue
        if Path(member.filename).name.lower().endswith("_origin.pdf"):
            continue
        target = (output_dir / member.filename).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


class _RateLimiter:
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
