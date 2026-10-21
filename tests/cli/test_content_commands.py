import json

from click.testing import CliRunner

from zotero_cli.cli import main


def test_summarize(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["summarize", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    assert "Attention Is All You Need" in result.output
    assert "Vaswani" in result.output


def test_summarize_json(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "summarize", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["meta"]["request_id"]
    data = envelope["data"]
    assert data["title"] == "Attention Is All You Need"


def test_summarize_all_json_envelope(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "summarize-all", "--limit", "2"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0, result.output
    # Exactly one envelope on stdout; structured progress goes to stderr.
    envelope = json.loads(result.stdout)
    assert envelope["ok"] is True
    assert envelope["meta"]["count"] == 2
    assert isinstance(envelope["data"], list)


def test_relate(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["zotero", "relate", "ATTN001"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 0
    assert "BERT002" in result.output


def test_relate_missing_item_is_not_found(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--json", "zotero", "relate", "MISSINGKEY"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    assert result.exit_code == 4
    envelope = json.loads(result.output)
    assert envelope["error"]["code"] == "not_found"


def test_pdf_no_attachment(test_db_path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["pdf", "DEEP003"],
        env={"ZOT_DATA_DIR": str(test_db_path.parent), "ZOT_FORMAT": "table"},
    )
    # Exit 4 (NOT_FOUND) per the agent contract.
    assert result.exit_code == 4
    assert "no pdf" in result.output.lower() or "not found" in result.output.lower()
