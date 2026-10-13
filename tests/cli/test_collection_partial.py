import json
from unittest.mock import patch

from click.testing import CliRunner

from zotero_cli.cli import main
from zotero_cli.core.writer import ZoteroWriteError


def test_reorganize_partial_failure_is_not_success(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"collections": [{"name": "A", "items": ["GOOD", "BAD"]}]}))
    with patch("zotero_cli.commands.collection.ZoteroWriter") as cls:
        cls.return_value.create_collection.return_value = "COL1"
        cls.return_value.move_to_collection.side_effect = [None, ZoteroWriteError("bad key")]
        result = CliRunner().invoke(main, ["--json", "collection", "reorganize", str(plan)],
                                    env={"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"})
    payload = json.loads(result.output)
    assert result.exit_code != 0
    assert payload["ok"] is False
    assert payload["data"]["summary"]["failures"] == 1
    assert payload["data"]["failed"][0]["item_key"] == "BAD"
