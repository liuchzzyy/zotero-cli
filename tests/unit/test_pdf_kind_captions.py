"""MS/SI classification from figure and table numbering.

Every case is either a real publisher form observed in the local MinerU cache or
a regression for an item the earlier string heuristics got wrong. The numbering
is the strongest available signal: an SI cover repeats the article title and
filename, so per-attachment text cues cannot separate the pair.
"""

from __future__ import annotations

import pytest

from zotero_cli.core.rag import CaptionCounts, PdfDocument, caption_counts, resolve_pdf_kinds

# Article front matter: what keeps a document out of the "supplementary" role.
_FRONT_MATTER = "Abstract: aqueous zinc batteries. \n1. Introduction\n"


def _blocks(*captions: tuple[str, str]) -> list[dict[str, object]]:
    """Build image/table blocks carrying the given (type, caption) pairs."""
    blocks: list[dict[str, object]] = []
    for block_type, caption in captions:
        blocks.append({"type": block_type, "page_idx": 0, f"{block_type}_caption": [caption]})
    return blocks


def _figures(count: int, *, numbered_s: bool, template: str = "Figure S{n}. Panel.") -> list[tuple[str, str]]:
    if not numbered_s:
        template = template.replace("S{n}", "{n}")
    return [("image", template.format(n=index + 1)) for index in range(count)]


class TestCaptionForms:
    @pytest.mark.parametrize(
        "caption",
        [
            "Figure S1. MnOR polarization curves.",
            "Fig. S1. MnOR polarization curves.",
            "FIG. S2. Panel.",
            "Supplementary Figure 1. Morphologies.",
            "Supplementary Fig. 1 Supplemental HRTEM images.",
            "Electronic Supplementary Material Figure S3.",
            "Supplementary Figure S4. Redundant S.",
        ],
    )
    def test_s_numbered_figure_forms(self, caption: str) -> None:
        counts = caption_counts(_blocks(("chart", caption)))
        assert counts.figure_supplementary == 1
        assert counts.figure_plain == 0
        assert counts.kind == "supplementary"

    @pytest.mark.parametrize(
        "caption",
        ["Figure 1. Failure modes.", "Fig. 2. XRD patterns.", "Scheme 1. Synthesis route."],
    )
    def test_plain_figure_forms(self, caption: str) -> None:
        counts = caption_counts(_blocks(("image", caption)))
        assert counts.figure_plain == 1
        assert counts.kind == "main"

    @pytest.mark.parametrize(
        "caption",
        ["Table S1. Unit cell parameters.", "Supplementary Table 1. EXAFS fits.", "Supplementary Table 3-1. Fits."],
    )
    def test_s_numbered_table_forms(self, caption: str) -> None:
        counts = caption_counts(_blocks(("table", caption)))
        assert counts.table_supplementary == 1
        assert counts.kind == "supplementary"

    def test_plain_table_form(self) -> None:
        counts = caption_counts(_blocks(("table", "Table 1. Electrical conductivity.")))
        assert counts.table_plain == 1
        assert counts.kind == "main"

    def test_panel_fragments_are_not_captions(self) -> None:
        counts = caption_counts(_blocks(("image", "a b"), ("chart", "C"), ("chart", "(a-1)"), ("image", "")))
        assert counts == CaptionCounts()

    def test_caption_with_leading_panel_letter_is_still_found(self) -> None:
        # MinerU merges the panel letter into the caption of the panel it closes.
        counts = caption_counts(_blocks(("image", "e Supplementary Figure 5. Temperature dependence.")))
        assert counts.figure_supplementary == 1

    def test_plain_caption_that_mentions_an_si_figure_counts_as_plain(self) -> None:
        counts = caption_counts(_blocks(("image", "Figure 5. Comparison with Supplementary Fig. 3.")))
        assert counts.figure_plain == 1
        assert counts.figure_supplementary == 0

    def test_figures_outrank_tables(self) -> None:
        # A Nature-style supplement numbers figures plainly but tables with an S;
        # figures must decide so a real article is not demoted.
        counts = CaptionCounts(figure_supplementary=0, figure_plain=18, table_supplementary=4, table_plain=0)
        assert counts.kind == "main"

    def test_tables_decide_when_there_are_no_figures(self) -> None:
        counts = CaptionCounts(table_supplementary=6, table_plain=0)
        assert counts.kind == "supplementary"

    def test_no_captions_is_unknown(self) -> None:
        assert caption_counts([]).kind == "unknown"


