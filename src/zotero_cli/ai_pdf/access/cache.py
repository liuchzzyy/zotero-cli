"""规范 MinerU 解析包的内容寻址缓存。

写入分两阶段（mkdtemp 暂存目录 + 同一文件系统内替换），并由按指纹
加锁的锁文件保护，使并发的 CLI 进程对每份 PDF 最多只解析一次。锁会记录
持有者 PID：已死的锁立即回收，确认存活时只等待，无法探测存活性时才按年龄回收。隐藏的
暂存/锁条目不计入统计与列表，被杀掉的写入留下的过期暂存目录会在下次解析前清扫。
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
from collections.abc import Generator, Sequence
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from zotero_cli.ai_pdf.access.client import MinerUClient, _RemoteParse, _safe_extract_zip
from zotero_cli.ai_pdf.access.requests import _as_mineru_error, _validated_model_version
from zotero_cli.ai_pdf.errors import MinerUError
from zotero_cli.ai_pdf.models import MinerUParseResult, ProgressCallback
from zotero_cli.config import load_pdf_config
from zotero_cli.config.runtime import section
from zotero_cli.pdf_rag.store import mineru_cache_dir

_CACHE_SCHEMA_VERSION = 1
_LOGGER = logging.getLogger(__name__)

# 跨进程解析预占。第二个 CLI 进程会等待第一个，而不是重复发起 MinerU 批次。
# 已死的持有者立即回收；确认存活的持有者只等待到超时，不能仅凭年龄抢锁。
# 无法探测存活性时才按过期窗口回收，该窗口保持在最大等待时间之内。
_LOCK_DIRNAME = ".locks"
_CACHE_KEYS = {
    "_LOCK_POLL_SECONDS": "lock_poll_seconds",
    "_LOCK_TIMEOUT_SECONDS": "lock_timeout_seconds",
    "_LOCK_STALE_SECONDS": "lock_stale_seconds",
    "_STAGING_STALE_SECONDS": "staging_stale_seconds",
}


def __getattr__(name: str) -> Any:
    key = _CACHE_KEYS.get(name)
    if key is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return section("pdf")["cache"][key]


def _policy(name: str) -> float:
    """经模块属性读取，使测试的 monkeypatch 与 TOML 覆盖都生效。"""
    return float(getattr(sys.modules[__name__], name))


def file_sha256(path: Path) -> str:
    """计算 PDF 内容指纹，作为解析缓存的内容寻址键。"""
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lock_owner_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _process_is_alive(pid: int) -> bool | None:
    """尽力探测进程是否存活；平台无法判断时返回 None。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


@contextmanager
def _exclusive_lock(lock_path: Path, *, timeout: float, poll: float, stale: float) -> Generator[None]:
    """创建排他锁文件；仅回收已死或存活性未知且过期的持有者。"""
    deadline = time.monotonic() + timeout
    notified = False
    while True:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            owner = _lock_owner_pid(lock_path)
            alive = _process_is_alive(owner) if owner is not None else None
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0.0
            if alive is False or (alive is None and age > stale):
                with contextlib.suppress(OSError):
                    lock_path.unlink()
                continue
            if not notified:
                _LOGGER.info("等待另一个进程完成 PDF 解析：%s", lock_path.stem[:12])
                notified = True
            if time.monotonic() >= deadline:
                raise MinerUError(
                    f"Timed out waiting for another process to finish parsing {lock_path.stem}; "
                    "retry once that process has finished"
                )
            time.sleep(poll)
            continue
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
        finally:
            os.close(descriptor)
        break
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            lock_path.unlink()


