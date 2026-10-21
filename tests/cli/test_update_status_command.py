"""Tests for update-status command and Semantic Scholar client."""

from __future__ import annotations

import json
from unittest.mock import patch

from tests.support import invoke_cli as _run

from zotero_cli.cli.commands.metadata.publication_status import (
    PreprintInfo,
    PublicationStatus,
    SemanticScholarError,
    extract_preprint_info,
)


class TestUpdateStatusCli:
    def test_no_preprint_ids_json_envelope(self) -> None:
        result = _run(["--json", "metadata", "update-status", "--limit", "1"])
        assert result.exit_code == 0
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["results"] == []
        assert env["data"]["checked"] == 0

    def test_missing_item_json_not_found(self) -> None:
        result = _run(["--json", "metadata", "update-status", "MISSING"])
        assert result.exit_code == 4
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["error"]["code"] == "not_found"


class TestExtractArxivIds:
    """arXiv ID forms that update-status must recognise as preprints."""

    def test_pdf_url(self) -> None:
        info = extract_preprint_info(url="https://arxiv.org/pdf/2301.08745")
        assert info is not None
        assert info.source == "arxiv"
        assert info.preprint_id == "2301.08745"

    def test_url_with_version(self) -> None:
        info = extract_preprint_info(url="https://arxiv.org/abs/1706.03762v5")
        assert info is not None
        assert info.preprint_id == "1706.03762"

    def test_arxiv_doi(self) -> None:
        info = extract_preprint_info(doi="10.48550/arXiv.2303.08774")
        assert info is not None
        assert info.source == "arxiv"
        assert info.preprint_id == "2303.08774"

    def test_extra_field(self) -> None:
        info = extract_preprint_info(extra="arXiv:2201.11903")
        assert info is not None
        assert info.preprint_id == "2201.11903"

    def test_old_format_id(self) -> None:
        info = extract_preprint_info(url="https://arxiv.org/abs/cs/0612047")
        assert info is not None
        assert info.source == "arxiv"
        assert info.preprint_id == "cs/0612047"

    def test_non_arxiv_doi_is_ignored(self) -> None:
        assert extract_preprint_info(doi="10.1000/xyz") is None


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


# --- M-07 / L-04: explicit --dry-run and surfaced Semantic Scholar failures ---


class _FailingClient:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def check_publication(self, _info: object) -> object:
        raise SemanticScholarError("Semantic Scholar is unavailable", retryable=True)

    def close(self) -> None:
        pass


class _RateLimitedClient(_FailingClient):
    def check_publication(self, _info: object) -> object:
        raise SemanticScholarError("Semantic Scholar rate limit exceeded", retryable=True, rate_limited=True)


def _preprint_info(**_kwargs: object) -> PreprintInfo:
    return PreprintInfo(preprint_id="2401.12345", source="arxiv", api_id="arXiv:2401.12345")


def test_explicit_dry_run_is_a_preview_without_writes() -> None:
    result = _run(["--json", "metadata", "update-status", "--dry-run", "--limit", "1"])
    assert result.exit_code == 0
    env = json.loads(result.output)
    assert env["ok"] is True
    assert env["dry_run"] is True


def test_apply_and_dry_run_are_mutually_exclusive() -> None:
    result = _run(["--json", "metadata", "update-status", "--apply", "--dry-run"])
    assert result.exit_code == 3
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["error"]["code"] == "validation_error"


def test_semantic_scholar_failure_is_surfaced_as_network_error() -> None:
    with (
        patch("zotero_cli.cli.commands.metadata.update_status.extract_preprint_info", side_effect=_preprint_info),
        patch("zotero_cli.cli.commands.metadata.update_status.SemanticScholarClient", _FailingClient),
    ):
        result = _run(["--json", "metadata", "update-status", "--limit", "1"])
    assert result.exit_code == 5
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["error"]["code"] == "network_error"
    assert env["error"]["retryable"] is True


def test_semantic_scholar_rate_limit_is_surfaced_distinctly() -> None:
    with (
        patch("zotero_cli.cli.commands.metadata.update_status.extract_preprint_info", side_effect=_preprint_info),
        patch("zotero_cli.cli.commands.metadata.update_status.SemanticScholarClient", _RateLimitedClient),
    ):
        result = _run(["--json", "metadata", "update-status", "--limit", "1"])
    assert result.exit_code == 5
    env = json.loads(result.output)
    assert env["ok"] is False
    assert env["error"]["code"] == "rate_limited"