class TestResolveKinds:
    def test_single_attachment_is_main(self) -> None:
        document = PdfDocument("A", "paper.pdf", "text", _blocks(("image", "Figure S1. Odd but alone.")))
        assert resolve_pdf_kinds([document]) == {"A": "main"}

    def test_no_attachments(self) -> None:
        assert resolve_pdf_kinds([]) == {}

    def test_s_numbered_attachment_becomes_the_supplement(self) -> None:
        # Item DDM2563M: the article was read as SI because its Introduction
        # starts past the 2500-char window and its SI mention lands at char 9073.
        main = PdfDocument("MS", "a.pdf", _FRONT_MATTER, _blocks(*_figures(5, numbered_s=False)))
        si = PdfDocument("SI", "a.pdf", "# Enhanced kinetics", _blocks(*_figures(34, numbered_s=True)))
        assert resolve_pdf_kinds([main, si]) == {"MS": "main", "SI": "supplementary"}

    def test_unnumbered_attachment_beside_a_numbered_one_is_the_supplement(self) -> None:
        main = PdfDocument("MS", "a.pdf", _FRONT_MATTER, _blocks(*_figures(3, numbered_s=False)))
        si = PdfDocument("SI", "a.pdf", "", [])
        assert resolve_pdf_kinds([main, si]) == {"MS": "main", "SI": "supplementary"}

    def test_s_numbered_tables_rescue_a_nature_style_supplement(self) -> None:
        # Item XTNNHCYF: the SI numbers its figures plainly ("Figure 1") but its
        # tables "Supplementary Table 1"; both attachments otherwise look alike.
        main = PdfDocument("MS", "a.pdf", "", _blocks(*_figures(4, numbered_s=False)))
        si = PdfDocument(
            "SI",
            "a.pdf",
            "",
            _blocks(*_figures(18, numbered_s=False), *[("table", f"Supplementary Table {n}.") for n in range(1, 5)]),
        )
        assert resolve_pdf_kinds([main, si]) == {"MS": "main", "SI": "supplementary"}

    def test_front_matter_is_never_demoted_to_supplementary(self) -> None:
        # Both attachments carry S-numbered captions (a merged article+SI PDF).
        main = PdfDocument("MS", "a.pdf", _FRONT_MATTER, _blocks(*_figures(9, numbered_s=True)))
        si = PdfDocument("SI", "b.pdf", "", _blocks(*_figures(2, numbered_s=True)))
        kinds = resolve_pdf_kinds([main, si])
        assert kinds["MS"] == "main"
        assert kinds["SI"] == "supplementary"

    def test_ambiguous_pair_falls_back_to_cover_heuristics(self) -> None:
        main = PdfDocument("MS", "paper.pdf", _FRONT_MATTER, [])
        si = PdfDocument("SI", "paper_SI.pdf", "Supporting Information\nMethods", [])
        assert resolve_pdf_kinds([main, si]) == {"MS": "main", "SI": "supplementary"}

    def test_hopeless_pair_keeps_the_longer_document_as_main(self) -> None:
        short = PdfDocument("SHORT", "a.pdf", "x" * 100, [])
        long = PdfDocument("LONG", "b.pdf", "y" * 500, [])
        assert resolve_pdf_kinds([short, long]) == {"SHORT": "supplementary", "LONG": "main"}

    def test_one_main_survives_a_second_supplement(self) -> None:
        documents = [
            PdfDocument("MS", "a.pdf", _FRONT_MATTER, _blocks(*_figures(4, numbered_s=False))),
            PdfDocument("SI1", "b.pdf", "", _blocks(*_figures(6, numbered_s=True))),
            PdfDocument("SI2", "c.pdf", "", _blocks(*_figures(3, numbered_s=True))),
        ]
        kinds = resolve_pdf_kinds(documents)
        assert kinds == {"MS": "main", "SI1": "supplementary", "SI2": "supplementary"}
