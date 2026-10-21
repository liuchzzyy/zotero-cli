"""Exercise the standalone inventory entrypoint in-process and fully offline."""

from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest
from tests.support import FIXTURES_DIR, REPO_ROOT

from zotero_cli import config
from zotero_cli.workspace import store as workspace_store
from zotero_cli.workspace.rag.index import RagIndex
from zotero_cli.workspace.rag.vector_store import QdrantVectorStore
from zotero_cli.workspace.store import (
    Workspace,
    load_workspace,
    save_workspace,
    workspace_file_path,
    workspace_index_path,
)
from zotero_cli.zotero.reader import ZoteroReader


@dataclass
class InventorySandbox:
    module: ModuleType
    root: Path
    db_path: Path
    output: Path
    vector_path: Path
    closed_readers: list[ZoteroReader] = field(default_factory=list)
    name: str = "offline-inventory"


def _build_library(db_path: Path) -> None:
    """Reuse the fixture schema, but seed a small, explicit isolated library."""
    shutil.copy2(FIXTURES_DIR / "zotero.sqlite", db_path)
    with sqlite3.connect(db_path) as conn:
        for table in (
            "itemData",
            "itemDataValues",
            "itemCreators",
            "itemTags",
            "collectionItems",
            "itemNotes",
            "itemAttachments",
            "deletedItems",
            "itemRelations",
            "fulltextItems",
            "items",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.executemany(
            "INSERT INTO items (itemID, itemTypeID, libraryID, key) VALUES (?, ?, 1, ?)",
            [
                (1, 2, "LOCAL001"),
                (2, 2, "LOCAL002"),
                (3, 2, "MISSING1"),
                (4, 2, "NOPDF001"),
                (11, 14, "PDFMAIN1"),
                (12, 14, "PDFSUPP1"),
                (13, 14, "PDFLINK2"),
                (14, 14, "PDFMISS1"),
                (15, 14, "PDFNULL1"),
                (16, 14, "TEXTFILE"),
            ],
        )
        title_field = conn.execute("SELECT fieldID FROM fields WHERE fieldName = 'title'").fetchone()[0]
        for item_id, title in [(1, "Local 研究 paper"), (2, "Linked PDF paper"), (3, "Missing PDFs"), (4, "Text only")]:
            value_id = conn.execute("INSERT INTO itemDataValues (value) VALUES (?)", (title,)).lastrowid
            conn.execute("INSERT INTO itemData VALUES (?, ?, ?)", (item_id, title_field, value_id))
        conn.executemany(
            "INSERT INTO collectionItems (collectionID, itemID) VALUES (?, ?)",
            [(1, 1), (1, 2), (1, 3), (2, 1), (2, 4)],
        )
        conn.executemany(
            "INSERT INTO itemAttachments (itemID, parentItemID, linkMode, contentType, path) VALUES (?, ?, 0, ?, ?)",
            [
                (11, 1, "application/pdf", "storage:main.pdf"),
                (12, 1, "application/pdf", "storage:supp.pdf"),
                (13, 2, "application/pdf", "attachments:linked.pdf"),
                (14, 3, "application/pdf", "storage:missing.pdf"),
                (15, 3, "application/pdf", None),
                (16, 4, "text/plain", "storage:notes.txt"),
            ],
        )
    for key, filename in [("PDFMAIN1", "main.pdf"), ("PDFSUPP1", "supp.pdf"), ("TEXTFILE", "notes.txt")]:
        path = db_path.parent / "storage" / key / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 isolated inventory fixture\n")


@pytest.fixture
def inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InventorySandbox:
    data_dir = tmp_path / "zotero"
    data_dir.mkdir()
    db_path = data_dir / "zotero.sqlite"
    _build_library(db_path)
    linked_dir = tmp_path / "linked"
    linked_dir.mkdir()
    (linked_dir / "linked.pdf").write_bytes(b"%PDF-1.4 linked inventory fixture\n")
    prefs = tmp_path / "prefs.js"
    prefs.write_text(
        'user_pref("extensions.zotero.baseAttachmentPath", ' + json.dumps(str(linked_dir)) + ");\n",
        encoding="utf-8",
    )
    vector_path = tmp_path / "vectors"
    isolated_config = tmp_path / "inventory.toml"
    isolated_config.write_text(
        "[profile.offline]\ndata_dir = "
        + json.dumps(str(data_dir))
        + "\nprefs_js_path = "
        + json.dumps(str(prefs))
        + "\n[vector_store]\npath = "
        + json.dumps(str(vector_path))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config.paths, "config_file_path", lambda: isolated_config)
    monkeypatch.setattr(workspace_store, "workspace_root", lambda: tmp_path / ".workspace")
    monkeypatch.setattr(workspace_store, "state_dir", lambda: tmp_path / ".zot" / "state")
    monkeypatch.setenv("ZOT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ZOT_PROFILE", "")
    monkeypatch.setenv("ZOT_PREFS_JS_PATH", "")

    def reject_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("An inventory test attempted a real network connection")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    script = REPO_ROOT / "tools" / "paperrag" / "workspace_inventory.py"
    spec = importlib.util.spec_from_file_location("offline_workspace_inventory", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = InventorySandbox(module, tmp_path, db_path, tmp_path / "reports" / "inventory.json", vector_path)
    real_close = ZoteroReader.close

    def record_close(reader: ZoteroReader) -> None:
        result.closed_readers.append(reader)
        real_close(reader)

    monkeypatch.setattr(ZoteroReader, "close", record_close)
    return result


def _run(inventory: InventorySandbox, monkeypatch: pytest.MonkeyPatch, *args: str) -> dict[str, Any]:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "workspace_inventory.py",
            "--workspace",
            inventory.name,
            "--profile",
            "offline",
            "--output",
            str(inventory.output),
            *args,
        ],
    )
    inventory.module.main()
    assert inventory.closed_readers
    assert all(reader._conn is None for reader in inventory.closed_readers)
    return json.loads(inventory.output.read_text(encoding="utf-8"))


