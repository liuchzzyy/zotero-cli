"""笔记文本归一化：上下标展平与中英混排空格。"""

from __future__ import annotations

import pytest

from zotero_cli.ai_notes.text import join_spaced_digits, pad_cjk_latin, plain_super_subscripts, prepare


class TestPadCjkLatin:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # 生成笔记中实际出现过的形态
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
            # 生成笔记中出现过的全部形态
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
            # MinerU 会把 LaTeX 数字分开输出，模型随后会误读或
            # 虚构精度，这正是产生伪造 BET 数值的原因。
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
