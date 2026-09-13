from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from zotero_cli_agent.core.pdf_extractor import PyMuPdfExtractor, _safe_extract_zip

FIXTURES = Path(__file__).parent / "fixtures"


def test_extract_full_pdf():
    text = PyMuPdfExtractor().extract_text(FIXTURES / "test.pdf")
    assert "test PDF" in text


def test_extract_specific_pages():
    text = PyMuPdfExtractor().extract_text(FIXTURES / "test.pdf", pages=(1, 1))
    assert "test PDF" in text


def test_extract_nonexistent_pdf():
    with pytest.raises(FileNotFoundError):
        PyMuPdfExtractor().extract_text(FIXTURES / "nonexistent.pdf")


def test_safe_extract_zip_rejects_sibling_prefix_traversal(tmp_path):
    output_dir = tmp_path / "out"
    sibling_dir = tmp_path / "out_evil"
    archive = BytesIO()
    with ZipFile(archive, "w") as zf:
        zf.writestr("../out_evil/escaped.txt", "must not be written")
        zf.writestr("safe.txt", "allowed")
    archive.seek(0)
    with ZipFile(archive) as zf:
        _safe_extract_zip(zf, output_dir)

    assert (output_dir / "safe.txt").read_text() == "allowed"
    assert not (sibling_dir / "escaped.txt").exists()