def _seed_workspace(inventory: InventorySandbox, keys: tuple[str, ...]) -> None:
    workspace = Workspace(name=inventory.name, created="2026-01-01T00:00:00+00:00", description="Preserve me")
    for key in keys:
        workspace.add_item(key, f"Saved {key}")
    save_workspace(workspace)


def _seed_index(inventory: InventorySandbox) -> list[int]:
    index = RagIndex(workspace_index_path(inventory.name))
    try:
        return [
            index.insert_chunk("LOCAL001", "pdf", "First local chunk"),
            index.insert_chunk("LOCAL001", "pdf", "Second local chunk"),
            index.insert_chunk("STALE999", "note", "Existing unrelated workspace content"),
        ]
    finally:
        index.close()


@pytest.mark.parametrize(
    ("option", "value"),
    [("--scan-limit", "0"), ("--scan-limit", "-1"), ("--progress-every", "0"), ("--progress-every", "-5")],
)
def test_inventory_rejects_nonpositive_limits_before_opening_services(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    value: str,
) -> None:
    loader = MagicMock(side_effect=AssertionError("Configuration must not be loaded"))
    monkeypatch.setattr(inventory.module, "load_config", loader)
    with pytest.raises(SystemExit, match=option + " must be greater than 0"):
        _run(inventory, monkeypatch, option, value)
    loader.assert_not_called()
    assert not inventory.output.exists()
    assert not (inventory.root / ".workspace").exists()


