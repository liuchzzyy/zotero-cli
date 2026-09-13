"""Tests for update-status command and Semantic Scholar client."""

from __future__ import annotations

import json

from tests.support import invoke_cli as _run

from zotero_cli.core.semantic_scholar import (
    PublicationStatus,
    extract_arxiv_id,
    extract_preprint_info,
)


class TestUpdateStatusCli:
    def test_no_preprint_ids_json_envelope(self) -> None:
        result = _run(["--json", "update-status", "--limit", "1"])
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["results"] == []
        assert env["data"]["checked"] == 0

    def test_missing_item_json_not_found(self) -> None:
        result = _run(["--json", "update-status", "MISSING"])
        assert result.exit_code == 4
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["error"]["code"] == "not_found"


class TestExtractArxivId:
    def test_abs_url(self) -> None:
        assert extract_arxiv_id(url="https://arxiv.org/abs/1706.03762") == "1706.03762"

    def test_pdf_url(self) -> None:
        assert extract_arxiv_id(url="https://arxiv.org/pdf/2301.08745") == "2301.08745"

    def test_url_with_version(self) -> None:
        assert extract_arxiv_id(url="https://arxiv.org/abs/1706.03762v5") == "1706.03762"

    def test_arxiv_doi(self) -> None:
        assert extract_arxiv_id(doi="10.48550/arXiv.2303.08774") == "2303.08774"

    def test_extra_field(self) -> None:
        assert extract_arxiv_id(extra="arXiv:2201.11903") == "2201.11903"

    def test_no_arxiv(self) -> None:
        assert extract_arxiv_id(url="https://example.com", doi="10.1000/xyz") is None

    def test_none_inputs(self) -> None:
        assert extract_arxiv_id() is None

    def test_old_format_id(self) -> None:
        assert extract_arxiv_id(url="https://arxiv.org/abs/cs/0612047") == "cs/0612047"

    def test_five_digit_id(self) -> None:
        assert extract_arxiv_id(url="https://arxiv.org/abs/2301.12345") == "2301.12345"

    def test_biorxiv_returns_none(self) -> None:
        """extract_arxiv_id should NOT match bioRxiv."""
        assert extract_arxiv_id(url="https://www.biorxiv.org/content/10.1101/2023.05.22.540599v1") is None


class TestExtractPreprintInfo:
    def test_arxiv_url(self) -> None:
        info = extract_preprint_info(url="https://arxiv.org/abs/1706.03762")
        assert info is not None
        assert info.source == "arxiv"
        assert info.preprint_id == "1706.03762"
        assert info.api_id == "arXiv:1706.03762"

    def test_arxiv_http(self) -> None:
        info = extract_preprint_info(url="http://arxiv.org/abs/2306.05813")
        assert info is not None
        assert info.source == "arxiv"
        assert info.preprint_id == "2306.05813"

    def test_biorxiv_url(self) -> None:
        info = extract_preprint_info(url="https://www.biorxiv.org/content/10.1101/2023.05.22.540599v1")
        assert info is not None
        assert info.source == "doi"
        assert info.preprint_id == "10.1101/2023.05.22.540599"
        assert info.api_id == "DOI:10.1101/2023.05.22.540599"

    def test_biorxiv_doi(self) -> None:
        info = extract_preprint_info(doi="10.1101/2023.05.22.540599")
        assert info is not None
        assert info.source == "doi"
        assert info.preprint_id == "10.1101/2023.05.22.540599"

    def test_medrxiv_url(self) -> None:
        info = extract_preprint_info(url="https://www.medrxiv.org/content/10.1101/2023.06.09.544397v1")
        assert info is not None
        assert info.source == "doi"

    def test_no_preprint(self) -> None:
        assert extract_preprint_info(url="https://nature.com/articles/123") is None

    def test_none_inputs(self) -> None:
        assert extract_preprint_info() is None

    def test_arxiv_takes_priority_over_biorxiv(self) -> None:
        """If both arXiv and bioRxiv patterns exist, arXiv wins (checked first)."""
        info = extract_preprint_info(url="https://arxiv.org/abs/2301.08745", doi="10.1101/2023.01.01.000001")
        assert info is not None
        assert info.source == "arxiv"


class TestPublicationStatus:
    def test_published(self) -> None:
        s = PublicationStatus(
            preprint_id="1706.03762",
            source="arxiv",
            title="Attention Is All You Need",
            is_published=True,
            venue="NeurIPS",
            doi="10.5555/3295222.3295349",
        )
        assert s.is_published
        assert s.venue == "NeurIPS"

    def test_not_published(self) -> None:
        s = PublicationStatus(
            preprint_id="2301.08745",
            source="arxiv",
            title="Some Paper",
            is_published=False,
        )
        assert not s.is_published
        assert s.venue is None
        assert s.doi is None

    def test_biorxiv_published(self) -> None:
        s = PublicationStatus(
            preprint_id="10.1101/2023.05.22.540599",
            source="doi",
            title="Some Bio Paper",
            is_published=True,
            journal_name="Nature Methods",
            doi="10.1038/s41592-023-12345-6",
        )
        assert s.is_published
        assert s.journal_name == "Nature Methods"
