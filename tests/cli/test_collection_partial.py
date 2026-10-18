import json
from unittest.mock import patch

from click.testing import CliRunner
from tests.support import FIXTURES_DIR

from zotero_cli.cli import main
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.core.zotero.writer_errors import ZoteroWriteError


def test_reorganize_partial_failure_is_not_success(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"collections": [{"name": "A", "items": ["GOOD", "BAD"]}]}))
    with patch("zotero_cli.commands.collections.reorganize.ZoteroWriter") as cls:
        cls.return_value.create_collection.return_value = "COL1"
        # True move replaces collection membership in one write; one item
        # fails and must surface as a typed partial result.

        def set_collections(item_key, _keys):
            if item_key == "BAD":
                raise ZoteroWriteError("bad key", code="not_found")

        cls.return_value.set_item_collections.side_effect = set_collections
        result = CliRunner().invoke(
            main,
            ["--json", "collection", "reorganize", str(plan)],
            env={"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"},
        )
    payload = json.loads(result.output)
    assert result.exit_code == 4
    assert payload["ok"] == "partial"
    assert payload["data"]["summary"]["failures"] == 1
    assert payload["data"]["failed"][0]["item_key"] == "BAD"
    assert payload["data"]["failed"][0]["code"] == "not_found"
    moved = [row for row in payload["data"]["items_moved"] if row["item_key"] == "GOOD"]
    assert moved and moved[0]["replaced_existing_memberships"] is True


def _collection_key() -> str:
    from tests.support import invoke_cli as invoke

    listed = invoke(["collection", "list"], json_output=True)
    payload = json.loads(listed.output)
    assert payload["ok"] is True
    return payload["data"][0]["key"]


def _run_json(args: list[str], tmp_path=None):
    from tests.support import invoke_cli as invoke

    return invoke(args, json_output=True)


def test_reorganize_empty_plan_dry_run_includes_marker(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"collections": []}))
    result = CliRunner().invoke(main, ["--json", "collection", "reorganize", str(plan), "--dry-run"], env={})
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["data"]["collections"] == []


def test_reorganize_valid_plan_dry_run_makes_no_api_call(tmp_path, monkeypatch):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"collections": [{"name": "Parent"}, {"name": "Child", "parent": "Parent"}]}))

    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not construct a writer")

    monkeypatch.setattr("zotero_cli.commands.collections.reorganize.ZoteroWriter", fail)
    result = _run_json(["collection", "reorganize", str(plan), "--dry-run"], tmp_path)
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["dry_run"] is True
    assert payload["data"]["total_collections"] == 2


def test_collection_delete_dry_run_previews_nested_cascade():
    result = _run_json(["collection", "delete", "NOSUCHKEY", "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["data"]["would_delete"] is True
    assert payload["data"]["descendant_collections"] == []
    assert payload["data"]["descendant_count"] == 0


def test_collection_delete_dry_run_accepts_known_key():
    key = _collection_key()
    result = _run_json(["collection", "delete", key, "--dry-run"])
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["dry_run"] is True
    assert payload["data"]["would_delete"] is True


def test_collection_delete_without_yes_requires_confirmation():
    key = _collection_key()
    result = _run_json(["collection", "delete", key])
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "confirmation_required"


def test_collection_create_move_rename_dry_run_makes_no_api_call(monkeypatch):
    def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("dry-run must not construct a writer")

    for module in ("zotero_cli.commands.collections.commands.ZoteroWriter",):
        monkeypatch.setattr(module, fail)
    cases = [
        (["collection", "create", "New Col", "--parent", "PARENT"], "would_create"),
        (["collection", "move", "ITEM1", "DESTCOL", "--from", "SRCCOL"], "would_change"),
        (["collection", "rename", "COL1", "New Name"], "would_rename"),
    ]
    for args, marker in cases:
        result = _run_json([*args, "--dry-run"])
        payload = json.loads(result.output)
        assert result.exit_code == 0, result.output
        assert payload["dry_run"] is True
        assert payload["data"][marker] is True


def test_collection_items_unknown_key_is_not_found():
    result = _run_json(["collection", "items", "NOSUCHKEY"])
    assert result.exit_code == 4, result.output
    assert json.loads(result.output)["error"]["code"] == "not_found"


def test_collection_items_known_key_returns_envelope():
    key = _collection_key()
    result = _run_json(["collection", "items", key])
    payload = json.loads(result.output)
    assert result.exit_code == 0, result.output
    assert payload["ok"] is True
    assert isinstance(payload["data"], list)


def test_collection_delete_dry_run_surfaces_nested_cascade_from_local_tree() -> None:
    reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
    try:
        collections = reader.get_collections()
    finally:
        reader.close()
    parent = next((collection for collection in collections if collection.children), None)
    assert parent is not None, "fixture must contain a nested collection"
    result = _run_json(["collection", "delete", parent.key, "--dry-run"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["data"]["would_delete"] is True
    assert (
        payload["data"]["descendant_count"] == len([child for child in parent.children])
        or payload["data"]["descendant_count"] >= 1
    )
    descendant_keys = {row["key"] for row in payload["data"]["descendant_collections"]}
    assert parent.children[0].key in descendant_keys


def test_collection_delete_confirmation_mentions_nested_cascade() -> None:
    reader = ZoteroReader(FIXTURES_DIR / "zotero.sqlite")
    try:
        collections = reader.get_collections()
    finally:
        reader.close()
    parent = next(collection for collection in collections if collection.children)
    result = _run_json(["collection", "delete", parent.key])
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "confirmation_required"
    assert "nested" in payload["error"]["message"]


def test_collection_create_typed_error_is_not_runtime_error() -> None:
    with (
        patch(
            "zotero_cli.commands.collections.commands.resolve_write_credentials",
            return_value=("123", "fake"),
        ),
        patch("zotero_cli.commands.collections.commands.ZoteroWriter") as writer_cls,
    ):
        writer_cls.return_value.create_collection.side_effect = ZoteroWriteError("collection missing", code="not_found")
        result = _run_json(["collection", "create", "New Col"])
    assert result.exit_code == 4, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "not_found"
