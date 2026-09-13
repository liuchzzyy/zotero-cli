"""Tests for AI note templates and HTML rendering."""

from __future__ import annotations

from zotero_cli.core.note_renderer import render_note
from zotero_cli.core.note_templates import format_template, load_template, template_path


class TestNoteTemplates:
    def test_load_template_by_alias(self):
        template = load_template("research")
        assert "论文基本信息" in template
        assert "{title}" in template
        assert "{fulltext}" in template

    def test_load_template_by_full_name(self):
        template = load_template("review_article")
        assert "综述" in template

    def test_load_book_template(self):
        template = load_template("book")
        assert "体裁" in template

    def test_load_classify_template(self):
        template = load_template("classify-item")
        assert "research_article" in template
        assert "review_article" in template
        assert "book" in template

    def test_template_path_resolves_alias(self):
        assert template_path("book").name == "book.md"

    def test_format_template_fills_placeholders(self):
        result = format_template("标题：{title}，作者：{authors}", title="T", authors="A")
        assert result == "标题：T，作者：A"

    def test_format_template_missing_uses_unknown(self):
        result = format_template("标题：{title}")
        assert "未知" in result

    def test_templates_have_no_coral_emoji(self):
        for name in ("research", "review", "book", "classify-item"):
            assert "🪸" not in load_template(name)

    def test_templates_have_no_arrow_chain_lists(self):
        for name in ("research", "review", "book"):
            assert "↓" not in load_template(name)

    def test_short_note_template(self):
        template = load_template("short-note")
        assert "500" in template
        assert "疑问：" in template
        assert "{fulltext}" in template
        assert "short_note" in template

    def test_book_template_enforces_json_output(self):
        template = load_template("book")
        assert "JSON 输出示例" in template
        assert "严禁输出散文式读书笔记" in template
        assert "禁止输出 JSON 代码块以外的任何内容" in template

    def test_research_template_has_per_section_requirements(self):
        template = load_template("research")
        assert "各章节具体要求" in template
        assert "粗读筛选" in template
        assert "笔记原子化" in template
        assert "重组分子化" in template
        assert "输出要求" in template
        assert "2-3 句" in template  # 重点章节密度约束


class TestNoteRenderer:
    def test_render_heading_and_paragraph(self):
        sections = [
            {"type": "heading", "level": 3, "text": "📖 粗读筛选"},
            {"type": "paragraph", "text": "正文内容"},
        ]
        html = render_note(sections, title="AI条目分析 - Test")
        assert "<h1" in html
        assert "AI条目分析 - Test" in html
        assert "📖 粗读筛选" in html
        assert "正文内容" in html

    def test_render_uses_inline_primary_color(self):
        html = render_note([{"type": "hr"}], title="T")
        assert "#EF7060" in html

    def test_render_bullet_list_with_citations(self):
        sections = [
            {
                "type": "bullet_list",
                "items": [
                    {"text": "普通条目"},
                    {"text": "带引用", "citations": [{"location": "第3页", "content": "原文内容"}]},
                ],
            }
        ]
        html = render_note(sections, title="T")
        assert "<ul" in html
        assert "普通条目" in html
        assert "带引用" in html
        assert "「📍 第3页：原文内容」" in html
        assert "#2b6cb0" in html

    def test_render_table(self):
        sections = [{"type": "table", "headers": ["A", "B"], "rows": [["1", "2"]]}]
        html = render_note(sections, title="T")
        assert "<table" in html
        assert "<th" in html
        assert "<td" in html

    def test_render_escapes_html(self):
        sections = [{"type": "paragraph", "text": "<script>alert(1)</script>"}]
        html = render_note(sections, title="T")
        assert "<script>" not in html

    def test_render_unknown_block_type_is_ignored(self):
        html = render_note([{"type": "unknown", "text": "ZZZ_UNKNOWN_ZZZ"}], title="T")
        assert "ZZZ_UNKNOWN_ZZZ" not in html
