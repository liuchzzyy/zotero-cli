from __future__ import annotations

import io
import json
import re
import shutil
import sys
import tempfile
import time
import zipfile
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any

from requests import Session  # type: ignore[import-untyped]

from zotero_cli.config import load_pdf_config
from zotero_cli.core.workspace import mineru_cache_dir

ProgressCallback = Callable[[str, int, int, int], None]

_CACHE_SCHEMA_VERSION = 1
_MAX_FILE_BYTES = 200 * 1024 * 1024
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class MinerUError(Exception):
    """Raised when a cloud parse or local parse-package operation fails."""


@dataclass(frozen=True)
class MinerUParseResult:
    """One canonical MinerU parse package shared by AI notes and RAG."""

    source_path: Path
    fingerprint: str
    model_version: str
    root: Path
    markdown_path: Path
    content_list_path: Path
    manifest_path: Path

    @property
    def markdown(self) -> str:
        return self.markdown_path.read_text(encoding="utf-8")

    @property
    def content_list(self) -> Any:
        return json.loads(self.content_list_path.read_text(encoding="utf-8"))

    @property
    def image_paths(self) -> list[Path]:
        raw_dir = self.root / "raw"
        if not raw_dir.exists():
            return []
        return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES)


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
        self.model_version = model_version
        self._session = session or Session()
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
        response = _retry(lambda: self._session.get(zip_url, headers=self._headers, timeout=300))
        if response.status_code != 200:
            raise MinerUError(f"Failed to download MinerU result: HTTP {response.status_code} {response.text}")
        return bytes(response.content)