class MinerUParseCache:
    """规范 MinerU 解析包的内容寻址缓存。"""

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

    def lookup(self, pdf_path: Path) -> tuple[MinerUParseResult | None, str | None]:
        """返回缓存的解析包与 PDF 指纹；PDF 不存在时返回 (None, None)。"""
        if not pdf_path.exists():
            return None, None
        fingerprint = file_sha256(pdf_path)
        return self._load_result(pdf_path, fingerprint), fingerprint

    def get(self, pdf_path: Path) -> MinerUParseResult | None:
        return self.lookup(pdf_path)[0]

    def ensure(
        self,
        pdf_path: Path,
        progress_callback: ProgressCallback | None = None,
        *,
        fingerprint: str | None = None,
    ) -> MinerUParseResult:
        if fingerprint is None:
            if not pdf_path.exists():
                if progress_callback:
                    progress_callback("cache", 1, 1, 0)
                raise FileNotFoundError(f"PDF not found: {pdf_path}")
            try:
                fingerprint = file_sha256(pdf_path)
            except OSError as exc:
                raise _as_mineru_error(exc) from exc
        try:
            cached = self._load_result(pdf_path, fingerprint)
        finally:
            if progress_callback:
                progress_callback("cache", 1, 1, 0)
        if cached is not None:
            return cached
        with self._fingerprint_locks([fingerprint]):
            cached = self._load_result(pdf_path, fingerprint)
            if cached is not None:
                return cached
            if progress_callback:
                progress_callback("cache-miss", 1, 1, 0)
            results = self._parse_and_store([(pdf_path, fingerprint)], progress_callback)
        parsed = results.get(pdf_path)
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
        misses: list[tuple[Path, str]] = []
        unique_paths = list(dict.fromkeys(pdf_paths))
        for index, pdf_path in enumerate(unique_paths, 1):
            try:
                if not pdf_path.exists():
                    results[pdf_path] = FileNotFoundError(f"PDF not found: {pdf_path}")
                else:
                    fingerprint = file_sha256(pdf_path)
                    cached = self._load_result(pdf_path, fingerprint)
                    if cached is not None:
                        results[pdf_path] = cached
                    else:
                        misses.append((pdf_path, fingerprint))
            except OSError as exc:
                results[pdf_path] = _as_mineru_error(exc)
            if progress_callback:
                progress_callback("cache", index, len(unique_paths), 0)

        if not misses:
            return results
        if progress_callback:
            progress_callback("cache-miss", len(misses), len(unique_paths), 0)

        with self._fingerprint_locks([fingerprint for _path, fingerprint in misses]):
            remaining: list[tuple[Path, str]] = []
            for pdf_path, fingerprint in misses:
                cached = self._load_result(pdf_path, fingerprint)
                if cached is not None:
                    results[pdf_path] = cached
                else:
                    remaining.append((pdf_path, fingerprint))
            if remaining:
                results.update(self._parse_and_store(remaining, progress_callback))
        return results

    def _parse_and_store(
        self,
        misses: list[tuple[Path, str]],
        progress_callback: ProgressCallback | None,
    ) -> dict[Path, MinerUParseResult | Exception]:
        self._cleanup_stale_staging([fingerprint for _path, fingerprint in misses])
        results: dict[Path, MinerUParseResult | Exception] = {}
        client = self._client or MinerUClient(self._token, model_version=self.model_version)
        remote_results = client.parse_many([pdf_path for pdf_path, _fingerprint in misses], progress_callback)
        for pdf_path, fingerprint in misses:
            remote = remote_results.get(pdf_path)
            if isinstance(remote, _RemoteParse):
                try:
                    results[pdf_path] = self._store(pdf_path, fingerprint, remote)
                except Exception as exc:
                    results[pdf_path] = _as_mineru_error(exc)
            elif isinstance(remote, Exception):
                results[pdf_path] = remote
            else:
                results[pdf_path] = MinerUError(f"MinerU did not return a result for {pdf_path.name}")
        return results

    def _cleanup_stale_staging(self, fingerprints: Sequence[str]) -> int:
        """删除这些指纹下被中断写入留下的隐藏暂存目录。"""
        removed = 0
        cutoff = time.time() - _policy("_STAGING_STALE_SECONDS")
        for fingerprint in sorted(set(fingerprints)):
            fingerprint_dir = self.root / fingerprint
            if fingerprint_dir.is_symlink() or not fingerprint_dir.is_dir():
                continue
            try:
                candidates = list(fingerprint_dir.iterdir())
            except OSError:
                continue
            for staging in candidates:
                if not staging.name.startswith(".") or not staging.is_dir():
                    continue
                try:
                    if staging.stat().st_mtime > cutoff:
                        continue
                except OSError:
                    continue
                shutil.rmtree(staging, ignore_errors=True)
                removed += 1
        return removed

    @contextmanager
    def _fingerprint_locks(self, fingerprints: Sequence[str]) -> Generator[None]:
        lock_dir = self.root / _LOCK_DIRNAME
        lock_dir.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            for fingerprint in sorted(set(fingerprints)):
                stack.enter_context(
                    _exclusive_lock(
                        lock_dir / f"{fingerprint}.lock",
                        timeout=_policy("_LOCK_TIMEOUT_SECONDS"),
                        poll=_policy("_LOCK_POLL_SECONDS"),
                        stale=_policy("_LOCK_STALE_SECONDS"),
                    )
                )
            yield

    def clear(self) -> int:
        """删除缓存根目录，仅把有效解析包计入删除数量。

        根目录先被重命名为隐藏的墓碑目录（同一文件系统），使崩溃或删除失败
        绝不会留下半可读的缓存根目录；随后尽力删除该墓碑目录。
        """
        if not self.root.exists():
            return 0
        count = len(self._valid_entries())
        tombstone = self.root.with_name(f".{self.root.name}-clearing-{os.getpid()}-{uuid.uuid4().hex[:8]}")
        try:
            os.replace(self.root, tombstone)
        except OSError:
            # 重命名是尽力而为：失败时回退为直接删除。
            shutil.rmtree(self.root)
            return count
        try:
            shutil.rmtree(tombstone)
        except OSError as exc:
            raise OSError(f"MinerU cache was detached to {tombstone} but could not be fully removed: {exc}") from exc
        return count

    def _manifest_paths(self) -> list[Path]:
        return sorted(
            path
            for path in self.root.glob("*/*/manifest.json")
            if not any(part.startswith(".") for part in path.relative_to(self.root).parts)
        )

    def _valid_entries(self) -> list[tuple[Path, dict[str, Any]]]:
        """返回 (manifest 路径, 解析后的 manifest)，跳过损坏/隐藏条目。"""
        valid: list[tuple[Path, dict[str, Any]]] = []
        if not self.root.exists():
            return valid
        for manifest_path in self._manifest_paths():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                valid.append((manifest_path, data))
        return valid

    def _visible_bytes(self, entries: Sequence[tuple[Path, dict[str, Any]]]) -> int:
        total = 0
        for manifest_path, _data in entries:
            package_dir = manifest_path.parent
            for path in package_dir.rglob("*"):
                relative = path.relative_to(package_dir)
                if not path.is_file() or any(part.startswith(".") for part in relative.parts):
                    continue
                try:
                    total += path.stat().st_size
                except OSError:
                    continue
        return total

    def stats(self) -> dict[str, int]:
        """统计有效解析包的 manifest 数量及其可见字节数。

        损坏的 manifest 与隐藏的暂存/锁条目会和 entries() 一样被忽略，
        因此 stats.entries 始终等于 len(entries())。
        """
        entries = self._valid_entries()
        return {"entries": len(entries), "total_bytes": self._visible_bytes(entries)}

    def entries(self) -> list[dict[str, Any]]:
        return [data for _path, data in self._valid_entries()]

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
                "parsed_at": datetime.now(UTC).isoformat(),
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
