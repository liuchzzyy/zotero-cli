"""An idempotency key identifies one request in one library."""

import json
from unittest.mock import patch

from tests.support import invoke_agent_cli

from zotero_cli.core.zotero import idempotency


def test_update_same_key_different_payload_or_library_is_not_cached(tmp_path):
    with (
        patch("zotero_cli.core.zotero.idempotency.state_dir", return_value=tmp_path),
        patch("zotero_cli.commands.items.update.ZoteroWriter") as writer,
    ):
        idempotency.clear()
        common = ["update", "ABC", "--idempotency-key", "same"]
        env = {"ZOT_LIBRARY_ID": "123", "ZOT_API_KEY": "fake"}
        a = invoke_agent_cli(common + ["--title", "first"], env=env)
        b = invoke_agent_cli(common + ["--title", "second"], env=env)
        c = invoke_agent_cli(common + ["--title", "first"], env={**env, "ZOT_LIBRARY_ID": "456"})
        d = invoke_agent_cli(common + ["--title", "first"], env=env)
    assert all(x.exit_code == 0 for x in (a, b, c, d))
    assert writer.return_value.update_item.call_count == 3
    assert json.loads(b.output)["data"]["fields"]["title"] == "second"
    assert json.loads(c.output)["meta"]["request_id"] != json.loads(a.output)["meta"]["request_id"]
    assert json.loads(d.output)["meta"]["request_id"] == json.loads(a.output)["meta"]["request_id"]
