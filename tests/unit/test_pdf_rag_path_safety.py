"""工作区路径解析必须始终留在隔离的临时根目录之内。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from zotero_cli.pdf_rag import store as pdf_rag
from zotero_cli.pdf_rag.index import RagIndex

_MANIFEST = 'created = "2026-01-01"\ndescription = "outside sentinel"\n'
_OPERATIONS = ("directory", "manifest", "index", "load", "exists", "save", "delete")


@pytest.fixture
def pdf_rag_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把工作区根指向临时目录，避免实现失误波及真实状态。"""
    assert tmp_path.is_absolute()
    root = tmp_path / ".zot" / "pdf_rag"
    root.mkdir(parents=True)
    monkeypatch.setattr(pdf_rag, "pdf_rags_dir", lambda: root)
    return root


def _operate(operation: str, name: str) -> object:
    if operation == "save":
        return pdf_rag.save_pdf_rag(pdf_rag.PdfRag(name=name, created="2026-01-01"))
    functions = {
        "directory": pdf_rag.pdf_rag_dir,
        "manifest": pdf_rag.pdf_rag_file_path,
        "index": pdf_rag.pdf_rag_index_path,
        "load": pdf_rag.load_pdf_rag,
        "exists": pdf_rag.pdf_rag_exists,
        "delete": pdf_rag.delete_pdf_rag,
    }
    return functions[operation](name)


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize(
    "name",
    (
        "",
        ".",
        "..",
        "../victim",
        "..\\victim",
        "nested/victim",
        "nested\\victim",
        "./victim",
        "victim/",
        "C:\\victim",
        "C:victim",
        "bad\x00name",
    ),
)
def test_unsafe_names_are_rejected_at_every_path_entry(pdf_rag_root: Path, operation: str, name: str) -> None:
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(pdf_rag.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, name)
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()


@pytest.mark.parametrize("operation", _OPERATIONS)
def test_absolute_name_cannot_access_an_outside_pdf_rag(pdf_rag_root: Path, operation: str) -> None:
    victim = pdf_rag_root.parent / "victim"
    victim.mkdir()
    manifest = victim / "pdf_rag.toml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(pdf_rag.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, str(victim))
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    assert manifest.read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("name", ("has spaces", "00_收件箱", "材料 研究-2026", "llm-safety"))
def test_existing_safe_names_keep_their_exact_spelling(pdf_rag_root: Path, name: str) -> None:
    ws = pdf_rag.PdfRag(name=name, created="2026-01-01", description="preserved")
    pdf_rag.save_pdf_rag(ws)
    assert pdf_rag.pdf_rag_dir(name) == pdf_rag_root / name
    assert pdf_rag.pdf_rag_file_path(name) == pdf_rag_root / name / "pdf_rag.toml"
    assert pdf_rag.pdf_rag_index_path(name) == pdf_rag_root / name / "pdf_rag.sqlite"
    idx = RagIndex(pdf_rag.pdf_rag_index_path(name))
    try:
        idx.insert_chunk("TESTKEY", "metadata", "path safety sentinel", 3)
        assert idx.search_bm25("sentinel")
    finally:
        idx.close()
    assert pdf_rag.pdf_rag_exists(name)
    assert pdf_rag.load_pdf_rag(name) == ws
    assert [entry.name for entry in pdf_rag.list_pdf_rags()] == [name]
    with patch.object(pdf_rag.shutil, "rmtree") as delete:
        pdf_rag.delete_pdf_rag(name)
        delete.assert_called_once_with(pdf_rag_root / name)


@pytest.mark.parametrize("operation", _OPERATIONS)
@pytest.mark.parametrize("target_kind", ("outside", "inside", "dangling", "loop"))
def test_pdf_rag_directory_symlinks_are_rejected(pdf_rag_root: Path, operation: str, target_kind: str) -> None:
    link = pdf_rag_root / "linked"
    if target_kind == "outside":
        target = pdf_rag_root.parent / "victim"
    elif target_kind == "inside":
        target = pdf_rag_root / "actual"
    elif target_kind == "dangling":
        target = pdf_rag_root.parent / "missing"
    else:
        target = link
    if target_kind in {"inside", "outside"}:
        target.mkdir()
        (target / "pdf_rag.toml").write_text(_MANIFEST, encoding="utf-8")
    link.symlink_to(target, target_is_directory=True)
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(pdf_rag.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, "linked")
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    if target_kind in {"inside", "outside"}:
        assert (target / "pdf_rag.toml").read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("operation", (*_OPERATIONS, "list"))
