"""Offline checks for the Chinese metadata formatting rules.

The rules are: no super/subscripts (Unicode, HTML or LaTeX), and one half-width
space at every CJK↔Latin boundary without breaking a unit or a formula. Both are
applied to the Chinese title and the Chinese abstract before a Zotero write, so
the cases that must NOT change matter as much as the ones that must.
"""

import unittest

import zh_text


class FlattenTests(unittest.TestCase):
    def test_unicode_scripts_are_written_out(self):
        cases = {
            "MnO₂": "MnO2",
            "Ti₃C₂Tₓ": "Ti3C2Tx",
            "Zn²⁺": "Zn2+",
            "CO₃²⁻": "CO32-",
            "mAh g⁻¹": "mAh g^-1",
            "10⁻⁵ S cm⁻¹": "10^-5 S cm^-1",
            "m² g⁻¹": "m2 g^-1",
        }
        for written, flat in cases.items():
            with self.subTest(written=written):
                self.assertEqual(zh_text.flatten_scripts(written), flat)

    def test_a_positive_exponent_drops_the_caret_but_a_negative_keeps_it(self):
        self.assertEqual(zh_text.flatten_scripts("m²"), "m2")
        self.assertEqual(zh_text.flatten_scripts("g⁻¹"), "g^-1")

    def test_html_tags_are_removed_and_latex_braces_unwrapped(self):
        self.assertEqual(zh_text.flatten_scripts("MnO<sub>2</sub>"), "MnO2")
        self.assertEqual(zh_text.flatten_scripts("MnO$_{2}$"), "MnO2")
        self.assertEqual(zh_text.flatten_scripts("$g^{-1}$"), "g^-1")
        self.assertEqual(zh_text.flatten_scripts("g^{-1}"), "g^-1")
        self.assertEqual(zh_text.flatten_scripts("Zn^{2+}"), "Zn2+")

    def test_a_price_is_not_treated_as_inline_math(self):
        self.assertEqual(zh_text.flatten_scripts("成本 $500 per kWh"), "成本 $500 per kWh")


class SpacingTests(unittest.TestCase):
    def test_a_space_goes_at_every_cjk_latin_boundary(self):
        self.assertEqual(
            zh_text.normalize("层状δ-MnO2正极在5 A g^-1下提供300 mAh g^-1"),
            "层状 δ-MnO2 正极在 5 A g^-1 下提供 300 mAh g^-1",
        )

    def test_the_charge_sign_ends_the_latin_run(self):
        self.assertEqual(zh_text.normalize("Mg2+嵌入"), "Mg2+ 嵌入")

    def test_cjk_punctuation_is_not_spaced(self):
        for text in ("层状δ-MnO2（LIB）正极", "Fig.3、图2。下一句", "水系Zn–MnO2正极，性能优异"):
            with self.subTest(text=text):
                out = zh_text.normalize(text)
                self.assertIsNone(__import__("re").search(r"\s[，。、：；！？）]", out), out)

    def test_units_and_formulas_are_not_split(self):
        for text in ("mAh g^-1", "Wh kg^-1", "δ-MnO2", "InAs/ZnSe", "Li||Ni90", "10.1038/nenergy.2016.141"):
            with self.subTest(text=text):
                out = zh_text.normalize(f"性能{text}保持")
                self.assertIn(text, out)
                self.assertNotIn("  ", out)


class IdempotenceTests(unittest.TestCase):
    def test_normalize_twice_changes_nothing(self):
        once = zh_text.normalize("层状δ-MnO₂正极在5 A g⁻¹下提供300 mAh g⁻¹。")
        self.assertEqual(zh_text.normalize(once), once)

    def test_already_formatted_text_is_untouched(self):
        text = "层状 δ-MnO2 正极在 5 A g^-1 下提供 300 mAh g^-1。"
        self.assertEqual(zh_text.normalize(text), text)


class AbstractTests(unittest.TestCase):
    def test_a_leading_abstract_label_is_dropped(self):
        self.assertEqual(
            zh_text.normalize_abstract("摘要 高能量密度锂金属电池的界面化学。"), "高能量密度锂金属电池的界面化学。"
        )
        self.assertEqual(zh_text.normalize_abstract("摘要：界面化学。"), "界面化学。")
        self.assertEqual(zh_text.normalize_abstract("Abstract: interface chemistry."), "interface chemistry.")

    def test_text_that_only_mentions_the_word_is_not_mangled(self):
        text = "本文摘要汇总了界面化学的结果。"
        self.assertEqual(zh_text.normalize_abstract(text), text)


class ScriptDetectionTests(unittest.TestCase):
    def test_has_script_flags_all_three_representations(self):
        for text in ("MnO₂", "MnO<sub>2</sub>", "MnO$_{2}$", "g⁻¹"):
            with self.subTest(text=text):
                self.assertTrue(zh_text.has_script(text))

    def test_has_script_accepts_flat_text(self):
        for text in ("MnO2", "g^-1", "Zn2+", "无化学式的中文"):
            with self.subTest(text=text):
                self.assertFalse(zh_text.has_script(text))


if __name__ == "__main__":
    unittest.main()
