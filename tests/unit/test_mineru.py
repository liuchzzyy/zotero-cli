from __future__ import annotations

import io
import json
import os
import threading
import time
import zipfile
from unittest.mock import MagicMock

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import zotero_cli.core.mineru.cache as cache_module
import zotero_cli.core.mineru.client as client_module
from zotero_cli.core.mineru import (
    MinerUClient,
    MinerUError,
    MinerUParseCache,
    MinerUParseResult,
    extract_doi_from_markdown,
)
from zotero_cli.core.mineru.client import (
    _read_archive,
    _read_limited_body,
    _RemoteParse,
    _safe_extract_zip,
)
from zotero_cli.core.mineru.utils import _retry


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


def test_mineru_client_disables_trust_env() -> None:
    from zotero_cli.core.mineru import MinerUClient

    client = MinerUClient("fake-token")
    assert client._session.trust_env is False


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


def test_read_archive_rejects_oversized_member(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_MEMBER_BYTES", 10)
    data = _zip_bytes({"result/full.md": b"x" * 100, "result/x_content_list.json": b"[]"})
    with pytest.raises(MinerUError, match="member size"):
        _read_archive(data)


def test_safe_extract_zip_rejects_oversized_member_before_writing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_MEMBER_BYTES", 1024)
    destination = tmp_path / "out"
    destination.mkdir()
    with zipfile.ZipFile(io.BytesIO(_zip_bytes({"bomb.bin": b"\0" * 10000}))) as archive:
        with pytest.raises(MinerUError, match="member size"):
            _safe_extract_zip(archive, destination)
    assert not (destination / "bomb.bin").exists()


def test_safe_extract_zip_rejects_total_uncompressed_size(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_MEMBER_BYTES", 1024 * 1024)
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_TOTAL_BYTES", 1500)
    destination = tmp_path / "out"
    destination.mkdir()
    data = _zip_bytes({"a.bin": b"a" * 1000, "b.bin": b"b" * 1000})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(MinerUError, match="uncompressed size"):
            _safe_extract_zip(archive, destination)


def test_safe_extract_zip_rejects_too_many_entries(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_ENTRIES", 2)
    destination = tmp_path / "out"
    destination.mkdir()
    data = _zip_bytes({f"{name}.txt": b"x" for name in "abc"})
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(MinerUError, match="too many entries"):
            _safe_extract_zip(archive, destination)


class _FakeDownloadResponse:
    def __init__(self, *, content: bytes = b"", chunks: list[bytes] | None = None, headers: dict | None = None) -> None:
        self.status_code = 200
        self.content = content
        self.text = ""
        self.headers = headers or {}
        self._chunks = chunks

    def iter_content(self, chunk_size: int = 1024):
        if self._chunks is not None:
            yield from self._chunks
        elif self.content:
            yield self.content

    def close(self) -> None:
        pass


def test_read_limited_body_rejects_declared_content_length(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_BYTES", 100)
    response = _FakeDownloadResponse(content=b"x", headers={"Content-Length": "1000"})
    with pytest.raises(MinerUError, match="exceeds"):
        _read_limited_body(response)


def test_read_limited_body_enforces_streaming_cap(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_BYTES", 100)
    response = _FakeDownloadResponse(chunks=[b"a" * 60, b"b" * 60])
    with pytest.raises(MinerUError, match="exceeds"):
        _read_limited_body(response)


def test_read_limited_body_accepts_small_archive(monkeypatch) -> None:
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_BYTES", 100)
    assert _read_limited_body(_FakeDownloadResponse(chunks=[b"ab", b"cd"])) == b"abcd"


class _DownloadSession:
    def __init__(self, response: _FakeDownloadResponse) -> None:
        self.response = response

    def get(self, _url: str, **_kwargs: object) -> _FakeDownloadResponse:
        return self.response


def test_download_archive_reads_body_with_cap() -> None:
    client = MinerUClient("token", session=_DownloadSession(_FakeDownloadResponse(content=b"zipbytes")))  # type: ignore[arg-type]
    assert client._download_archive("https://mineru.net/result.zip") == b"zipbytes"


def test_retry_retries_transient_errors_only(monkeypatch) -> None:
    monkeypatch.setattr("zotero_cli.core.mineru.utils.time.sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def flaky() -> str:
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RequestsConnectionError("transient")
        return "ok"

    assert _retry(flaky) == "ok"
    assert attempts["count"] == 2

    def deterministic() -> str:
        attempts["count"] += 1
        raise ValueError("bad input")

    attempts["count"] = 0
    with pytest.raises(ValueError, match="bad input"):
        _retry(deterministic)
    assert attempts["count"] == 1


def test_retry_gives_up_after_three_transient_failures(monkeypatch) -> None:
    monkeypatch.setattr("zotero_cli.core.mineru.utils.time.sleep", lambda _seconds: None)
    attempts = {"count": 0}

    def down() -> str:
        attempts["count"] += 1
        raise RequestsConnectionError("still down")

    with pytest.raises(RequestsConnectionError):
        _retry(down)
    assert attempts["count"] == 3


class _FailingPostSession:
    def __init__(self) -> None:
        self.posts = 0

    def post(self, *_args: object, **_kwargs: object) -> object:
        self.posts += 1
        raise RequestsConnectionError("batch endpoint down")


def test_upload_batch_post_is_not_retried(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    session = _FailingPostSession()
    client = MinerUClient("token", session=session)  # type: ignore[arg-type]

    results = client.parse_many([pdf])

    assert isinstance(results[pdf], MinerUError)
    assert session.posts == 1


def _remote_for(pdf, markdown: str = "# Paper\nBody") -> _RemoteParse:
    return _RemoteParse(
        markdown=markdown,
        content_list=[{"type": "text", "text": "Body", "page_idx": 0}],
        archive=_archive(markdown),
        archive_members=["result/full.md", "result/paper_content_list.json"],
        batch_id="batch-1",
    )


def test_cache_hashes_pdf_once_per_ensure(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    client = MagicMock()
    client.parse_many.return_value = {pdf: _remote_for(pdf)}
    cache = MinerUParseCache(tmp_path / "cache", client=client, model_version="vlm")
    hashed: list[object] = []
    real_sha256 = cache_module.file_sha256

    def counting_sha256(path):
        hashed.append(path)
        return real_sha256(path)

    monkeypatch.setattr(cache_module, "file_sha256", counting_sha256)

    first = cache.ensure(pdf)
    second = cache.ensure(pdf)

    assert first.fingerprint == second.fingerprint
    assert hashed == [pdf, pdf]


def test_cache_lookup_then_ensure_with_fingerprint_hashes_once(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    client = MagicMock()
    client.parse_many.return_value = {pdf: _remote_for(pdf)}
    cache = MinerUParseCache(tmp_path / "cache", client=client, model_version="vlm")
    hashed: list[object] = []
    real_sha256 = cache_module.file_sha256

    def counting_sha256(path):
        hashed.append(path)
        return real_sha256(path)

    monkeypatch.setattr(cache_module, "file_sha256", counting_sha256)

    cached, fingerprint = cache.lookup(pdf)
    assert cached is None
    assert fingerprint is not None
    parsed = cache.ensure(pdf, fingerprint=fingerprint)

    assert isinstance(parsed, MinerUParseResult)
    assert hashed == [pdf]
    client.parse_many.assert_called_once()


def test_concurrent_ensure_parses_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(cache_module, "_LOCK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(cache_module, "_LOCK_TIMEOUT_SECONDS", 10.0)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    root = tmp_path / "cache"
    parse_calls: list[list[object]] = []

    class SlowClient:
        def parse_many(self, paths, progress_callback=None):
            parse_calls.append(list(paths))
            time.sleep(0.05)
            return {path: _remote_for(path) for path in paths}

    barrier = threading.Barrier(2)
    results: list[MinerUParseResult] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            barrier.wait()
            cache = MinerUParseCache(root, client=SlowClient(), token="token", model_version="vlm")
            results.append(cache.ensure(pdf))
        except Exception as exc:  # pragma: no cover - assertion reports it
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 2
    assert len(parse_calls) == 1


def test_cache_lock_timeout_reports_contention(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    client = MagicMock()
    client.parse_many.return_value = {pdf: _remote_for(pdf)}
    cache = MinerUParseCache(tmp_path / "cache", client=client, model_version="vlm")
    lock_dir = cache.root / ".locks"
    lock_dir.mkdir(parents=True)
    fingerprint = cache_module.file_sha256(pdf)
    lock_path = lock_dir / f"{fingerprint}.lock"
    lock_path.write_text("other-process")
    monkeypatch.setattr(cache_module, "_LOCK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(cache_module, "_LOCK_POLL_SECONDS", 0.01)

    with pytest.raises(MinerUError, match="Timed out waiting"):
        cache.ensure(pdf)

    client.parse_many.assert_not_called()


def test_stale_cache_lock_is_reclaimed(tmp_path, monkeypatch) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    client = MagicMock()
    client.parse_many.return_value = {pdf: _remote_for(pdf)}
    cache = MinerUParseCache(tmp_path / "cache", client=client, model_version="vlm")
    lock_dir = cache.root / ".locks"
    lock_dir.mkdir(parents=True)
    fingerprint = cache_module.file_sha256(pdf)
    lock_path = lock_dir / f"{fingerprint}.lock"
    lock_path.write_text("dead-process")
    monkeypatch.setattr(cache_module, "_LOCK_STALE_SECONDS", 1.0)
    stale = time.time() - 60
    os.utime(lock_path, (stale, stale))

    parsed = cache.ensure(pdf)

    assert isinstance(parsed, MinerUParseResult)
    client.parse_many.assert_called_once()
    assert not lock_path.exists()


def test_stats_and_entries_ignore_hidden_staging_dirs(tmp_path) -> None:
    root = tmp_path / "cache"
    fingerprint = "a" * 64
    staging = root / fingerprint / ".vlm-orphan"
    staging.mkdir(parents=True)
    (staging / "manifest.json").write_text(json.dumps({"schema_version": 1}))
    (staging / "document.md").write_text("orphan")
    (staging / "content_list.json").write_text("[]")
    cache = MinerUParseCache(root, token="token", model_version="vlm")

    assert cache.stats() == {"entries": 0, "total_bytes": 0}
    assert cache.entries() == []