@pytest.mark.parametrize("dangling", (False, True))
def test_symlinked_pdf_rag_root_is_rejected(
    pdf_rag_root: Path, monkeypatch: pytest.MonkeyPatch, operation: str, dangling: bool
) -> None:
    victim = pdf_rag_root.parent / "victim"
    if not dangling:
        (victim / "safe").mkdir(parents=True)
        (victim / "safe" / "pdf_rag.toml").write_text(_MANIFEST, encoding="utf-8")
    link = pdf_rag_root.parent / "linked-root"
    link.symlink_to(victim, target_is_directory=True)
    monkeypatch.setattr(pdf_rag, "pdf_rags_dir", lambda: link)
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(pdf_rag.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            pdf_rag.list_pdf_rags() if operation == "list" else _operate(operation, "safe")
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    if not dangling:
        assert (victim / "safe" / "pdf_rag.toml").read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("operation", ("manifest", "load", "exists", "save"))
@pytest.mark.parametrize("target_kind", ("outside", "inside", "dangling", "loop"))
def test_manifest_symlinks_are_rejected(pdf_rag_root: Path, operation: str, target_kind: str) -> None:
    directory = pdf_rag_root / "safe"
    directory.mkdir()
    manifest = directory / "pdf_rag.toml"
    target = directory / "actual.toml" if target_kind == "inside" else pdf_rag_root.parent / "victim.toml"
    if target_kind in {"inside", "outside"}:
        target.write_text(_MANIFEST, encoding="utf-8")
    elif target_kind == "loop":
        target = manifest
    manifest.symlink_to(target)
    with (
        patch.object(Path, "read_text", return_value=_MANIFEST) as read,
        patch.object(Path, "write_text") as write,
        patch.object(pdf_rag.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, "safe")
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()
    if target_kind in {"inside", "outside"}:
        assert target.read_text(encoding="utf-8") == _MANIFEST


@pytest.mark.parametrize("suffix", ("", "-journal", "-wal", "-shm"))
@pytest.mark.parametrize("dangling", (False, True))
def test_index_and_sqlite_sidecar_symlinks_are_rejected(pdf_rag_root: Path, suffix: str, dangling: bool) -> None:
    directory = pdf_rag_root / "safe"
    directory.mkdir()
    victim = pdf_rag_root.parent / "victim.sqlite"
    if not dangling:
        victim.write_bytes(b"outside SQLite sentinel")
    (directory / f"pdf_rag.sqlite{suffix}").symlink_to(victim)
    with pytest.raises(ValueError):
        pdf_rag.pdf_rag_index_path("safe")
    if not dangling:
        assert victim.read_bytes() == b"outside SQLite sentinel"


def test_listing_skips_symlinks_without_reading_outside_the_root(pdf_rag_root: Path) -> None:
    pdf_rag.save_pdf_rag(pdf_rag.PdfRag(name="safe", created="2026-01-01"))
    victim = pdf_rag_root.parent / "victim"
    victim.mkdir()
    (victim / "pdf_rag.toml").write_text(_MANIFEST, encoding="utf-8")
    (pdf_rag_root / "linked").symlink_to(victim, target_is_directory=True)
    (pdf_rag_root / "safe" / "unrelated").symlink_to(victim, target_is_directory=True)
    real_read = Path.read_text
    reads = []

    def read_contained(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        assert path.resolve().is_relative_to(pdf_rag_root.resolve()), f"Outside read: {path}"
        reads.append(path)
        return real_read(path, encoding=encoding, errors=errors)

    with patch.object(Path, "read_text", autospec=True, side_effect=read_contained):
        assert [entry.name for entry in pdf_rag.list_pdf_rags()] == ["safe"]
    assert reads == [pdf_rag_root / "safe" / "pdf_rag.toml"]


@pytest.mark.parametrize("operation", ("load", "exists", "save", "index", "delete"))
def test_real_outside_manifest_is_unchanged_by_rejected_traversal(pdf_rag_root: Path, operation: str) -> None:
    victim = pdf_rag_root.parent / "victim"
    victim.mkdir()
    manifest = victim / "pdf_rag.toml"
    manifest.write_text(_MANIFEST, encoding="utf-8")
    real_read = Path.read_text
    real_write = Path.write_text
    reads = []
    writes = []

    def read_contained(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        assert path.resolve().is_relative_to(pdf_rag_root.resolve()), f"Outside read: {path}"
        reads.append(path)
        return real_read(path, encoding=encoding, errors=errors)

    def write_contained(path: Path, data: str, encoding: str | None = None, errors: str | None = None) -> int:
        assert path.resolve().is_relative_to(pdf_rag_root.resolve()), f"Outside write: {path}"
        writes.append(path)
        return real_write(path, data, encoding=encoding, errors=errors)

    with (
        patch.object(Path, "read_text", autospec=True, side_effect=read_contained),
        patch.object(Path, "write_text", autospec=True, side_effect=write_contained),
        patch.object(pdf_rag.shutil, "rmtree") as delete,
    ):
        with pytest.raises(ValueError):
            _operate(operation, "../victim")
        assert not reads
        assert not writes
        delete.assert_not_called()
    assert manifest.read_text(encoding="utf-8") == _MANIFEST
    assert not (victim / "pdf_rag.sqlite").exists()


@pytest.mark.parametrize("operation", ("directory", "manifest", "index", "load", "exists", "save", "list"))
def test_paths_resolve_safely_before_pdf_rag_root_exists(
    pdf_rag_root: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    missing_root = pdf_rag_root.parent / "new-root"
    monkeypatch.setattr(pdf_rag, "pdf_rags_dir", lambda: missing_root)
    if operation == "list":
        assert pdf_rag.list_pdf_rags() == []
    elif operation == "load":
        with pytest.raises(FileNotFoundError):
            pdf_rag.load_pdf_rag("safe")
    elif operation == "exists":
        assert not pdf_rag.pdf_rag_exists("safe")
    else:
        result = _operate(operation, "safe")
        if operation == "save":
            assert pdf_rag.load_pdf_rag("safe").name == "safe"
        else:
            assert isinstance(result, Path)
            assert result.is_relative_to(missing_root)
            assert not result.exists()


def test_parent_alias_is_canonicalized_without_changing_pdf_rag_names(
    pdf_rag_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias = pdf_rag_root.parent / "project-alias"
    alias.symlink_to(pdf_rag_root.parent, target_is_directory=True)
    logical_root = alias / pdf_rag_root.name
    monkeypatch.setattr(pdf_rag, "pdf_rags_dir", lambda: logical_root)
    ws = pdf_rag.PdfRag(name="研究 topic-1", created="2026-01-01")
    pdf_rag.save_pdf_rag(ws)
    assert pdf_rag.pdf_rag_dir(ws.name) == logical_root / ws.name
    assert pdf_rag.load_pdf_rag(ws.name) == ws
    assert [entry.name for entry in pdf_rag.list_pdf_rags()] == [ws.name]
    assert (pdf_rag_root / ws.name / "pdf_rag.toml").is_file()


@pytest.mark.parametrize("target", ("self", "sibling"))
def test_resolved_path_cannot_equal_or_leave_pdf_rag_root(pdf_rag_root: Path, target: str) -> None:
    escaped = pdf_rag_root if target == "self" else pdf_rag_root.with_name(pdf_rag_root.name + "-victim")
    child = pdf_rag_root / "safe"
    real_resolve = Path.resolve

    def resolve(path: Path, *args: object, **kwargs: object) -> Path:
        return escaped if path == child else real_resolve(path)

    with patch.object(Path, "resolve", autospec=True, side_effect=resolve):
        with pytest.raises(ValueError, match="inside"):
            pdf_rag.pdf_rag_dir("safe")


@pytest.mark.parametrize("error", (OSError("unresolvable path"), RuntimeError("symlink loop")))
def test_unresolvable_paths_fail_closed(pdf_rag_root: Path, error: Exception) -> None:
    with patch.object(Path, "resolve", side_effect=error):
        with pytest.raises(ValueError, match="unable to resolve") as caught:
            pdf_rag.pdf_rag_dir("safe")
    assert caught.value.__cause__ is error
