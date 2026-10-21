import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from tests.support import FIXTURES_DIR

from zotero_cli.cli import main


def test_note_read(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["zotero", "note", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    assert "transformer architecture" in result.output


def test_note_read_json(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "zotero", "note", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    data = json.loads(result.output)["data"]
    assert len(data) >= 1


@patch("zotero_cli.cli.commands.zotero.notes.ZoteroWriter")
def test_note_add(mock_writer_cls, test_db_path):
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_writer.add_note.return_value = "NEWNOTE"

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["zotero", "note", "ATTN001", "--add", "New note"],
        env={
            "ZOT_DATA_DIR": str(test_db_path.parent),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "table",
        },
    )
    assert result.exit_code == 0
    mock_writer.add_note.assert_called_once()


def test_note_add_empty_content_is_validation_error(test_db_path):
    runner = CliRunner()
    for content in ("", "   "):
        result = runner.invoke(
            main,
            ["--json", "zotero", "note", "ATTN001", "--add", content],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 3
        assert json.loads(result.output)["error"]["code"] == "validation_error"


def test_note_missing_item_reports_item_not_found(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "zotero", "note", "MISSINGKEY"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 4
    error = json.loads(result.output)["error"]
    assert error["code"] == "not_found"
    assert "Item 'MISSINGKEY' not found" in error["message"]


def test_note_existing_item_without_notes_reports_no_notes():
    reader = MagicMock()
    reader.get_notes.return_value = []
    reader.get_item.return_value = MagicMock()
    runner = CliRunner()
    with patch("zotero_cli.cli.commands.zotero.notes.ZoteroReader", return_value=reader):
        result = runner.invoke(
            main,
            ["--json", "zotero", "note", "ITEM1"],
            env={"ZOT_DATA_DIR": str(FIXTURES_DIR), "ZOT_FORMAT": "table"},
        )
    assert result.exit_code == 4
    error = json.loads(result.output)["error"]
    assert error["code"] == "not_found"
    assert "No notes found for 'ITEM1'" in error["message"]
    reader.close.assert_called_once()


def test_note_dry_run_never_writes():
    runner = CliRunner()
    with patch("zotero_cli.cli.commands.zotero.notes.ZoteroWriter") as writer:
        result = runner.invoke(
            main,
            ["--json", "zotero", "note", "ABC", "--add", "New note", "--dry-run"],
            env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": ""},
        )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    assert payload["data"]["would"]["parent"] == "ABC"
    writer.assert_not_called()


def test_note_unknown_outcome_blocks_retry(tmp_path):
    from zotero_cli.zotero.writer_errors import ZoteroWriteError

    runner = CliRunner()
    with (
        patch("zotero_cli.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.cli.commands.zotero.notes.ZoteroWriter") as writer,
    ):
        writer.return_value.add_note.side_effect = ZoteroWriteError(
            "Connection lost", code="network_error", retryable=True
        )
        args = ["--json", "zotero", "note", "ABC", "--add", "New note", "--idempotency-key", "once"]
        env = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"}
        first = runner.invoke(main, args, env=env)
        retry = runner.invoke(main, args, env=env)
    assert first.exit_code == 5, first.output
    assert json.loads(first.output)["error"]["retryable"] is False
    assert retry.exit_code == 6, retry.output
    assert json.loads(retry.output)["error"]["code"] == "conflict"
    writer.return_value.add_note.assert_called_once()
