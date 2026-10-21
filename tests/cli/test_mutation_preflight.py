"""在构造写入器之前完成对写入请求的完整校验。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from click.testing import CliRunner

from zotero_cli import config
from zotero_cli.cli import main
from zotero_cli.zotero.access import idempotency
from zotero_cli.zotero.access.errors import ZoteroWriteError

WRITE_ENV = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "offline-write-key"}


@pytest.fixture(autouse=True)
def isolated_config_and_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.paths, "config_file_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(idempotency, "state_dir", lambda: tmp_path / "state")


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("selector", ["flag", "env"])
def test_unknown_profile_blocks_cli_writer_even_with_legacy_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str, json_output: bool
) -> None:
    (tmp_path / "config.toml").write_text(
        '[zotero]\nlibrary_id = "legacy"\napi_key = "offline-legacy-secret"\n[profile.main]\nlibrary_id = "main"\n',
        encoding="utf-8",
    )
    writer = Mock()
    writer.return_value.add_item.return_value = "ITEM001"
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.items.ZoteroWriter", writer)
    args = ["--json" if json_output else "--no-json"]
    env = WRITE_ENV.copy()
    if selector == "flag":
        args += ["--profile", "typo"]
    else:
        env["ZOT_PROFILE"] = "typo"
    args += ["zotero", "add", "--doi", "10.1000/offline", "--no-resolve"]

    result = CliRunner().invoke(main, args, env=env)

    writer.assert_not_called()
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "typo" in result.output
    assert "offline-legacy-secret" not in result.output


@pytest.mark.parametrize("source", ["--from-file"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("credentials", [False, True], ids=["no-auth", "auth"])
def test_add_unsupported_idempotency_combination_fails_before_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    dry_run: bool,
    json_output: bool,
    credentials: bool,
) -> None:
    path = tmp_path / ("paper.pdf" if source == "--pdf" else "dois.txt")
    path.write_bytes(b"%PDF-1.4 offline" if source == "--pdf" else b"10.1000/offline\n")
    writer = Mock()
    writer.return_value.add_item.return_value = "ITEM001"
    writer.return_value.upload_attachment.return_value = "ATT001"
    resolver = Mock(return_value={"title": "Offline"})
    parser = Mock()
    cache_read = Mock()
    cache_write = Mock()
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.items.ZoteroWriter", writer)
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.items.resolve_doi", resolver)
    monkeypatch.setattr("zotero_cli.ai_pdf.MinerUParseCache", parser)
    monkeypatch.setattr(idempotency, "get_cached", cache_read)
    monkeypatch.setattr(idempotency, "store_cached", cache_write)
    args = ["--json" if json_output else "--no-json", "zotero", "add", source, str(path), "--idempotency-key", "retry"]
    if source == "--pdf":
        args += ["--doi", "10.1000/offline"]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args, env=WRITE_ENV if credentials else {})

    assert result.exit_code == 3, result.output
    writer.assert_not_called()
    resolver.assert_not_called()
    parser.assert_not_called()
    cache_read.assert_not_called()
    cache_write.assert_not_called()
    assert not (tmp_path / "state").exists()
    if json_output:
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "validation_error"
        assert payload["error"]["retryable"] is False
        message = payload["error"]["message"]
    else:
        message = result.output
    assert "--idempotency-key" in message
    assert source in message


@pytest.mark.parametrize("source", ["--doi", "--url"])
def test_add_single_input_idempotent_retries_remain_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    writer = Mock()
    writer.return_value.add_item.return_value = "ITEM001"
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.items.ZoteroWriter", writer)
    value = "10.1000/offline" if source == "--doi" else "https://example.invalid/paper"
    args = ["--json", "zotero", "add", source, value, "--no-resolve", "--idempotency-key", "retry"]
    runner = CliRunner()

    first = runner.invoke(main, args, env=WRITE_ENV)
    retried = runner.invoke(main, args, env=WRITE_ENV)

    assert first.exit_code == retried.exit_code == 0, first.output + retried.output
    writer.assert_called_once()
    writer.return_value.add_item.assert_called_once_with(
        doi=value if source == "--doi" else None,
        url=value if source == "--url" else None,
        extra_fields=None,
    )
    assert json.loads(retried.output) == json.loads(first.output)
    assert (tmp_path / "state" / "idempotency.db").is_file()


@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
@pytest.mark.parametrize("invalid_name", [None, "", "  ", 7, [], {}])
def test_reorganize_invalid_second_row_is_rejected_before_first_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool, invalid_name: object
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"collections": [{"name": "Valid", "items": ["K1"]}, {"name": invalid_name, "items": ["K2"]}]})
    )
    writer = Mock()
    writer.return_value.create_collection.return_value = "COL1"
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)
    args = ["--json", "zotero", "collection", "reorganize", str(plan)]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args, env=WRITE_ENV)

    writer.assert_not_called()
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"
    assert "name" in payload["error"]["message"]


@pytest.mark.parametrize(
    "plan_value",
    [
        None,
        [],
        "not-an-object",
        {"collections": None},
        {"collections": {}},
        {"collections": "not-a-list"},
        {"collections": [{"name": "Valid"}, None]},
        {"collections": [{"name": "Valid"}, []]},
        {"collections": [{"name": "Valid"}, "not-a-row"]},
    ],
    ids=[
        "null-plan",
        "array-plan",
        "string-plan",
        "null-rows",
        "object-rows",
        "string-rows",
        "null-row",
        "array-row",
        "string-row",
    ],
)
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
def test_reorganize_invalid_json_types_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_value: object, dry_run: bool
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(plan_value))
    writer = Mock()
    writer.return_value.create_collection.return_value = "COL1"
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)
    args = ["--json", "zotero", "collection", "reorganize", str(plan)]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args, env=WRITE_ENV)

    writer.assert_not_called()
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "items",
    [
        None,
        "K2",
        {},
        7,
        ["K2", None],
        ["K2", 7],
        ["K2", {}],
        ["K2", []],
        ["K2", ""],
        ["K2", "  "],
        ["K2", " K3 "],
        ["K2", "BAD KEY"],
        ["K2", "BAD\nKEY"],
        ["K2", "BAD/KEY"],
    ],
    ids=[
        "null",
        "string",
        "object",
        "number",
        "null-key",
        "number-key",
        "object-key",
        "list-key",
        "empty-key",
        "blank-key",
        "padded-key",
        "spaced-key",
        "multiline-key",
        "path-key",
    ],
)
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
def test_reorganize_invalid_item_moves_fail_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, items: object, dry_run: bool
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"collections": [{"name": "Valid", "items": ["K1"]}, {"name": "Second", "items": items}]})
    )
    writer = Mock()
    writer.return_value.create_collection.return_value = "COL1"
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)
    args = ["--json", "zotero", "collection", "reorganize", str(plan)]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args, env=WRITE_ENV)

    writer.assert_not_called()
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "validation_error"
    assert "items" in payload["error"]["message"]


@pytest.mark.parametrize(
    "collections",
    [
        [{"name": "Valid"}, {"name": "Child", "parent": "missing"}],
        [{"name": "Valid"}, {"name": "Child", "parent": 7}],
        [{"name": "Valid"}, {"name": "Child", "parent": False}],
        [{"name": "Valid"}, {"name": "Child", "parent": []}],
        [{"name": "Valid"}, {"name": "Child", "parent": {}}],
        [{"name": "Valid"}, {"name": "Child", "parent": ""}],
        [{"name": "Valid"}, {"name": "Child", "parent": "  "}],
        [{"name": "Same"}, {"name": "Same"}],
        [{"name": "Root"}, {"name": "Same"}, {"name": "Same", "parent": "Root"}],
        [{"name": "Valid"}, {"name": "A", "parent": "A"}],
        [{"name": "Valid"}, {"name": "A", "parent": "B"}, {"name": "B", "parent": "A"}],
        [{"name": "Valid"}, {"name": "A", "parent": "B"}, {"name": "B", "parent": "C"}, {"name": "C", "parent": "A"}],
    ],
    ids=[
        "unknown-parent",
        "number-parent",
        "bool-parent",
        "list-parent",
        "object-parent",
        "empty-parent",
        "blank-parent",
        "duplicate",
        "duplicate-under-different-parents",
        "self-cycle",
        "two-cycle",
        "three-cycle",
    ],
)
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
def test_reorganize_invalid_hierarchy_is_rejected_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collections: list[dict], dry_run: bool
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"collections": collections}))
    writer = Mock()
    writer.return_value.create_collection.return_value = "COL1"
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)
    args = ["--json", "zotero", "collection", "reorganize", str(plan)]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args, env=WRITE_ENV)

    writer.assert_not_called()
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "validation_error"


def test_add_pdf_idempotency_preview_is_side_effect_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4 offline")
    writer = Mock()
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.items.ZoteroWriter", writer)
    result = CliRunner().invoke(
        main,
        [
            "--json",
            "zotero",
            "add",
            "--pdf",
            str(path),
            "--doi",
            "10.1000/offline",
            "--idempotency-key",
            "retry",
            "--dry-run",
        ],
        env={},
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["data"]["would"]["source"] == "pdf"
    writer.assert_not_called()


def test_add_pdf_idempotency_replays_cached_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4 offline")
    writer = Mock()
    cached = {"ok": True, "data": {"key": "CACHED1"}, "meta": {}}
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.items.ZoteroWriter", writer)
    monkeypatch.setattr(idempotency, "begin_mutation", Mock(return_value=cached))
    result = CliRunner().invoke(
        main,
        ["--json", "zotero", "add", "--pdf", str(path), "--doi", "10.1000/offline", "--idempotency-key", "retry"],
        env=WRITE_ENV,
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["key"] == "CACHED1"
    writer.assert_not_called()


def test_reorganize_forward_parent_reference_does_not_become_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"collections": [{"name": "Child", "parent": "Root", "items": ["K1"]}, {"name": "Root"}]})
    )
    writer = Mock()
    writer.return_value.create_collection.side_effect = ["ROOTKEY", "CHILDKEY"]
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)

    result = CliRunner().invoke(main, ["--json", "zotero", "collection", "reorganize", str(plan)], env=WRITE_ENV)

    assert result.exit_code == 0, result.output
    assert writer.return_value.create_collection.call_args_list == [
        call("Root", parent_key=None),
        call("Child", parent_key="ROOTKEY"),
    ]
    writer.return_value.set_item_collections.assert_called_once_with("K1", ["CHILDKEY"])
    rows = json.loads(result.output)["data"]["created_collections"]
    assert rows[1]["parent_key"] == "ROOTKEY"


@pytest.mark.parametrize("json_output", [True, False], ids=["json", "text"])
def test_reorganize_failed_parent_never_creates_or_moves_orphaned_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, json_output: bool
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "collections": [
                    {"name": "Root"},
                    {"name": "Child", "parent": "Root", "items": ["K1"]},
                    {"name": "Grandchild", "parent": "Child", "items": ["K2"]},
                    {"name": "Independent", "items": ["K3"]},
                ]
            }
        )
    )
    writer = Mock()
    writer.return_value.create_collection.side_effect = [ZoteroWriteError("parent failed"), "OTHERKEY"]
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)

    result = CliRunner().invoke(
        main, ["--json" if json_output else "--no-json", "zotero", "collection", "reorganize", str(plan)], env=WRITE_ENV
    )

    assert result.exit_code != 0
    assert writer.return_value.create_collection.call_args_list == [
        call("Root", parent_key=None),
        call("Independent", parent_key=None),
    ]
    writer.return_value.set_item_collections.assert_called_once_with("K3", ["OTHERKEY"])
    if json_output:
        payload = json.loads(result.output)
        assert payload["ok"] == "partial"
        assert payload["data"]["summary"] == {
            "collections_requested": 4,
            "collections_created": 1,
            "items_moved": 1,
            "failures": 3,
        }
        assert [row["name"] for row in payload["data"]["failed"]] == ["Root", "Child", "Grandchild"]
    else:
        assert "Child" in result.output
        assert "Grandchild" in result.output


@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
def test_reorganize_legacy_valid_plan_preserves_names_and_parent_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, json_output: bool, dry_run: bool
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "collections": [
                    {"name": "主题 A", "parent": None, "items": ["K1", "K2"]},
                    {"name": "Sub B", "parent": "主题 A", "items": ["K3"]},
                    {"name": "Empty"},
                ]
            }
        )
    )
    writer = Mock()
    writer.return_value.create_collection.side_effect = ["COLA", "COLB", "COLC"]
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)
    args = ["--json" if json_output else "--no-json", "zotero", "collection", "reorganize", str(plan)]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args, env={} if dry_run else WRITE_ENV)

    assert result.exit_code == 0, result.output
    if dry_run:
        writer.assert_not_called()
    else:
        assert writer.return_value.create_collection.call_args_list == [
            call("主题 A", parent_key=None),
            call("Sub B", parent_key="COLA"),
            call("Empty", parent_key=None),
        ]
        assert writer.return_value.set_item_collections.call_args_list == [
            call("K1", ["COLA"]),
            call("K2", ["COLA"]),
            call("K3", ["COLB"]),
        ]
    if json_output:
        payload = json.loads(result.output)
        assert payload["ok"] is True
        if dry_run:
            assert payload["dry_run"] is True
            assert payload["data"]["total_items"] == 3
            assert [row["name"] for row in payload["data"]["would_create_collections"]] == ["主题 A", "Sub B", "Empty"]
        else:
            assert payload["data"]["summary"] == {
                "collections_requested": 3,
                "collections_created": 3,
                "items_moved": 3,
                "failures": 0,
            }
    else:
        assert "主题 A" in result.output


@pytest.mark.parametrize("plan_value", [{}, {"collections": []}])
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
def test_reorganize_empty_plan_remains_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plan_value: dict, dry_run: bool
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(plan_value))
    writer = Mock()
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)
    args = ["--json", "zotero", "collection", "reorganize", str(plan)]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args)

    writer.assert_not_called()
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["collections"] == []


@pytest.mark.parametrize("contents", ["{not JSON", '{"collections": ['])
@pytest.mark.parametrize("dry_run", [False, True], ids=["apply", "preview"])
def test_reorganize_malformed_json_is_a_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contents: str, dry_run: bool
) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text(contents)
    writer = Mock()
    monkeypatch.setattr("zotero_cli.cli.commands.zotero.collections.ZoteroWriter", writer)
    args = ["--json", "zotero", "collection", "reorganize", str(plan)]
    if dry_run:
        args.append("--dry-run")

    result = CliRunner().invoke(main, args, env=WRITE_ENV)

    writer.assert_not_called()
    assert result.exit_code == 3, result.output
    assert json.loads(result.output)["error"]["code"] == "validation_error"
