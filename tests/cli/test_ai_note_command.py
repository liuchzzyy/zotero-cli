"""`ai-note` 子命令测试：试运行契约、错误映射与提示输出。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from tests.support import invoke_cli

from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.config import AiNoteConfig
from zotero_cli.zotero.idempotency import PendingMutationError
from zotero_cli.zotero.models import Note


def _make_ai_config() -> AiNoteConfig:
    return AiNoteConfig(api_key="test-key", base_url="http://127.0.0.1:9/v1", model="test-model")


def _item():
    from zotero_cli.zotero.models import Item

    return Item(
        key="ITEM1",
        item_type="journalArticle",
        title="Safety test",
        creators=[],
        abstract=None,
        date=None,
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="",
        date_modified="",
    )


class TestAiNoteCLI:
    def _run(self, args, env=None):
        base_env = {
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            # 留空 ZOT_FORMAT 以启用非 TTY 自动判定，非终端下默认输出 JSON。
            "ZOT_FORMAT": "",
        }
        if env:
            base_env.update(env)
        # `[ai_notes]` 来自被 Git 忽略的 `.zot/config.toml`；干净检出时没有
        # 密钥，`ai-note` 会在进入被测行为前就以 3 退出，因此在这里替换加载器。
        with patch(
            "zotero_cli.cli.commands.ai_notes.load_ai_note_config",
            return_value=_make_ai_config(),
        ):
            return invoke_cli(args, env=base_env)

    def test_dry_run_envelope(self):
        result = {
            "status": "dry_run",
            "item_key": "ABC123",
            "item_type": "journalArticle",
            "paper_type": "research_article",
            "template": "research-article",
            "pdf_count": 1,
            "chars": 100,
            "prompt_preview": "preview",
        }
        with (
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", return_value=result),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123", "--dry-run"])

        assert res.exit_code == 0
        data = json.loads(res.output)["data"]
        assert data["status"] == "dry_run"
        assert data["paper_type"] == "research_article"

    def test_not_found_error_exit_code(self):
        with (
            patch(
                "zotero_cli.cli.commands.ai_notes.analyze_item",
                side_effect=NoteAnalysisError("条目 'X' 不存在", code="not_found"),
            ),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "X"])

        assert res.exit_code == 4
        env = json.loads(res.output)
        assert env["error"]["code"] == "not_found"

    def test_dry_run_without_ai_config_skips_ai_client(self):
        expected = {"status": "dry_run", "item_key": "ABC123"}
        with (
            patch("zotero_cli.cli.commands.ai_notes.load_ai_note_config", return_value=AiNoteConfig()),
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", return_value=expected) as analyze,
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient") as client_cls,
        ):
            res = invoke_cli(
                ["ai-note", "ABC123", "--dry-run"],
                json_output=True,
                env={"ZOT_LIBRARY_ID": "", "ZOT_API_KEY": "", "ZOT_FORMAT": ""},
            )
        assert res.exit_code == 0, res.output
        assert json.loads(res.output)["data"] == expected
        assert analyze.call_args.args[2] is None
        assert analyze.call_args.kwargs["max_extracted_chars"] == AiNoteConfig().max_extracted_chars
        client_cls.assert_not_called()

    def test_idempotency_cached_replay_skips_analysis(self):
        cached = {"ok": True, "data": {"status": "ok", "note_key": "NOTE1"}, "meta": {"count": 1}}
        with (
            patch("zotero_cli.cli.commands.ai_notes.begin_mutation", return_value=cached),
            patch("zotero_cli.cli.commands.ai_notes.analyze_item") as analyze,
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123", "--idempotency-key", "k1"])
        assert res.exit_code == 0, res.output
        analyze.assert_not_called()
        assert json.loads(res.output) == cached

    def test_idempotency_success_is_stored(self):
        result = {
            "status": "ok",
            "item_key": "ABC123",
            "note_key": "NOTE1",
            "paper_type": "research_article",
            "sections": 12,
            "short_note": "ok",
            "short_note_error": "",
        }
        with (
            patch("zotero_cli.cli.commands.ai_notes.begin_mutation", return_value=None),
            patch("zotero_cli.cli.commands.ai_notes.store_cached") as store,
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", return_value=result),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123", "--idempotency-key", "k1"])
        assert res.exit_code == 0, res.output
        store.assert_called_once()
        envelope = store.call_args.args[2]
        assert envelope["data"] == result

    def test_idempotency_released_on_pre_write_error(self):
        with (
            patch("zotero_cli.cli.commands.ai_notes.begin_mutation", return_value=None),
            patch("zotero_cli.cli.commands.ai_notes.release_rejected_mutation") as release,
            patch(
                "zotero_cli.cli.commands.ai_notes.analyze_item",
                side_effect=NoteAnalysisError("条目 'X' 不存在", code="not_found"),
            ),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "X", "--idempotency-key", "k1"])
        assert res.exit_code == 4
        release.assert_called_once()

    def test_idempotency_released_for_definite_rejection(self):
        error = NoteAnalysisError("Bad request", code="auth_invalid", retryable=False, committed=False)
        with (
            patch("zotero_cli.cli.commands.ai_notes.begin_mutation", return_value=None),
            patch("zotero_cli.cli.commands.ai_notes.release_rejected_mutation") as release,
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", side_effect=error),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123", "--idempotency-key", "k1"])
        assert res.exit_code == 2
        release.assert_called_once()
        assert json.loads(res.output)["error"]["retryable"] is False

    def test_idempotency_reservation_kept_when_write_may_have_committed(self):
        error = NoteAnalysisError("note 已写入但 tag 失败", code="api_error", committed=True)
        with (
            patch("zotero_cli.cli.commands.ai_notes.begin_mutation", return_value=None),
            patch("zotero_cli.cli.commands.ai_notes.release_rejected_mutation") as release,
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", side_effect=error),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123", "--idempotency-key", "k1"])
        assert res.exit_code == 1
        release.assert_not_called()
        assert json.loads(res.output)["error"]["retryable"] is False

    def test_idempotency_pending_conflict(self):
        with (
            patch(
                "zotero_cli.cli.commands.ai_notes.begin_mutation",
                side_effect=PendingMutationError("pending"),
            ),
            patch("zotero_cli.cli.commands.ai_notes.analyze_item") as analyze,
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123", "--idempotency-key", "k1"])
        assert res.exit_code == 6
        analyze.assert_not_called()

    def test_provider_auth_error_exits_2(self):
        with (
            patch(
                "zotero_cli.cli.commands.ai_notes.analyze_item",
                side_effect=NoteAnalysisError("401 Unauthorized", code="auth_invalid"),
            ),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123"])
        assert res.exit_code == 2
        assert json.loads(res.output)["error"]["code"] == "auth_invalid"

    def test_help_lists_new_surface(self):
        res = invoke_cli(["ai-note", "--help"])
        assert res.exit_code == 0
        assert "--idempotency-key" in res.output
        assert "只输出分类结果" in res.output

    def test_idempotency_key_is_ignored_under_dry_run(self):
        expected = {"status": "dry_run", "item_key": "ABC123"}
        with (
            patch("zotero_cli.cli.commands.ai_notes.begin_mutation") as begin,
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", return_value=expected),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["ai-note", "ABC123", "--dry-run", "--idempotency-key", "k1"])
        assert res.exit_code == 0, res.output
        begin.assert_not_called()

    def test_progress_uses_lines_when_stderr_is_not_a_tty(self):
        def fake_analyze(reader, writer, ai_client, key, **kwargs):
            kwargs["progress"]("analyze", "paper_type=research_article")
            return {"status": "dry_run", "item_key": key}

        with (
            patch("zotero_cli.cli.commands.ai_notes.analyze_item", side_effect=fake_analyze),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroReader"),
            patch("zotero_cli.cli.commands.ai_notes.ZoteroWriter"),
            patch("zotero_cli.cli.commands.ai_notes.AiClient"),
        ):
            res = self._run(["--no-json", "ai-note", "ABC123", "--dry-run"])
        assert res.exit_code == 0, res.output
        assert "\r" not in res.stderr
        assert "[analyze] paper_type=research_article" in res.stderr


def test_short_note_dry_run_uses_real_analysis_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """试运行必须走真实分析流程，且不构造写入器。"""
    reader = MagicMock()
    reader.get_item.return_value = _item()
    reader.get_notes.return_value = [Note("LATEST", "ITEM1", "<p>AI条目分析 - Current evidence.</p>")]
    ai_client = MagicMock()
    writer_constructor = MagicMock(side_effect=AssertionError("dry-run 不得构造写入器"))
    monkeypatch.setattr(
        "zotero_cli.cli.commands.ai_notes.load_ai_note_config",
        lambda: AiNoteConfig(api_key="test-key", base_url="http://127.0.0.1:9/v1", model="test-model"),
    )
    monkeypatch.setattr("zotero_cli.cli.commands.ai_notes.ZoteroReader", lambda *args, **kwargs: reader)
    monkeypatch.setattr("zotero_cli.cli.commands.ai_notes.ZoteroWriter", writer_constructor)
    monkeypatch.setattr("zotero_cli.cli.commands.ai_notes.AiClient", lambda config: ai_client)

    result = invoke_cli(["ai-note", "ITEM1", "--dry-run", "--short-note-only"], json_output=True)

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["data"]["status"] == "dry_run"
    assert envelope["data"]["note_key"] == "LATEST"
    assert envelope["data"]["prompt_preview"]
    ai_client.chat.assert_not_called()
    writer_constructor.assert_not_called()
    reader.close.assert_called_once()
