from zotero_cli.core.rag import infer_pdf_kind


def test_supporting_information_with_doi_is_not_main():
    assert infer_pdf_kind("Supporting Information\nDOI: 10.1234/example\nFigure S1", "paper.pdf") == "supplementary"


def test_main_article_citing_supplementary_remains_main():
    assert infer_pdf_kind("Cite this: Journal\nDOI: 10.1234/example\nSee Supporting Information for methods.") == "main"
