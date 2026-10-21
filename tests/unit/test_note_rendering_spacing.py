"""渲染、短笔记与模板三处必须遵守同一套中英混排规则。"""

from __future__ import annotations

import pytest

from zotero_cli.ai_notes.renderer import render_note
from zotero_cli.ai_notes.short_note import normalize_short_note, validate_short_note
from zotero_cli.ai_notes.templates import load_template
from zotero_cli.ai_notes.text import prepare


class TestRendererSpacing:
    def test_paragraph_heading_list_and_table_cells(self):
        html = render_note(
            [
                {"type": "heading", "level": 2, "text": "🧩 版式与Rct分析"},
                {"type": "paragraph", "text": "负载5 mg cm^-2下保持率100%"},
                {
                    "type": "bullet_list",
                    "items": [
                        {
                            "text": "层状δ-MnO2提供界面",
                            "citations": [{"location": "Fig.5g", "content": "Rct由558.18 Ω降至60.61 Ω"}],
                        }
                    ],
                },
                {"type": "table", "headers": ["指标", "数值"], "rows": [["Rct", "60.61 Ω"]]},
            ],
            title="A solid future",
        )
        assert "版式与 Rct 分析" in html
        assert "负载 5 mg cm^-2 下保持率 100%" in html
        assert "层状 δ-MnO2 提供界面" in html
        assert "Rct 由 558.18 Ω 降至 60.61 Ω" in html

    def test_escapes_html_after_padding(self):
        html = render_note([{"type": "paragraph", "text": "a<b>c</b>与Mn"}], title="t")
        assert "<b>" not in html
        assert "&lt;b&gt;" in html
        assert "与 Mn" in html


class TestShortNoteSpacing:
    def test_normalize_keeps_punctuation_rules_unpadded(self):
        # normalize_short_note 只负责标点与换行归一化；最终的
        # 500 字符预算针对实际存储的加空格后文本校验。
        out = normalize_short_note("Na预插层δ-MnO2 | 界面主导存储 | 疑问: 界面容量未分离")
        assert out == "Na预插层δ-MnO2 | 界面主导存储 | 疑问：界面容量未分离"
        assert validate_short_note(out) is None

    def test_padded_short_note_is_valid(self):
        padded = prepare(
            "Na预插层层状δ-MnO2与双直径CNT | 界面主导存储 | 329 mAh g^-1 | 无粘结剂自支撑 | 疑问：容量贡献未分离"
        )
        assert validate_short_note(padded) is None
        assert "Na 预插层层状 δ-MnO2 与双直径 CNT" in padded

    def test_padded_over_limit_short_note_is_rejected(self):
        padded = prepare("中A" * 100 + " | " + "中A" * 100 + " | 疑问：中A")
        assert len(padded) > 500
        error = validate_short_note(padded)
        assert error is not None and "too long" in error


class TestTemplateSpacingRule:
    @pytest.mark.parametrize("name", ["research", "review", "book"])
    def test_analysis_templates_state_the_rule(self, name):
        template = load_template(name)
        assert "中英混排规则" in template
        assert "半角空格" in template

    @pytest.mark.parametrize("name", ["research", "review", "book"])
    def test_templates_pin_section_heading_level(self, name):
        # 缺少这条约束时，模型有时会把顶层区段输出成三级标题，
        # 从而导致彩色区段横幅消失。
        assert "`level: 2`" in load_template(name)

    def test_short_note_template_states_the_rule(self):
        template = load_template("short-note")
        assert "半角空格" in template
        assert "α-MnO2 水系锌电正极" in template
