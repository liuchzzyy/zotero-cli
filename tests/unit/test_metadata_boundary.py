"""Crossref 响应与 DOI 传输标识必须原样通过边界校验。"""

from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

from zotero_cli.zotero import items as resolver


@pytest.mark.parametrize("payload", [[], None, 42, "invalid object"])
def test_non_object_crossref_payload_is_a_controlled_error(payload: object, monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock(status_code=200)
    response.json.return_value = payload
    monkeypatch.setattr(resolver.httpx, "get", Mock(return_value=response))
    with pytest.raises(resolver.MetadataResolveError):
        resolver.resolve_doi("10.1234/test")


def test_null_date_falls_back_to_online_date() -> None:
    assert resolver.map_crossref_to_zotero({"published-print": None, "published-online": {"date-parts": [[2024]]}}) == {
        "date": "2024"
    }


def test_doi_reserved_characters_remain_in_path(monkeypatch: pytest.MonkeyPatch) -> None:
    get = Mock(return_value=httpx.Response(200, json={"message": {"title": ["Paper"]}}))
    monkeypatch.setattr(resolver.httpx, "get", get)
    doi = "10.1234/a?b#c%20"
    resolver.resolve_doi(doi)
    request = httpx.Request("GET", get.call_args.args[0])
    assert request.url.query == b""
    assert request.url.fragment == ""
    assert request.url.path.endswith(doi)
