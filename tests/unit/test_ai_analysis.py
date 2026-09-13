"""Tests for the AI note analysis pipeline and CLI command."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from tests.support import FIXTURES_DIR

from zotero_cli.cli import main
from zotero_cli.config import AiNoteConfig
from zotero_cli.core.ai_client import AiClient
from zotero_cli.core.note_analysis import (
    ANALYZED_TAG,
    KEYWORDS_TAG,
    NO_KEYWORDS_TAG,
    NOT_ANALYZED_TAG,
    NoteAnalysisError,
    analyze_item,
    extract_json_object,
    validate_short_note,
)
from zotero_cli.core.writer import merge_short_note_into_extra
from zotero_cli.models import Attachment, Creator, Item, Note


def _make_item(item_type="journalArticle", tags=None, abstract="摘要内容") -> Item:
    return Item(
        key="ABC123",
        item_type=item_type,
        title="Test Paper",
        creators=[Creator("Alice", "Smith", "author")],
        abstract=abstract,
        date="2024",
        url=None,
        doi="10.1234/test",
        tags=tags or [],
        collections=[],
        date_added="2024-01-01",
        date_modified="2024-01-01",
        extra={"publicationTitle": "Nature"},
    )


def _make_attachment(tmp_path) -> Attachment:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    return Attachment(
        key="ATT1",
        parent_key="ABC123",
        filename="paper.pdf",
        content_type="application/pdf",
        path=pdf,
    )


def _make_ai_config() -> AiNoteConfig:
    return AiNoteConfig(api_key="k", base_url="http://x", model="m", max_extracted_chars=10000)


def _sections_json(n: int = 12) -> str:
    return json.dumps(
        {"sections": [{"type": "paragraph", "text": f"内容 {i}"} for i in range(n)]},
        ensure_ascii=False,
    )


def _short_note_json() -> str:
    return '{"short_note": "体系A | 机制B | 性能C | 疑问：未解问题"}'


class TestExtractJsonObject:
    def test_fenced_json(self):
        fence = chr(96) * 3
        text = fence + "json\n" + '{"sections": []}' + "\n" + fence
        assert extract_json_object(text) == {"sections": []}

    def test_bare_json(self):
        assert extract_json_object('{"a": 1}') == {"a": 1}

    def test_json_with_extra_text(self):
        text = '分析结果如下：\n{"sections": []}\n完毕'
        assert extract_json_object(text) == {"sections": []}

    def test_invalid_raises(self):
        with pytest.raises(NoteAnalysisError):
            extract_json_object("没有任何 json")


class TestAnalyzeItem:
    def test_analyze_research_article(self, tmp_path):
        item = _make_item()
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]

        writer = MagicMock()
        writer.add_note.return_value = "NOTE1"

        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            '{"paper_type":"research_article","confidence":0.9,"evidence":["e"],"reason":"r"}',
            _sections_json(12),
            _short_note_json(),
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Introduction. This is the main text."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123")

        assert result["status"] == "ok"
        assert result["paper_type"] == "research_article"
        assert result["note_key"] == "NOTE1"
        assert result["short_note"] == "ok"
        assert ai_client.chat.call_count == 3  # classify + analyze + keywords
        writer.add_note.assert_called_once()
        writer.add_tags.assert_any_call("ABC123", [ANALYZED_TAG])
        writer.add_tags.assert_any_call("ABC123", [KEYWORDS_TAG])
        writer.update_short_note.assert_called_once_with("ABC123", "体系A | 机制B | 性能C | 疑问：未解问题")

    def test_analyze_book_prejudged_by_item_type(self, tmp_path):
        item = _make_item(item_type="book")
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]

        writer = MagicMock()
        writer.add_note.return_value = "NOTE1"

        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [_sections_json(12), _short_note_json()]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Chapter 1. Introduction."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123")

        assert result["paper_type"] == "book"
        assert ai_client.chat.call_count == 2  # analyze + keywords（book 无分类调用）

    def test_analyze_uncertain_skips_and_tags(self, tmp_path):
        item = _make_item()
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]

        writer = MagicMock()
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.return_value = '{"paper_type":"uncertain","confidence":0.4,"evidence":["e"],"reason":"r"}'

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Some text."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123")

        assert result["status"] == "uncertain"
        writer.add_note.assert_not_called()
        writer.add_tags.assert_called_once_with("ABC123", [NOT_ANALYZED_TAG])

    def test_analyze_already_analyzed(self):
        item = _make_item(tags=[ANALYZED_TAG])
        reader = MagicMock()
        reader.get_item.return_value = item
        writer = MagicMock()
        ai_client = MagicMock()

        result = analyze_item(reader, writer, ai_client, "ABC123")
        assert result["status"] == "already_analyzed"
        writer.add_note.assert_not_called()

    def test_analyze_force_reruns(self, tmp_path):
        item = _make_item(tags=[ANALYZED_TAG])
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        writer.add_note.return_value = "NOTE2"
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            '{"paper_type":"research_article","confidence":0.9,"evidence":["e"],"reason":"r"}',
            _sections_json(12),
            _short_note_json(),
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "text"},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123", force=True)

        assert result["status"] == "ok"

    def test_analyze_dry_run(self, tmp_path):
        item = _make_item()
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            '{"paper_type":"research_article","confidence":0.9,"evidence":["e"],"reason":"r"}',
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Introduction text."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123", dry_run=True)

        assert result["status"] == "dry_run"
        assert result["paper_type"] == "research_article"
        assert result["template"] == "research-article"
        writer.add_note.assert_not_called()

    def test_analyze_sparse_output_retries(self, tmp_path):
        item = _make_item()
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        writer.add_note.return_value = "NOTE1"
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            '{"paper_type":"research_article","confidence":0.9,"evidence":["e"],"reason":"r"}',
            _sections_json(3),
            _sections_json(15),
            _short_note_json(),
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Introduction text."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123")

        assert result["status"] == "ok"
        assert result["sections"] == 15
        assert ai_client.chat.call_count == 4  # classify + analyze + retry + keywords
        writer.add_note.assert_called_once()

    def test_analyze_sparse_retry_keeps_better_of_two(self, tmp_path):
        item = _make_item()
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        writer.add_note.return_value = "NOTE1"
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            '{"paper_type":"research_article","confidence":0.9,"evidence":["e"],"reason":"r"}',
            _sections_json(3),
            _sections_json(2),
            _short_note_json(),
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Introduction text."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123")

        assert result["status"] == "ok"
        assert result["sections"] == 3
        assert ai_client.chat.call_count == 4  # classify + analyze + retry + keywords

    def test_analyze_parse_failure_retries(self, tmp_path):
        item = _make_item(item_type="book")
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        writer.add_note.return_value = "NOTE1"
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            "这是一段没有 JSON 的输出",
            _sections_json(12),
            _short_note_json(),
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Chapter 1. Introduction."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123")

        assert result["status"] == "ok"
        assert result["sections"] == 12
        assert ai_client.chat.call_count == 3  # book：analyze + retry + keywords
        writer.add_note.assert_called_once()

    def test_analyze_parse_failure_twice_raises(self, tmp_path):
        item = _make_item(item_type="book")
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = ["无 JSON 一", "无 JSON 二"]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Chapter 1. Introduction."},
        ):
            with pytest.raises(NoteAnalysisError) as exc:
                analyze_item(reader, writer, ai_client, "ABC123")

        assert exc.value.code == "runtime_error"
        assert "两次都无法解析" in str(exc.value)
        writer.add_note.assert_not_called()


    def test_analyze_no_short_note_flag(self, tmp_path):
        item = _make_item()
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        writer.add_note.return_value = "NOTE1"
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            '{"paper_type":"research_article","confidence":0.9,"evidence":["e"],"reason":"r"}',
            _sections_json(12),
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Introduction text."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123", no_short_note=True)

        assert result["status"] == "ok"
        assert result["short_note"] == "skipped"
        assert ai_client.chat.call_count == 2  # classify + analyze only
        writer.update_short_note.assert_not_called()
        writer.add_tags.assert_any_call("ABC123", [ANALYZED_TAG])
        assert not any(
            call.args[1] in ([KEYWORDS_TAG], [NO_KEYWORDS_TAG]) for call in writer.add_tags.call_args_list
        )

    def test_analyze_keyword_failure_tags_no_keywords(self, tmp_path):
        item = _make_item()
        att = _make_attachment(tmp_path)
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_pdf_attachments.return_value = [att]
        writer = MagicMock()
        writer.add_note.return_value = "NOTE1"
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.side_effect = [
            '{"paper_type":"research_article","confidence":0.9,"evidence":["e"],"reason":"r"}',
            _sections_json(12),
            "没有 JSON 的关键词输出",
        ]

        with patch(
            "zotero_cli.core.note_analysis.convert_pdfs_to_text",
            return_value={att.path: "Introduction text."},
        ):
            result = analyze_item(reader, writer, ai_client, "ABC123")

        assert result["status"] == "ok"  # note 仍然成功
        assert result["short_note"] == "failed"
        assert result["short_note_error"]
        writer.add_note.assert_called_once()
        writer.add_tags.assert_any_call("ABC123", [ANALYZED_TAG])
        writer.add_tags.assert_any_call("ABC123", [NO_KEYWORDS_TAG])
        writer.update_short_note.assert_not_called()

    def test_analyze_short_note_only_backfill(self):
        item = _make_item()
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_notes.return_value = [
            Note(
                key="N_OLD",
                parent_key="ABC123",
                content="AI条目分析 - Test Paper\n" + ("旧版工作流生成的更长的旧笔记内容。\n" * 40),
            ),
            Note(key="N1", parent_key="ABC123", content="AI条目分析 - Test Paper\n\n## 🧭 阅读协议\n- 文本可读性：完整"),
            Note(key="N2", parent_key="ABC123", content="普通笔记"),
        ]
        writer = MagicMock()
        ai_client = MagicMock()
        ai_client.config = _make_ai_config()
        ai_client.chat.return_value = _short_note_json()

        result = analyze_item(reader, writer, ai_client, "ABC123", short_note_only=True)

        assert result["status"] == "short_note_only"
        assert result["note_key"] == "N1"  # 在含 "AI条目分析" 的笔记中选最新一条
        assert result["short_note"] == "ok"
        assert ai_client.chat.call_count == 1
        writer.add_note.assert_not_called()
        writer.update_short_note.assert_called_once_with("ABC123", "体系A | 机制B | 性能C | 疑问：未解问题")
        writer.add_tags.assert_called_once_with("ABC123", [KEYWORDS_TAG])

    def test_analyze_short_note_only_without_notes_raises(self):
        item = _make_item()
        reader = MagicMock()
        reader.get_item.return_value = item
        reader.get_notes.return_value = []
        writer = MagicMock()
        ai_client = MagicMock()

        with pytest.raises(NoteAnalysisError) as exc:
            analyze_item(reader, writer, ai_client, "ABC123", short_note_only=True)

        assert exc.value.code == "validation_error"


class TestValidateShortNote:
    def test_valid(self):
        assert validate_short_note("体系A | 机制B | 性能C | 疑问：未解问题") is None

    def test_minimal_three_segments(self):
        assert validate_short_note("体系A | 机制B | 疑问：未解问题") is None

    def test_empty(self):
        assert validate_short_note("") == "empty short_note"

    def test_too_long(self):
        assert "too long" in (validate_short_note("段 | " * 300) or "")

    def test_fewer_than_three_segments(self):
        assert "fewer than 3 segments" in (validate_short_note("体系A | 机制B") or "")

    def test_last_segment_must_start_with_question(self):
        assert "last segment must start with 疑问：" in (validate_short_note("体系A | 机制B | 性能C") or "")

    def test_newline_rejected(self):
        assert validate_short_note("体系A | 机制B\n性能C | 疑问：X") == "contains newline"

    def test_brackets_rejected(self):
        assert "forbidden bracket" in (validate_short_note("体系[A] | 机制B | 疑问：X") or "")

    def test_empty_segment_rejected(self):
        assert validate_short_note("体系A | | 机制B | 疑问：X") == "empty pipe segment"

    def test_leading_pipe_rejected(self):
        assert validate_short_note("| 体系A | 机制B | 疑问：X") == "leading/trailing pipe"


class TestMergeShortNote:
    def test_empty_extra(self):
        assert merge_short_note_into_extra("", "短语") == "short-note: 短语"

    def test_none_extra(self):
        assert merge_short_note_into_extra(None, "短语") == "short-note: 短语"  # type: ignore[arg-type]

    def test_appends_to_existing_lines(self):
        extra = "Funder: NSF\ntranslated-title: 标题"
        merged = merge_short_note_into_extra(extra, "短语")
        assert merged == "Funder: NSF\ntranslated-title: 标题\nshort-note: 短语"

    def test_replaces_existing_short_note(self):
        extra = "Funder: NSF\nshort-note: 旧短语\ntranslated-title: 标题"
        merged = merge_short_note_into_extra(extra, "新短语")
        assert merged == "Funder: NSF\ntranslated-title: 标题\nshort-note: 新短语"

    def test_replaces_case_insensitive(self):
        extra = "Short-Note: 旧短语"
        merged = merge_short_note_into_extra(extra, "新短语")
        assert merged == "short-note: 新短语"


class TestAiClient:
    @staticmethod
    def _respond(content: str = "{}"):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    def test_chat_sends_system_prompt_and_temperature(self):
        config = _make_ai_config()
        client = AiClient(config)
        client._client.chat.completions.create = MagicMock(return_value=self._respond())
        out = client.chat("hello")
        assert out == "{}"
        kwargs = client._client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == config.model
        assert kwargs["messages"][0]["role"] == "system"
        assert "科研文献分析" in kwargs["messages"][0]["content"]
        assert kwargs["messages"][1] == {"role": "user", "content": "hello"}
        assert kwargs["temperature"] == 0.7
        assert "max_tokens" not in kwargs

    def test_chat_passes_max_tokens_when_configured(self):
        config = _make_ai_config()
        config.max_tokens = 16384
        client = AiClient(config)
        client._client.chat.completions.create = MagicMock(return_value=self._respond())
        client.chat("hello")
        kwargs = client._client.chat.completions.create.call_args.kwargs
        assert kwargs["max_tokens"] == 16384

    def test_chat_temperature_override(self):
        config = _make_ai_config()
        client = AiClient(config)
        client._client.chat.completions.create = MagicMock(return_value=self._respond())
        client.chat("hello", temperature=0.1)
        kwargs = client._client.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.1
        client.chat("hello")
        kwargs = client._client.chat.completions.create.call_args.kwargs
        assert kwargs["temperature"] == 0.7  # 未覆盖时用 config 默认值


class TestAiAnalyzeCLI:
    def _run(self, args, env=None):
        runner = CliRunner()
        base_env = {
            "ZOT_DATA_DIR": str(FIXTURES_DIR),
            "ZOT_LIBRARY_ID": "123",
            "ZOT_API_KEY": "abc",
            "ZOT_FORMAT": "",
        }
        if env:
            base_env.update(env)
        return runner.invoke(main, args, env=base_env)

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
        with patch("zotero_cli.commands.ai_analyze.analyze_item", return_value=result), patch(
            "zotero_cli.commands.ai_analyze.ZoteroReader"
        ), patch("zotero_cli.commands.ai_analyze.ZoteroWriter"), patch(
            "zotero_cli.commands.ai_analyze.AiClient"
        ):
            res = self._run(["ai_analyze", "ABC123", "--dry-run"])

        assert res.exit_code == 0
        data = json.loads(res.output)["data"]
        assert data["status"] == "dry_run"
        assert data["paper_type"] == "research_article"

    def test_not_found_error_exit_code(self):
        with patch(
            "zotero_cli.commands.ai_analyze.analyze_item",
            side_effect=NoteAnalysisError("条目 'X' 不存在", code="not_found"),
        ), patch("zotero_cli.commands.ai_analyze.ZoteroReader"), patch(
            "zotero_cli.commands.ai_analyze.ZoteroWriter"
        ), patch("zotero_cli.commands.ai_analyze.AiClient"):
            res = self._run(["ai_analyze", "X"])

        assert res.exit_code == 4
        env = json.loads(res.output)
        assert env["error"]["code"] == "not_found"
