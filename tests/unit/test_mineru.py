from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import MagicMock

from zotero_cli.core.mineru import (
    MinerUParseCache,
    MinerUParseResult,
    _read_archive,
    _RemoteParse,
    _safe_extract_zip,
    extract_doi_from_markdown,
)


def _archive(markdown: str = "# Paper\nBody", content: list[dict] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("result/full.md", markdown)
        archive.writestr(
            "result/paper_content_list.json",
            json.dumps(content or [{"type": "text", "text": "Body", "page_idx": 0}]),
        )
        archive.writestr("result/images/figure.png", b"png")
    return output.getvalue()


def test_read_archive_requires_and_returns_markdown_and_structured_json() -> None:
    markdown, content, members = _read_archive(_archive())
    assert markdown == "# Paper\nBody"
    assert content[0]["page_idx"] == 0
    assert "result/full.md" in members


def test_safe_extract_zip_rejects_path_traversal(tmp_path) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("../escaped.txt", "blocked")
        archive.writestr("safe.txt", "allowed")
    output.seek(0)
    destination = tmp_path / "output"
    with zipfile.ZipFile(output) as archive:
        _safe_extract_zip(archive, destination)
    assert (destination / "safe.txt").read_text() == "allowed"
    assert not (tmp_path / "escaped.txt").exists()


def test_parse_cache_persists_canonical_package_and_reuses_it(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"fake-pdf-v1")
    archive = _archive()
    remote = _RemoteParse(
        markdown="# Paper\nBody",
        content_list=[{"type": "text", "text": "Body", "page_idx": 0}],
        archive=archive,
        archive_members=["result/full.md", "result/paper_content_list.json", "result/images/figure.png"],
        batch_id="batch-1",
    )
    client = MagicMock()
    client.parse_many.return_value = {pdf: remote}
    cache = MinerUParseCache(tmp_path / "cache", client=client, model_version="vlm")

    first = cache.ensure(pdf)
    second = cache.ensure(pdf)

    assert isinstance(first, MinerUParseResult)
    assert second.root == first.root
    assert first.markdown == "# Paper\nBody"
    assert first.content_list[0]["text"] == "Body"
    assert len(first.image_paths) == 1
    assert json.loads(first.manifest_path.read_text())["mineru_model_version"] == "vlm"
    client.parse_many.assert_called_once()


def test_parse_cache_key_changes_with_pdf_content(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"v1")
    remote = _RemoteParse("md", [], _archive("md", []), ["result/full.md"], "batch")
    client = MagicMock()
    client.parse_many.return_value = {pdf: remote}
    cache = MinerUParseCache(tmp_path / "cache", client=client, model_version="vlm")
    first = cache.ensure(pdf)
    pdf.write_bytes(b"v2")
    client.parse_many.return_value = {pdf: remote}
    second = cache.ensure(pdf)
    assert first.fingerprint != second.fingerprint


def test_extract_doi_from_mineru_markdown() -> None:
    assert extract_doi_from_markdown("DOI: 10.1234/example.paper).") == "10.1234/example.paper"
    assert extract_doi_from_markdown("no identifier") is None
