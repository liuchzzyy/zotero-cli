import json
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from tests.support import FIXTURES_DIR

from zotero_cli.cli import main


def test_note_read(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["note", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    assert "transformer architecture" in result.output


def test_note_read_json(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "note", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    data = json.loads(result.output)["data"]
    assert len(data) >= 1


@patch("zotero_cli.commands.notes.note.ZoteroWriter")
def test_note_add(mock_writer_cls, test_db_path):
    mock_writer = MagicMock()
    mock_writer_cls.return_value = mock_writer
    mock_writer.add_note.return_value = "NEWNOTE"

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["note", "ATTN001", "--add", "New note"],
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
            ["--json", "note", "ATTN001", "--add", content],
            env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
        )
        assert result.exit_code == 3
        assert json.loads(result.output)["error"]["code"] == "validation_error"


def test_note_missing_item_reports_item_not_found(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "note", "MISSINGKEY"],
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
    with patch("zotero_cli.commands.notes.note.ZoteroReader", return_value=reader):
        result = runner.invoke(
            main,
            ["--json", "note", "ITEM1"],
            env={"ZOT_DATA_DIR": str(FIXTURES_DIR), "ZOT_FORMAT": "table"},
        )
    assert result.exit_code == 4
    error = json.loads(result.output)["error"]
    assert error["code"] == "not_found"
    assert "No notes found for 'ITEM1'" in error["message"]
    reader.close.assert_called_once()