@pytest.mark.parametrize("existing", [False, True], ids=["new-workspace", "existing-workspace"])
def test_inventory_dry_run_deduplicates_collections_and_preserves_workspace(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    existing: bool,
) -> None:
    original = None
    if existing:
        _seed_workspace(inventory, ("LOCAL001", "STALE999"))
        original = workspace_file_path(inventory.name).read_bytes()
    before_db = inventory.db_path.read_bytes()
    payload = _run(
        inventory,
        monkeypatch,
        "--collection",
        "Machine Learning",
        "--collection",
        "Transformers",
        "--progress-every",
        "2",
        "--dry-run",
    )
    assert payload["dry_run"] is True
    assert payload["db_path"] == str(inventory.db_path)
    assert payload["collection_item_counts"] == {"Machine Learning": 3, "Transformers": 2}
    assert payload["scanned_items"] == 4
    assert payload["local_pdf_items"] == 2
    assert payload["pdf_but_missing_local_file"] == 1
    assert payload["workspace_created"] is not existing
    assert payload["workspace_existing_items"] == (2 if existing else 0)
    assert payload["workspace_added_items"] == (1 if existing else 2)
    assert payload["pending_index_keys"] == ["LOCAL001", "LOCAL002"]
    assert payload["pending_index_items"] == 2
    assert payload["indexed_items"] == payload["indexed_chunk_count"] == payload["chunks_with_embeddings"] == 0
    assert [row["pdf_count"] for row in payload["items"]] == [2, 1]
    assert payload["items"][0]["title"] == "Local 研究 paper"
    assert payload["items"][0]["pdfs"] == [
        {
            "key": "PDFMAIN1",
            "filename": "main.pdf",
            "path": str(inventory.db_path.parent / "storage/PDFMAIN1/main.pdf"),
        },
        {
            "key": "PDFSUPP1",
            "filename": "supp.pdf",
            "path": str(inventory.db_path.parent / "storage/PDFSUPP1/supp.pdf"),
        },
    ]
    assert payload["items"][1]["pdfs"][0]["path"] == str(inventory.root / "linked/linked.pdf")
    assert inventory.db_path.read_bytes() == before_db
    if existing:
        assert workspace_file_path(inventory.name).read_bytes() == original
    else:
        assert not (inventory.root / ".workspace").exists()
    assert not inventory.vector_path.exists()
    assert not workspace_index_path(inventory.name).exists()
    output = capsys.readouterr().out
    assert "scanned=2/4" in output
    assert "scanned=4/4" in output
    assert "[inventory-summary] local_pdf_items=2" in output
    assert payload["created_at"].endswith("+00:00")


