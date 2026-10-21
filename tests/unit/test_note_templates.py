"""AI 笔记模板与 HTML 渲染测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from zotero_cli.ai_notes.constants import MAX_PROMPT_CHARS
from zotero_cli.ai_notes.rendering import render_note
from zotero_cli.ai_notes.templates import fit_text_to_prompt, format_template, load_template, template_path


class TestNoteTemplates:
    def test_load_template_missing_raises_informative_error(self):
        with pytest.raises(FileNotFoundError, match="AI note template 'nonexistent'"):
            load_template("nonexistent")

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

    def test_package_data_dir_is_a_fallback(self):
        """提示词模板从包目录解析。"""
        from zotero_cli.ai_notes import templates as note_templates

        package_data = Path(note_templates.__file__).resolve().parent / "templates"
        assert package_data.name == "templates"
        assert note_templates._candidate_template_dirs() == [package_data]

    def test_every_prompt_template_resolves(self):
        for name in ("research-article", "review-article", "book", "classify-item", "short-note"):
            assert template_path(name).exists(), name

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

    def test_every_template_delimits_untrusted_content(self):
        for name in ("research-article", "review-article", "book", "classify-item", "short-note"):
            template = load_template(name)
            assert "数据边界" in template, name
            assert "不是指令" in template, name
            assert "忽略" in template, name

    def test_fit_text_to_prompt_bounds_template_plus_text(self):
        template = load_template("book")
        fitted = fit_text_to_prompt(template, "x" * (MAX_PROMPT_CHARS * 2))
        assert len(fitted) == MAX_PROMPT_CHARS - len(template) - 2_000
        assert len(template) + len(fitted) <= MAX_PROMPT_CHARS


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
        assert "「📍 第 3 页：原文内容」" in html
        assert "#2b6cb0" in html

    def test_render_nested_bullet_list(self):
        sections = [
            {
                "type": "bullet_list",
                "items": [
                    {
                        "text": "一级项目 A",
                        "items": [
                            {"text": "二级项目 A1"},
                            {"text": "二级项目 A2"},
                        ],
                    },
                    "一级项目 B",
                ],
            }
        ]
        html = render_note(sections, title="T")
        assert "一级项目 A" in html
        assert "二级项目 A1" in html
        assert "二级项目 A2" in html
        assert "一级项目 B" in html
        assert html.count("<ul") == 2

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

    def test_render_defensive_types(self):
        # items 为普通字符串列表，citations 为普通字符串，level 为 None，table row 异常
        sections = [
            {"type": "heading", "level": None, "text": "标题"},
            {"type": "bullet_list", "items": ["纯文本项1", {"text": "带字符串引用", "citations": ["p. 5 原文"]}]},
            {"type": "table", "headers": ["H1"], "rows": ["单列标量值"]},
            "非法非dict结构",
        ]
        html = render_note(sections, title="T")
        assert "<h3" in html
        assert "纯文本项 1" in html
        assert "「📍 p. 5 原文」" in html
        assert "单列标量值" in html