class MinerUParseCache:
    """Content-addressed cache for canonical MinerU parse packages."""

    def __init__(
        self,
        root: Path | None = None,
        *,
        client: MinerUClient | None = None,
        token: str | None = None,
        model_version: str | None = None,
    ) -> None:
        config = load_pdf_config()
        self.root = root or mineru_cache_dir()
        self.model_version = model_version or config.mineru_model_version
        self._client = client
        self._token = token if token is not None else config.mineru_token

    def get(self, pdf_path: Path) -> MinerUParseResult | None:
        if not pdf_path.exists():
            return None
        fingerprint = file_sha256(pdf_path)
        return self._load_result(pdf_path, fingerprint)

    def ensure(
        self,
        pdf_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> MinerUParseResult:
        result = self.get(pdf_path)
        if result is not None:
            if progress_callback:
                progress_callback("cache", 1, 1, 0)
            return result
        parsed = self.ensure_many([pdf_path], progress_callback).get(pdf_path)
        if isinstance(parsed, MinerUParseResult):
            return parsed
        if isinstance(parsed, Exception):
            raise parsed
        raise MinerUError(f"MinerU did not return a parse result for {pdf_path}")

    def ensure_many(
        self,
        pdf_paths: list[Path],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[Path, MinerUParseResult | Exception]:
        results: dict[Path, MinerUParseResult | Exception] = {}
        misses: list[Path] = []
        fingerprints: dict[Path, str] = {}
        unique_paths = list(dict.fromkeys(pdf_paths))
        for index, pdf_path in enumerate(unique_paths, 1):
            if not pdf_path.exists():
                results[pdf_path] = FileNotFoundError(f"PDF not found: {pdf_path}")
            else:
                fingerprint = file_sha256(pdf_path)
                fingerprints[pdf_path] = fingerprint
                cached = self._load_result(pdf_path, fingerprint)
                if cached is not None:
                    results[pdf_path] = cached
                else:
                    misses.append(pdf_path)
            if progress_callback:
                progress_callback("cache", index, len(unique_paths), 0)

        if not misses:
            return results
        if progress_callback:
            progress_callback("cache-miss", len(misses), len(unique_paths), 0)

        client = self._client or MinerUClient(self._token, model_version=self.model_version)
        remote_results = client.parse_many(misses, progress_callback)
        for pdf_path in misses:
            remote = remote_results.get(pdf_path)
            if isinstance(remote, _RemoteParse):
                try:
                    results[pdf_path] = self._store(pdf_path, fingerprints[pdf_path], remote)
                except Exception as exc:
                    results[pdf_path] = _as_mineru_error(exc)
            elif isinstance(remote, Exception):
                results[pdf_path] = remote
            else:
                results[pdf_path] = MinerUError(f"MinerU did not return a result for {pdf_path.name}")
        return results

    def clear(self) -> int:
        count = self.stats()["entries"]
        if self.root.exists():
            shutil.rmtree(self.root)
        return count

    def stats(self) -> dict[str, int]:
        manifests = list(self.root.glob("*/*/manifest.json")) if self.root.exists() else []
        total_bytes = (
            sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file()) if self.root.exists() else 0
        )
        return {"entries": len(manifests), "total_bytes": total_bytes}

    def entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not self.root.exists():
            return entries
        for manifest_path in sorted(self.root.glob("*/*/manifest.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries.append(data)
        return entries

    def _cache_path(self, fingerprint: str) -> Path:
        safe_model = re.sub(r"[^a-zA-Z0-9._-]+", "-", self.model_version).strip("-") or "default"
        return self.root / fingerprint / safe_model

    def _load_result(self, pdf_path: Path, fingerprint: str) -> MinerUParseResult | None:
        root = self._cache_path(fingerprint)
        manifest_path = root / "manifest.json"
        markdown_path = root / "document.md"
        content_list_path = root / "content_list.json"
        if not (manifest_path.is_file() and markdown_path.is_file() and content_list_path.is_file()):
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                manifest.get("schema_version") != _CACHE_SCHEMA_VERSION
                or manifest.get("source_sha256") != fingerprint
                or manifest.get("mineru_model_version") != self.model_version
            ):
                return None
            json.loads(content_list_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return MinerUParseResult(
            source_path=pdf_path,
            fingerprint=fingerprint,
            model_version=self.model_version,
            root=root,
            markdown_path=markdown_path,
            content_list_path=content_list_path,
            manifest_path=manifest_path,
        )

    def _store(self, pdf_path: Path, fingerprint: str, remote: _RemoteParse) -> MinerUParseResult:
        destination = self._cache_path(fingerprint)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=str(destination.parent)))
        try:
            raw_dir = staging / "raw"
            raw_dir.mkdir(parents=True)
            with zipfile.ZipFile(io.BytesIO(remote.archive)) as archive:
                _safe_extract_zip(archive, raw_dir)
            (staging / "document.md").write_text(remote.markdown, encoding="utf-8")
            (staging / "content_list.json").write_text(
                json.dumps(remote.content_list, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            stat = pdf_path.stat()
            manifest = {
                "schema_version": _CACHE_SCHEMA_VERSION,
                "source_path": str(pdf_path.resolve()),
                "source_name": pdf_path.name,
                "source_size": stat.st_size,
                "source_mtime_ns": stat.st_mtime_ns,
                "source_sha256": fingerprint,
                "mineru_model_version": self.model_version,
                "parsed_at": datetime.now(timezone.utc).isoformat(),
                "batch_id": remote.batch_id,
                "markdown_file": "document.md",
                "structured_json_file": "content_list.json",
                "archive_members": remote.archive_members,
            }
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            if destination.exists():
                shutil.rmtree(destination)
            staging.replace(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        result = self._load_result(pdf_path, fingerprint)
        if result is None:
            raise MinerUError(f"Failed to validate cached MinerU package for {pdf_path.name}")
        return result


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_doi_from_markdown(markdown: str) -> str | None:
    match = re.search(r"10\.\d{4,9}/[^\s<>]+", markdown[:30000], flags=re.IGNORECASE)
    return match.group(0).rstrip(".,;:)]}>'\"") if match else None


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


def _retry(function: Callable[[], Any]) -> Any:
    for attempt in range(3):
        try:
            return function()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)
    raise AssertionError("unreachable")


def _as_mineru_error(exc: Exception) -> Exception:
    return exc if isinstance(exc, (MinerUError, FileNotFoundError)) else MinerUError(f"MinerU request failed: {exc}")


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
