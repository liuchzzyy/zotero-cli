"""Content-addressed cache for canonical MinerU parse packages."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from zotero_cli.config import load_pdf_config
from zotero_cli.core.mineru.client import MinerUClient, _RemoteParse, _safe_extract_zip
from zotero_cli.core.mineru.models import MinerUError, MinerUParseResult, ProgressCallback
from zotero_cli.core.mineru.utils import _as_mineru_error, _validated_model_version, file_sha256
from zotero_cli.core.workspace_store import mineru_cache_dir

_CACHE_SCHEMA_VERSION = 1


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
        self.model_version = _validated_model_version(
            config.mineru_model_version if model_version is None else model_version
        )
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
            try:
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
            except OSError as exc:
                results[pdf_path] = _as_mineru_error(exc)
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
            if isinstance(data, dict):
                entries.append(data)
        return entries

    def _cache_path(self, fingerprint: str) -> Path:
        model_version = _validated_model_version(self.model_version)
        destination = self.root / fingerprint / model_version
        expected = self.root.resolve() / fingerprint / model_version
        if destination.is_symlink() or destination.resolve() != expected:
            raise MinerUError("MinerU cache path must stay strictly inside its fingerprint directory")
        return destination

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
                not isinstance(manifest, dict)
                or manifest.get("schema_version") != _CACHE_SCHEMA_VERSION
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
