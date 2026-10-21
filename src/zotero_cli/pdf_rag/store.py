"""仓库内的 pdf_rag manifest、索引路径与 MinerU 缓存目录。"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath

from zotero_cli.config import pdf_rag_root, state_dir

# pdf_rag 名称是路径组件：允许 Unicode/CJK 名称以及 _ 和 -，
# 同时要求首尾字符为字母数字或 CJK 字符。
_NAME_RE = re.compile(r"^[a-z0-9\u4e00-\u9fff]+(?:[-_][a-z0-9\u4e00-\u9fff]+)*$")
_MAX_NAME_BYTES = 200
_PDF_RAG_DIRNAME = "pdf_rag"


class PdfRagManifestError(ValueError):
    """当 pdf_rag manifest 不可读或不符合 schema 时抛出。"""


def pdf_rags_dir() -> Path:
    return pdf_rag_root()


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"Unsafe pdf_rag path: {path}; symbolic links are not allowed")


def _pdf_rag_root() -> Path:
    root = pdf_rags_dir()
    _reject_symlink(root)
    return root


def _contained_path(path: Path, root: Path) -> Path:
    """在解析 pdf_rag 路径之前检查已存在的和悬空的链接。"""
    _reject_symlink(path)
    try:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"Unsafe pdf_rag path: {path}; unable to resolve path") from exc
    if resolved_path == resolved_root or not resolved_path.is_relative_to(resolved_root):
        raise ValueError(f"Unsafe pdf_rag path: {path}; path must stay inside {root}")
    return path


def pdf_rag_dir(name: str) -> Path:
    """解析单个 pdf_rag 路径组件，不允许使用用户提供的路径。

    保持旧名称（包括空格和 Unicode）仍可用，且不受 CLI 创建 pdf_rag 时采用的
    更严格命名约定影响。
    """
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or "\x00" in name
        or Path(name).is_absolute()
        or PureWindowsPath(name).drive
    ):
        raise ValueError(f"Invalid pdf_rag name: {name!r}; expected a single directory name")
    root = _pdf_rag_root()
    return _contained_path(root / name, root)


def pdf_rag_file_path(name: str) -> Path:
    directory = pdf_rag_dir(name)
    return _contained_path(directory / "pdf_rag.toml", directory)


def pdf_rag_index_path(name: str) -> Path:
    directory = pdf_rag_dir(name)
    path = _contained_path(directory / "pdf_rag.sqlite", directory)
    # SQLite 会连同数据库一起打开这些文件，恢复期间也是如此。
    for suffix in ("-journal", "-wal", "-shm"):
        _contained_path(path.with_name(path.name + suffix), directory)
    return path


def mineru_cache_dir() -> Path:
    # 共享的云端解析包是运行时状态，而不是 pdf_rag 定义。
    return state_dir() / "mineru"


def validate_name(name: str) -> bool:
    if not name or len(name.encode("utf-8")) > _MAX_NAME_BYTES:
        return False
    return bool(_NAME_RE.fullmatch(name))


@dataclass
class PdfRagItem:
    key: str
    title: str
    added: str


@dataclass
class PdfRag:
    name: str
    created: str
    description: str = ""
    items: list[PdfRagItem] = field(default_factory=list)

    def has_item(self, key: str) -> bool:
        return any(i.key == key for i in self.items)

    def add_item(self, key: str, title: str) -> bool:
        """添加条目。若已存在则返回 False。"""
        if self.has_item(key):
            return False
        self.items.append(
            PdfRagItem(
                key=key,
                title=title,
                added=datetime.now(UTC).isoformat(),
            )
        )
        return True

    def remove_item(self, key: str) -> bool:
        """移除条目。若不存在则返回 False。"""
        before = len(self.items)
        self.items = [i for i in self.items if i.key != key]
        return len(self.items) < before


def _pdf_rag_path(name: str) -> Path:
    return pdf_rag_file_path(name)


def _toml_string(value: str) -> str:
    """将 Python 字符串序列化为 TOML 基本字符串字面量。

    JSON 字符串转义已覆盖 TOML 要求转义的所有字符（引号、反斜杠、控制字符）；非 ASCII 文本
    保持原样。DEL 是 JSON 原样保留而 TOML 要求转义的唯一控制字符。
    """
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007F")


def _manifest_text(ws: PdfRag) -> str:
    lines = [
        f"created = {_toml_string(ws.created)}",
        f"description = {_toml_string(ws.description)}",
        "",
    ]
    for item in ws.items:
        lines.append("[[items]]")
        lines.append(f"key = {_toml_string(item.key)}")
        lines.append(f"title = {_toml_string(item.title)}")
        lines.append(f"added = {_toml_string(item.added)}")
        lines.append("")
    return "\n".join(lines)


def _write_atomic(path: Path, content: str) -> None:
    """通过同目录临时文件与原子替换写入内容。"""
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_pdf_rag(ws: PdfRag) -> None:
    path = _pdf_rag_path(ws.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, _manifest_text(ws))


def _parse_manifest(name: str, text: str) -> PdfRag:
    try:
        data = tomllib.loads(text)
    except Exception as exc:  # tomllib.TOMLDecodeError（ValueError）以及同类异常
        raise PdfRagManifestError(f"invalid TOML manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise PdfRagManifestError("manifest must be a TOML table")
    created = data.get("created", "")
    description = data.get("description", "")
    if not isinstance(created, str):
        raise PdfRagManifestError("manifest field 'created' must be a string")
    if not isinstance(description, str):
        raise PdfRagManifestError("manifest field 'description' must be a string")
    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raise PdfRagManifestError("manifest field 'items' must be a list of tables")
    items: list[PdfRagItem] = []
    for row, raw_item in enumerate(raw_items, 1):
        if not isinstance(raw_item, dict):
            raise PdfRagManifestError(f"items[{row}] must be a table with key, title, and added")
        values: list[str] = []
        for field_name in ("key", "title", "added"):
            value = raw_item.get(field_name)
            if not isinstance(value, str) or not value:
                raise PdfRagManifestError(f"items[{row}].{field_name} must be a non-empty string")
            values.append(value)
        items.append(PdfRagItem(key=values[0], title=values[1], added=values[2]))
    return PdfRag(name=name, created=created, description=description, items=items)


def load_pdf_rag(name: str) -> PdfRag:
    path = _pdf_rag_path(name)
    if not path.exists():
        raise FileNotFoundError(f"PdfRag '{name}' not found")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise
    return _parse_manifest(name, text)


def _list_warning(path: Path, exc: Exception) -> str | None:
    """说明跳过某个 pdf_rag 目录的原因；非 pdf_rag 返回 None。"""
    if path.is_symlink():
        return f"skipping pdf_rag '{path.name}': symbolic links are not allowed"
    manifest = path / "pdf_rag.toml"
    if manifest.is_symlink():
        return f"skipping pdf_rag '{path.name}': manifest is a symbolic link"
    if not manifest.exists():
        return None
    return f"skipping unreadable pdf_rag '{path.name}': {exc}"


def list_pdf_rags() -> list[PdfRag]:
    pdf_rag_dir_path = _pdf_rag_root()
    if not pdf_rag_dir_path.exists():
        return []
    result = []
    warnings: list[str] = []
    for path in sorted(pdf_rag_dir_path.iterdir()):
        if not path.is_dir():
            continue
        try:
            result.append(load_pdf_rag(path.name))
        except Exception as exc:
            warning = _list_warning(path, exc)
            if warning is not None:
                warnings.append(warning)
    for warning in warnings:
        sys.stderr.write(f"Warning: {warning}\n")
    if warnings:
        sys.stderr.flush()
    return result


def delete_pdf_rag(name: str) -> None:
    path = pdf_rag_dir(name)
    if not path.exists():
        raise FileNotFoundError(f"PdfRag '{name}' not found")
    shutil.rmtree(path)


def cleanup_empty_pdf_rag_root() -> None:
    """当不再有 pdf_rag 时删除 pdf_rag 根目录。

    只清理标准的 '.zot/pdf_rag' 根目录；先删除已知的 prune 锁文件，避免它们让根目录
    无法删除。仅为尽力而为。
    """
    root = pdf_rags_dir()
    try:
        if root.name != _PDF_RAG_DIRNAME or root.is_symlink() or not root.is_dir():
            return
    except OSError:
        return
    try:
        leftovers = [entry for entry in root.iterdir() if not _is_prune_lock_file(entry)]
        if leftovers:
            return
        for entry in root.iterdir():
            if _is_prune_lock_file(entry):
                entry.unlink()
        root.rmdir()
    except OSError:
        pass


def _is_prune_lock_file(path: Path) -> bool:
    try:
        return path.is_file() and path.name.endswith(".prune.lock")
    except OSError:
        return False


def pdf_rag_exists(name: str) -> bool:
    return _pdf_rag_path(name).exists()
