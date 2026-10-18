from zotero_cli.core.rag import infer_pdf_kind


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
