"""Offline HTTP and clock-driven tests for Semantic Scholar publication checks."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any

import httpx
import pytest

from zotero_cli.core.zotero import publication_status as semantic_scholar
from zotero_cli.core.zotero.publication_status import (
    API_BASE,
    FIELDS,
    REQUEST_TIMEOUT,
    PreprintInfo,
    PublicationStatus,
    SemanticScholarClient,
    SemanticScholarError,
    extract_preprint_info,
)


@dataclass
class OfflineClock:
    now: float = 1000.0
    sleeps: list[float] = field(default_factory=list)

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> OfflineClock:
    clock = OfflineClock()
    monkeypatch.setattr(semantic_scholar.time, "time", clock.time)
    monkeypatch.setattr(semantic_scholar.time, "monotonic", clock.time)
    monkeypatch.setattr(semantic_scholar.time, "sleep", clock.sleep)
    return clock


ResponseFactory = Callable[
    [list[httpx.Response | httpx.HTTPError], str | None],
    tuple[SemanticScholarClient, list[httpx.Request]],
]


@pytest.fixture
def make_client(monkeypatch: pytest.MonkeyPatch, clock: OfflineClock) -> Iterator[ResponseFactory]:
    """Keep the real HTTP client; supply only an offline transport and clock."""
    real_client = httpx.Client
    clients: list[httpx.Client] = []

    def create(
        responses: list[httpx.Response | httpx.HTTPError], api_key: str | None = None
    ) -> tuple[SemanticScholarClient, list[httpx.Request]]:
        remaining = iter(responses)
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            response = next(remaining, None)
            assert response is not None, f"Unexpected extra HTTP request: {request.url}"
            if isinstance(response, httpx.HTTPError):
                raise response
            return response

        def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs["transport"] = httpx.MockTransport(handler)
            kwargs["trust_env"] = False
            client = real_client(*args, **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(semantic_scholar.httpx, "Client", client_factory)
        return SemanticScholarClient(api_key=api_key), requests

    yield create
    for client in clients:
        client.close()


@pytest.fixture
def preprint() -> PreprintInfo:
    return PreprintInfo(preprint_id="2401.12345", source="arxiv", api_id="arXiv:2401.12345")


def published_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "A published study",
        "externalIds": {"DOI": "10.1234/published"},
        "journal": {"name": "Journal of Offline Studies"},
        "venue": "Research Conference",
        "publicationVenue": {"name": "Structured venue"},
        "publicationDate": "2024-05-01",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("api_key", [None, "offline-api-key"])
def test_client_sends_fields_headers_and_configured_timeout(
    make_client: ResponseFactory, preprint: PreprintInfo, api_key: str | None
) -> None:
    client, requests = make_client([httpx.Response(200, json=published_payload())], api_key)
    status = client.check_publication(preprint)
    assert status == PublicationStatus(
        preprint_id="2401.12345",
        source="arxiv",
        title="A published study",
        is_published=True,
        venue="Research Conference",
        journal_name="Journal of Offline Studies",
        doi="10.1234/published",
        publication_date="2024-05-01",
    )
    [request] = requests
    assert request.method == "GET"
    assert str(request.url).split("?")[0] == f"{API_BASE}/paper/{preprint.api_id}"
    assert request.url.params["fields"] == FIELDS
    assert request.headers.get("x-api-key") == api_key
    assert set(request.extensions["timeout"].values()) == {REQUEST_TIMEOUT}
    client.close()
    assert client._client.is_closed


@pytest.mark.parametrize(
    ("payload", "published", "journal_name", "doi"),
    [
        (
            published_payload(journal=None, venue="", publicationVenue={"name": "Formal Journal"}),
            True,
            "Formal Journal",
            "10.1234/published",
        ),
        (
            published_payload(journal="unexpected string", venue="Conference", publicationVenue=None),
            True,
            None,
            "10.1234/published",
        ),
        (published_payload(journal=None, venue="", publicationVenue=[]), False, None, "10.1234/published"),
        (published_payload(externalIds={}, journal=None, publicationVenue=None), False, None, None),
        ({}, False, None, None),
        (
            published_payload(externalIds={"DOI": "10.48550/arXiv.2401.12345"}),
            False,
            "Journal of Offline Studies",
            None,
        ),
        (
            published_payload(externalIds={"DOI": "10.1101/2024.01.01.123456"}),
            False,
            "Journal of Offline Studies",
            None,
        ),
        (published_payload(venue="ARXIV", journal=None, publicationVenue=None), False, None, "10.1234/published"),
        (published_payload(journal={"name": "bioRxiv"}), False, "bioRxiv", "10.1234/published"),
        (published_payload(journal=None, publicationVenue={"name": "medRxiv"}), False, "medRxiv", "10.1234/published"),
        (
            published_payload(venue="SSRN Electronic Journal", journal=None, publicationVenue=None),
            False,
            None,
            "10.1234/published",
        ),
    ],
    ids=[
        "structured-venue-fallback",
        "non-dict-journal",
        "no-formal-venue",
        "no-doi",
        "empty-paper",
        "arxiv-doi",
        "biorxiv-doi",
        "arxiv-venue",
        "biorxiv-journal",
        "medrxiv-structured-venue",
        "ssrn-venue",
    ],
)
def test_publication_requires_formal_venue_and_nonpreprint_doi(
    make_client: ResponseFactory,
    preprint: PreprintInfo,
    payload: dict[str, Any],
    published: bool,
    journal_name: str | None,
    doi: str | None,
) -> None:
    client, requests = make_client([httpx.Response(200, json=payload)], None)
    status = client.check_publication(preprint)
    assert status is not None
    assert status.is_published is published
    assert status.journal_name == journal_name
    assert status.doi == doi
    assert status.title == payload.get("title", "")
    assert status.venue == (payload.get("venue") or None)
    assert len(requests) == 1


def test_doi_source_identity_survives_publication_lookup(make_client: ResponseFactory) -> None:
    info = PreprintInfo("10.1101/2024.01.01.123456", "doi", "DOI:10.1101/2024.01.01.123456")
    client, requests = make_client([httpx.Response(200, json=published_payload())], None)
    status = client.check_publication(info)
    assert status is not None
    assert status.preprint_id == info.preprint_id
    assert status.source == "doi"
    assert requests[0].url.path.endswith(info.api_id)


@pytest.mark.parametrize(
    ("status_code", "raises", "retryable"),
    [
        (400, True, False),
        (401, True, False),
        (403, True, False),
        (404, False, False),
        (500, True, True),
        (503, True, True),
    ],
)
def test_non_success_http_status_surfaces_typed_error_without_retry(
    make_client: ResponseFactory,
    preprint: PreprintInfo,
    clock: OfflineClock,
    status_code: int,
    raises: bool,
    retryable: bool,
) -> None:
    client, requests = make_client([httpx.Response(status_code, text="Offline API error")], None)
    if raises:
        with pytest.raises(SemanticScholarError) as caught:
            client.check_publication(preprint)
        assert caught.value.retryable is retryable
    else:
        assert client.check_publication(preprint) is None
    assert len(requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize("error_class", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_transport_failures_raise_retryable_error(
    make_client: ResponseFactory,
    preprint: PreprintInfo,
    clock: OfflineClock,
    error_class: type[httpx.HTTPError],
) -> None:
    client, requests = make_client([error_class("Offline connection failed")], None)
    with pytest.raises(SemanticScholarError) as caught:
        client.check_publication(preprint)
    assert caught.value.retryable is True
    assert len(requests) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(("api_key", "delay"), [(None, 3.0), ("offline-api-key", 1.0)])
def test_rate_limit_waits_only_for_remaining_interval(
    make_client: ResponseFactory, preprint: PreprintInfo, clock: OfflineClock, api_key: str | None, delay: float
) -> None:
    client, requests = make_client([httpx.Response(200, json={}) for _ in range(3)], api_key)
    assert client.check_publication(preprint) is not None
    assert clock.sleeps == []
    clock.now += 0.25
    assert client.check_publication(preprint) is not None
    assert clock.sleeps == [delay - 0.25]
    clock.now += delay + 0.5
    assert client.check_publication(preprint) is not None
    assert clock.sleeps == [delay - 0.25]
    assert len(requests) == 3


@pytest.mark.parametrize(("api_key", "delay"), [(None, 3.0), ("offline-api-key", 1.0)])
def test_rate_limited_request_retries_once_then_obeys_normal_spacing(
    make_client: ResponseFactory, preprint: PreprintInfo, clock: OfflineClock, api_key: str | None, delay: float
) -> None:
    client, requests = make_client(
        [httpx.Response(429), httpx.Response(200, json=published_payload()), httpx.Response(200, json={})], api_key
    )
    status = client.check_publication(preprint)
    assert status is not None and status.is_published is True
    assert clock.sleeps == [delay * 2]
    assert requests[0].url == requests[1].url
    assert client.check_publication(preprint) is not None
    assert clock.sleeps == [delay * 2, delay]
    assert len(requests) == 3


@pytest.mark.parametrize(
    "retry_response",
    [httpx.Response(429), httpx.Response(404), httpx.Response(503), httpx.ReadTimeout("Offline retry timeout")],
    ids=["still-limited", "missing", "unavailable", "retry-network-error"],
)
def test_failed_rate_limit_retry_stops_after_two_attempts(
    make_client: ResponseFactory,
    preprint: PreprintInfo,
    clock: OfflineClock,
    retry_response: httpx.Response | httpx.HTTPError,
) -> None:
    client, requests = make_client([httpx.Response(429), retry_response], None)
    with pytest.raises(SemanticScholarError) as caught:
        client.check_publication(preprint)
    assert caught.value.retryable is True
    assert len(requests) == 2
    assert clock.sleeps == [6.0]


@pytest.mark.parametrize("rate_limited_first", [False, True])
def test_invalid_json_is_an_unavailable_lookup_not_an_uncaught_decoder_error(
    make_client: ResponseFactory, preprint: PreprintInfo, rate_limited_first: bool
) -> None:
    responses: list[httpx.Response | httpx.HTTPError] = [
        httpx.Response(200, text="<html>Upstream gateway error</html>")
    ]
    if rate_limited_first:
        responses.insert(0, httpx.Response(429))
    client, _ = make_client(responses, None)
    with pytest.raises(SemanticScholarError) as caught:
        client.check_publication(preprint)
    assert caught.value.retryable is True


def test_null_external_ids_does_not_crash_an_unpublished_lookup(
    make_client: ResponseFactory, preprint: PreprintInfo
) -> None:
    client, _ = make_client([httpx.Response(200, json=published_payload(externalIds=None))], None)
    status = client.check_publication(preprint)
    assert status is not None
    assert status.is_published is False
    assert status.doi is None


@pytest.mark.parametrize("payload", [[], [published_payload()], "not a paper object", 42])
def test_non_object_json_is_an_unavailable_lookup(
    make_client: ResponseFactory, preprint: PreprintInfo, payload: Any
) -> None:
    client, _ = make_client([httpx.Response(200, json=payload)], None)
    with pytest.raises(SemanticScholarError) as caught:
        client.check_publication(preprint)
    assert caught.value.retryable is True


@pytest.mark.parametrize("retry_after", ["12", "18"])
def test_retry_after_header_is_respected_before_second_request(
    make_client: ResponseFactory, preprint: PreprintInfo, clock: OfflineClock, retry_after: str
) -> None:
    client, requests = make_client(
        [httpx.Response(429, headers={"Retry-After": retry_after}), httpx.Response(200, json={})], None
    )
    assert client.check_publication(preprint) is not None
    assert sum(clock.sleeps) >= float(retry_after)
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [("not-a-delay", 6.0), ("-3", 6.0), ("nan", 6.0), ("inf", 6.0), ("http-date", 15.0)],
)
def test_retry_after_dates_and_invalid_headers_keep_bounded_safe_spacing(
    make_client: ResponseFactory,
    preprint: PreprintInfo,
    clock: OfflineClock,
    retry_after: str,
    expected_delay: float,
) -> None:
    if retry_after == "http-date":
        retry_after = format_datetime(datetime.fromtimestamp(clock.now + expected_delay, tz=timezone.utc), usegmt=True)
    client, requests = make_client(
        [httpx.Response(429, headers={"Retry-After": retry_after}), httpx.Response(200, json={})], None
    )
    assert client.check_publication(preprint) is not None
    assert clock.sleeps == [expected_delay]
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {"url": "https://arxiv.org/pdf/hep-th/9901001v2.pdf"},
            PreprintInfo("hep-th/9901001", "arxiv", "arXiv:hep-th/9901001"),
        ),
        ({"doi": "10.48550/arXiv.2401.12345v2"}, PreprintInfo("2401.12345", "arxiv", "arXiv:2401.12345")),
        (
            {"extra": "citation info\narXiv:2401.12345v3\nother metadata"},
            PreprintInfo("2401.12345", "arxiv", "arXiv:2401.12345"),
        ),
        (
            {"url": "https://www.medrxiv.org/content/10.1101/2024.01.01.123456v3.full.pdf"},
            PreprintInfo("10.1101/2024.01.01.123456", "doi", "DOI:10.1101/2024.01.01.123456"),
        ),
    ],
    ids=["legacy-arxiv-pdf", "versioned-arxiv-doi", "multiline-extra", "medrxiv-pdf"],
)
def test_preprint_identifiers_are_normalized_without_network(kwargs: dict[str, str], expected: PreprintInfo) -> None:
    assert extract_preprint_info(**kwargs) == expected


@pytest.mark.parametrize("kwargs", [{}, {"url": ""}, {"doi": "10.1234/formal"}, {"extra": "No preprint here"}])
def test_nonpreprint_metadata_has_no_identifier(kwargs: dict[str, str]) -> None:
    assert extract_preprint_info(**kwargs) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"externalIds": {"DOI": 42}},
        {"venue": 42},
        {"journal": {"name": 42}},
        {"journal": None, "publicationVenue": {"name": 42}},
    ],
)
def test_wrong_field_types_do_not_escape_the_service_boundary(
    make_client: ResponseFactory, preprint: PreprintInfo, overrides: dict[str, Any]
) -> None:
    client, _ = make_client([httpx.Response(200, json=published_payload(**overrides))], None)
    status = client.check_publication(preprint)
    assert status is None or isinstance(status, PublicationStatus)


def test_excessive_retry_after_does_not_sleep_or_retry(
    make_client: ResponseFactory, preprint: PreprintInfo, clock: OfflineClock
) -> None:
    client, requests = make_client([httpx.Response(429, headers={"Retry-After": "9999999999999999999999999999"})], None)
    with pytest.raises(SemanticScholarError) as caught:
        client.check_publication(preprint)
    assert caught.value.rate_limited is True
    assert requests and len(requests) == 1
    assert clock.sleeps == []