def test_inventory_apply_is_idempotent_and_keeps_unrelated_workspace_members(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _run(inventory, monkeypatch)
    assert first["workspace_created"] is True
    assert first["workspace_added_items"] == 2
    assert first["collections"] == []
    workspace = load_workspace(inventory.name)
    assert [item.key for item in workspace.items] == ["LOCAL001", "LOCAL002"]
    workspace.description = "Keep custom description"
    workspace.add_item("STALE999", "User-added note-only paper")
    save_workspace(workspace)
    original = workspace_file_path(inventory.name).read_bytes()
    second = _run(inventory, monkeypatch)
    assert second["workspace_created"] is False
    assert second["workspace_existing_items"] == 3
    assert second["workspace_added_items"] == 0
    assert workspace_file_path(inventory.name).read_bytes() == original
    assert [item.key for item in load_workspace(inventory.name).items] == ["LOCAL001", "LOCAL002", "STALE999"]
    assert not workspace_index_path(inventory.name).exists()


def test_inventory_scan_limit_is_applied_per_collection_before_deduplication(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _run(
        inventory,
        monkeypatch,
        "--collection",
        "Machine Learning",
        "--collection",
        "Transformers",
        "--scan-limit",
        "1",
        "--dry-run",
    )
    assert payload["collection_item_counts"] == {"Machine Learning": 3, "Transformers": 2}
    assert payload["scanned_items"] == payload["local_pdf_items"] == payload["workspace_added_items"] == 1
    assert payload["pending_index_keys"] == ["LOCAL001"]


def test_inventory_empty_library_writes_zero_summary_without_creating_workspace(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with sqlite3.connect(inventory.db_path) as conn:
        conn.execute("DELETE FROM items")
    payload = _run(inventory, monkeypatch, "--dry-run")
    assert payload["scanned_items"] == payload["local_pdf_items"] == payload["workspace_added_items"] == 0
    assert payload["collection_item_counts"] == {}
    assert payload["items"] == payload["pending_index_keys"] == []
    assert not (inventory.root / ".workspace").exists()
    assert "scanned=" not in capsys.readouterr().out


@pytest.mark.parametrize("vector_count", [1, 5], ids=["partly-embedded", "extra-vectors"])
def test_inventory_reads_real_index_and_local_qdrant_counts_without_reindexing(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
    vector_count: int,
) -> None:
    _seed_workspace(inventory, ("LOCAL001", "STALE999"))
    chunk_ids = _seed_index(inventory)
    vectors = QdrantVectorStore(inventory.vector_path, f"ws_{inventory.name}")
    try:
        vectors.upsert(list(range(1, vector_count + 1)), [[1.0, 0.0]] * vector_count)
    finally:
        vectors.close()
    payload = _run(inventory, monkeypatch, "--dry-run")
    assert payload["indexed_items"] == 2
    assert payload["indexed_chunk_count"] == len(chunk_ids) == 3
    assert payload["chunks_with_embeddings"] == vector_count
    assert payload["chunks_missing_embeddings"] == max(3 - vector_count, 0)
    assert payload["pending_index_keys"] == ["LOCAL002"]
    assert payload["pending_index_items"] == 1
    reopened = QdrantVectorStore(inventory.vector_path, f"ws_{inventory.name}")
    try:
        assert reopened.count() == vector_count
    finally:
        reopened.close()
    index = RagIndex(workspace_index_path(inventory.name))
    try:
        assert index.get_chunk_ids() == chunk_ids
    finally:
        index.close()


def test_inventory_missing_qdrant_collection_is_zero_not_a_failure(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_index(inventory)
    payload = _run(inventory, monkeypatch, "--dry-run")
    assert payload["chunks_with_embeddings"] == 0
    assert payload["chunks_missing_embeddings"] == 3
    assert payload["pending_index_keys"] == ["LOCAL002"]
    assert "warning:" not in capsys.readouterr().out
    reopened = QdrantVectorStore(inventory.vector_path, f"ws_{inventory.name}")
    try:
        assert reopened.count() == 0
    finally:
        reopened.close()


@pytest.mark.parametrize("failure_at", ["open", "count"])
def test_inventory_vector_failure_falls_back_to_zero_and_releases_open_store(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure_at: str,
) -> None:
    _seed_index(inventory)
    vectors = MagicMock()
    constructor = MagicMock(return_value=vectors)
    if failure_at == "open":
        constructor.side_effect = RuntimeError("offline vector store locked")
    else:
        vectors.count.side_effect = RuntimeError("offline vector count unavailable")
    monkeypatch.setattr(inventory.module, "QdrantVectorStore", constructor)
    payload = _run(inventory, monkeypatch, "--dry-run")
    assert payload["indexed_items"] == 2
    assert payload["indexed_chunk_count"] == payload["chunks_missing_embeddings"] == 3
    assert payload["chunks_with_embeddings"] == 0
    assert payload["pending_index_keys"] == ["LOCAL002"]
    constructor.assert_called_once_with(inventory.vector_path, f"ws_{inventory.name}")
    if failure_at == "count":
        vectors.close.assert_called_once_with()
    else:
        vectors.close.assert_not_called()
    assert "unable to count vectors" in capsys.readouterr().out


def test_inventory_unknown_collection_closes_reader_without_partial_output(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        _run(inventory, monkeypatch, "--collection", "not-an-offline-collection")
    assert raised.value.code == 2
    assert len(inventory.closed_readers) == 1
    assert inventory.closed_readers[0]._conn is None
    assert not inventory.output.exists()
    assert not (inventory.root / ".workspace").exists()
    assert "[inventory error] Collection 'not-an-offline-collection' not found" in capsys.readouterr().out


def test_inventory_directory_named_pdf_is_not_a_local_pdf_file(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for relative in ("storage/PDFMAIN1/main.pdf", "storage/PDFSUPP1/supp.pdf"):
        path = inventory.db_path.parent / relative
        path.unlink()
        path.mkdir()
    payload = _run(inventory, monkeypatch, "--dry-run")
    assert payload["local_pdf_items"] == 1
    assert payload["pdf_but_missing_local_file"] == 2
    assert payload["pending_index_keys"] == ["LOCAL002"]
    assert [row["key"] for row in payload["items"]] == ["LOCAL002"]


def test_inventory_missing_database_closes_reader_without_creating_state(
    inventory: InventorySandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory.db_path.unlink()
    with pytest.raises(FileNotFoundError, match="Zotero database not found"):
        _run(inventory, monkeypatch)
    assert len(inventory.closed_readers) == 1
    assert inventory.closed_readers[0]._conn is None
    assert not inventory.output.exists()
    assert not (inventory.root / ".workspace").exists()
