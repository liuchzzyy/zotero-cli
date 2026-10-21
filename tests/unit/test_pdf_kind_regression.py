"""PDF kind heuristics: the failure modes this file must keep out.

Every rule here is a string cue over the first few thousand characters plus the
attachment filename; there is no structural or layout evidence involved, so each
case below is a documented real misclassification, not a hypothetical one.
"""

import pytest

from zotero_cli.workspace.rag import infer_pdf_kind


def test_supporting_information_with_doi_is_not_main():
    assert infer_pdf_kind("Supporting Information\nDOI: 10.1234/example\nFigure S1", "paper.pdf") == "supplementary"


def test_main_article_citing_supplementary_remains_main():
    assert infer_pdf_kind("Cite this: Journal\nDOI: 10.1234/example\nSee Supporting Information for methods.") == "main"


def test_main_article_with_abstract_and_introduction_not_supplementary():
    text = (
        "# A Very Novel Battery Research Article\n"
        "Author One, Author Two\n"
        "Department of Chemistry, University of Science\n\n"
        "Abstract: Aqueous zinc-ion batteries have attracted great attention. "
        + ("Here we study the fundamental mechanisms. " * 15)
        + "\n\n## 1. Introduction\n"
        + ("Energy storage systems are crucial for modern society. " * 15)
        + "\nAs shown in Figure S1 in the Supporting Information, we synthesized the materials."
    )
    assert infer_pdf_kind(text, "paper.pdf") == "main"


def test_electronic_annex_cover_is_supplementary():
    text = (
        "## Electronic Annex\n\n"
        'to the article "Zn sorption modifies the layer structure of vernadite"\n\n'
        "Supplementary methods and Figure S1."
    )
    assert infer_pdf_kind(text, "paper.pdf") == "supplementary"


@pytest.mark.parametrize(
    "filename",
    [
        "paper_SI.pdf",
        "supplementary-material.pdf",
        "paper-ESI.pdf",
        "supporting_information.pdf",
        "SI.pdf",
    ],
)
def test_supplementary_filename_cues_are_honoured(filename: str):
    assert infer_pdf_kind("Data discussion and methods overview.", filename) == "supplementary"


@pytest.mark.parametrize("filename", ["sodium_mechanism.pdf", "spin_coating.pdf", "main_article.pdf", "papers.pdf"])
def test_main_filename_tokens_are_not_mistaken_for_si(filename: str):
    assert infer_pdf_kind("Data discussion and methods overview.", filename) == "main"


def test_cite_this_cover_overrides_supplementary_filename_cue():
    text = "Cite this: J. Mater. Chem. A\nDOI: 10.1234/example\nAbstract of the article."
    assert infer_pdf_kind(text, "paper_SI.pdf") == "main"


# --- A publisher DOI stamp must not outvote an SI cover ---------------------
# Real case (item CQUZFVHV, att QGTE5WK8, file sciadv.aea1401_sm.pdf): the cover
# reads "Supplementary Materials for <title>", the DOI appears at char 247, and
# the old rule order returned "main" — so every SI figure was treated as required
# main-text evidence.


@pytest.mark.parametrize(
    "cover",
    [
        "# Supplementary Materials for Activating the Mn(2+)/Mn(7+) Redox",
        "Electronic Supplementary Material (ESI) for Energy & Environmental Science",
        "Supporting Information for Enabling Stable MnO2 Cathodes",
    ],
)
def test_si_cover_with_a_doi_stamp_is_supplementary(cover: str):
    text = f"{cover}\nSome Author et al.\nDOI: 10.1126/sciadv.aea1401\ntable of contents follows"
    assert infer_pdf_kind(text, "paper.pdf") == "supplementary"


def test_doi_without_a_cover_still_reads_as_a_main_article():
    text = "A Study of Manganese Oxides\nAuthor One\nDOI: 10.1021/example\nOur findings are summarised below."
    assert infer_pdf_kind(text, "paper.pdf") == "main"


# --- Cover lines are matched as prefixes, not as whole lines ---------------


@pytest.mark.parametrize(
    "cover",
    [
        "Supporting Information for Some Paper Title",
        "Supplementary Material for Some Paper Title",
        "Electronic Supplementary Material (ESI) for Some Journal",
        "Supplementary Information: Experimental Details",
    ],
)
def test_cover_line_prefix_variants_are_supplementary(cover: str):
    text = f"{cover}\nSection 1. Methods\nWe describe the synthesis."
    assert infer_pdf_kind(text, "paper.pdf") == "supplementary"


@pytest.mark.parametrize(
    "line",
    [
        "Supporting Information is available free of charge from the publisher.",
        "Supporting Information and raw data can be requested from the authors.",
    ],
)
def test_prose_that_starts_with_the_cover_phrase_is_not_a_cover_line(line: str):
    # Front matter of a real article, with the sentence appearing in the body.
    text = "# A Study of Manganese Oxides\nAuthor One\nDOI: 10.1021/example\n" + "Body text. " * 200 + f"\n{line}"
    assert infer_pdf_kind(text, "paper.pdf") == "main"


def test_an_unrecognised_si_cover_outranks_a_doi_stamp_within_the_first_page():
    # A cover variant the line rules do not parse, plus the parent article's DOI.
    text = "FULL PAPER\nSupplementary figures, tables and methods\nDOI: 10.1039/example\nFigure S1. XRD patterns."
    assert infer_pdf_kind(text, "paper.pdf") == "supplementary"
