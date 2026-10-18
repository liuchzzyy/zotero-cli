"""Tests for note text flattening: no super/subscripts, CJK/Latin spacing."""

from __future__ import annotations

import pytest

from zotero_cli.core.notes.renderer import render_note
from zotero_cli.core.notes.short_note import normalize_short_note, validate_short_note
from zotero_cli.core.notes.templates import load_template
from zotero_cli.core.notes.text import join_spaced_digits, pad_cjk_latin, plain_super_subscripts, prepare


class TestPadCjkLatin:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # the shapes that actually appeared in a generated note
            ("层状δ-MnO2", "层状 δ-MnO2"),
            ("Rct由558.18降至60.61 Ω", "Rct 由 558.18 降至 60.61 Ω"),
            ("双直径CNT自支撑", "双直径 CNT 自支撑"),
            ("负载5 mg cm^-2下", "负载 5 mg cm^-2 下"),
            ("保持率100%以上", "保持率 100% 以上"),
            ("Mg2+嵌入", "Mg2+ 嵌入"),
            ("Na预插层", "Na 预插层"),
            ("纯CNT容量有限", "纯 CNT 容量有限"),
            ("Fig.3与Table S1", "Fig.3 与 Table S1"),
            ("约770 Wh/L", "约 770 Wh/L"),  # CJK next to a digit also gets a space
        ],
    )
    def test_inserts_space_at_boundaries(self, raw, expected):
        assert pad_cjk_latin(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "（LIB）与（Zn）",  # full-width brackets never gain a space
            "Fig.3、Fig.S1-S4）",  # CJK punctuation stays glued to Latin
            "10.1038/nenergy.2016.141",  # pure Latin, untouched
            "α-Fe2O3 与 MnO2 的复合",  # already padded
            "中文·英文",  # middle dot is not a Latin tail
            "①②③",  # enclosed numerals are not Latin
            "电压窗口 1.0–1.8 V",  # en dash inside numbers
            "",  # empty input
        ],
    )
    def test_leaves_non_boundaries_alone(self, raw):
        assert pad_cjk_latin(raw) == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "层状δ-MnO2与5 mg cm^-2负载",
            "层状 δ-MnO2 与 5 mg",  # already padded: no second space
        ],
    )
    def test_is_idempotent(self, raw):
        once = pad_cjk_latin(raw)
        assert pad_cjk_latin(once) == once


class TestPlainSuperSubscripts:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # every shape that occurred in a generated note
            ("mAh g⁻¹", "mAh g^-1"),
            ("Wh kg⁻¹", "Wh kg^-1"),
            ("F g⁻¹", "F g^-1"),
            ("W kg⁻¹", "W kg^-1"),
            ("cm⁻²", "cm^-2"),
            ("m² g⁻¹", "m2 g^-1"),  # only negative exponents keep the caret
            ("10⁻⁵ S cm⁻¹", "10^-5 S cm^-1"),
            ("Zn²⁺", "Zn2+"),
            ("Mn²⁺ 溶解", "Mn2+ 溶解"),
            ("H⁺", "H+"),
            ("MnO₂", "MnO2"),
            ("Ti₃C₂Tₓ", "Ti3C2Tx"),
            ("Li₄Ti₅O₁₂", "Li4Ti5O12"),
            ("CO₃²⁻", "CO32-"),  # charge signs flatten, as with Zn2+
        ],
    )
    def test_flattens_to_plain_text(self, raw, expected):
        assert plain_super_subscripts(raw) == expected

    @pytest.mark.parametrize("raw", ["MnO2 与 Zn2+", "没有脚本字符", ""])
    def test_leaves_plain_text_alone(self, raw):
        assert plain_super_subscripts(raw) == raw

    def test_no_script_character_survives(self):
        raw = "MnO₂/m² g⁻¹/Zn²⁺/Ti₃C₂Tₓ/10⁻⁵"
        assert not any(ch in "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻₀₁₂₃₄₅₆₇₈₉ₓ" for ch in plain_super_subscripts(raw))


class TestJoinSpacedDigits:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # MinerU emits LaTeX digits separately; the model then misreads or
            # invents precision, which is what produced a fabricated BET value.
            ("$2 0 1 . 6 \\lor 5 . 6 3 . 7 4 4$", "$2 0 1 . 6 \\lor 5 . 6 3 . 7 4 4$"),
            ("capacity 1 7 2 . 3 mAh", "capacity 1 7 2 . 3 mAh"),
            ("$3 2 9$ mAh", "$329$ mAh"),
            ("2 4 8 0 . 6 W kg", "2 4 8 0 . 6 W kg"),
        ],
    )
    def test_rejoins_split_numbers(self, raw, expected):
        assert join_spaced_digits(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "10.1038/nenergy.2016.141",  # DOI untouched
            "published 2016 ; doi 10.1",  # prose spacing untouched
            "98.6 Wh kg^-1",
            "JCPDS 44-0141",
        ],
    )
    def test_leaves_real_numbers_alone(self, raw):
        assert join_spaced_digits(raw) == raw

    def test_never_creates_a_double_dot(self):
        assert ".." not in join_spaced_digits("$2 0 1 . 6$")

    def test_is_idempotent(self):
        once = join_spaced_digits("$2 0 1 . 6$")
        assert join_spaced_digits(once) == once


class TestPrepare:
    def test_flattens_then_spaces(self):
        assert prepare("负载5 mg cm⁻²下，MnO₂保持332.2 mAh g⁻¹") == ("负载 5 mg cm^-2 下，MnO2 保持 332.2 mAh g^-1")

    def test_idempotent(self):
        once = prepare("MnO₂与Zn²⁺在5 mg cm⁻²下")
        assert prepare(once) == once


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
        # normalize_short_note must stay a punctuation/newline normaliser: the
        # 500-char budget is validated before padding is applied.
        out = normalize_short_note("Na预插层δ-MnO2 | 界面主导存储 | 疑问: 界面容量未分离")
        assert out == "Na预插层δ-MnO2 | 界面主导存储 | 疑问：界面容量未分离"
        assert validate_short_note(out) is None

    def test_padded_short_note_is_valid(self):
        padded = prepare(
            "Na预插层层状δ-MnO2与双直径CNT | 界面主导存储 | 329 mAh g^-1 | 无粘结剂自支撑 | 疑问：容量贡献未分离"
        )
        assert validate_short_note(padded) is None
        assert "Na 预插层层状 δ-MnO2 与双直径 CNT" in padded


class TestTemplateSpacingRule:
    @pytest.mark.parametrize("name", ["research", "review", "book"])
    def test_analysis_templates_state_the_rule(self, name):
        template = load_template(name)
        assert "中英混排规则" in template
        assert "半角空格" in template

    @pytest.mark.parametrize("name", ["research", "review", "book"])
    def test_templates_pin_section_heading_level(self, name):
        # Without this the model sometimes emits level 3 for top-level
        # sections, which loses the coloured section banner.
        assert "`level: 2`" in load_template(name)

    def test_short_note_template_states_the_rule(self):
        template = load_template("short-note")
        assert "半角空格" in template
        assert "α-MnO2 水系锌电正极" in template
