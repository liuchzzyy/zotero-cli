"""以真实文件与请求构造验证 MinerU 的安全边界。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from requests import PreparedRequest, Response, Session
from requests.adapters import BaseAdapter

from zotero_cli import config
from zotero_cli.ai_pdf import MinerUClient, MinerUError, MinerUParseCache, MinerUParseResult, file_sha256

_API_BASE = "https://mineru.example.invalid/api/v4"
_TOKEN = "test-only-mineru-token"


class _MinerUTransport(BaseAdapter):
    """只替换 HTTP I/O，保留 requests 的鉴权与重定向逻辑。"""

    def __init__(self, archive: bytes, zip_url: str) -> None:
        super().__init__()
        self.archive = archive
        self.zip_url = zip_url
        self.requests: list[PreparedRequest] = []
        self.uploads: list[bytes] = []
        self.names: list[str] = []
        self.redirects: dict[str, str] = {}

    def send(self, request: PreparedRequest, **_kwargs: Any) -> Response:
        self.requests.append(request)
        response = Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        if request.method == "POST" and request.url == f"{_API_BASE}/file-urls/batch":
            payload = json.loads(request.body)
            self.names = [item["name"] for item in payload["files"]]
            data = {
                "batch_id": "test-batch",
                "file_urls": [f"https://upload.example.invalid/input-{index}" for index in range(len(self.names))],
            }
            response._content = json.dumps({"code": 0, "data": data}).encode()
        elif request.method == "PUT" and request.url.startswith("https://upload.example.invalid/"):
            self.uploads.append(request.body.read())
            response._content = b""
        elif request.method == "GET" and request.url == f"{_API_BASE}/extract-results/batch/test-batch":
            data = {
                "extract_result": [
                    {"file_name": name, "state": "done", "full_zip_url": self.zip_url} for name in self.names
                ]
            }
            response._content = json.dumps({"code": 0, "data": data}).encode()
        elif request.method == "GET":
            if request.url in self.redirects:
                response.status_code = 302
                response.headers["Location"] = self.redirects[request.url]
                response._content = b""
            else:
                response._content = self.archive
        else:
            raise AssertionError(f"Unexpected HTTP request: {request.method} {request.url}")
        response._content_consumed = True
        return response

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """读取真实的空配置，绝不读取用户含凭据的配置。"""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[pdf]\n", encoding="utf-8")
    monkeypatch.setattr(config.paths, "config_file_path", lambda *_args: config_path)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "sample_data" / "minimal-one-page.pdf"
    destination = tmp_path / "paper.pdf"
    shutil.copyfile(source, destination)
    return destination


@pytest.fixture
def archive(tmp_path: Path) -> bytes:
    path = tmp_path / "result.zip"
    with zipfile.ZipFile(path, "w") as result:
        result.writestr("result/full.md", "# Paper\nParsed evidence")
        result.writestr(
            "result/paper_content_list.json",
            json.dumps([{"type": "text", "text": "Parsed evidence", "page_idx": 0}]),
        )
        result.writestr("result/images/figure.png", b"test-only-image")
    with zipfile.ZipFile(path) as result:
        assert result.testzip() is None
    return path.read_bytes()


def _client(archive: bytes, zip_url: str, *, api_base: str = _API_BASE) -> tuple[MinerUClient, _MinerUTransport]:
    transport = _MinerUTransport(archive, zip_url)
    session = Session()
    session.trust_env = False
    session.mount("https://", transport)
    session.mount("http://", transport)
    session.headers["authorization"] = "Bearer inherited-session-token"
    return MinerUClient(_TOKEN, api_base=api_base, session=session), transport


@pytest.mark.parametrize(
    "zip_url",
    [
        "https://storage.example.invalid/result.zip?X-Amz-Signature=test%2Fsignature&expires=123",
        "https://mineru.example.invalid:444/result.zip?signature=test",
        "http://mineru.example.invalid/result.zip?signature=test",
    ],
)
def test_cross_origin_archive_never_sends_authorization(
    pdf: Path, archive: bytes, tmp_path: Path, zip_url: str
) -> None:
    client, transport = _client(archive, zip_url)
    cache = MinerUParseCache(tmp_path / "cache", client=client, token=_TOKEN, model_version="vlm")

    result = cache.ensure(pdf)

    assert isinstance(result, MinerUParseResult)
    assert result.markdown == "# Paper\nParsed evidence"
    assert result.content_list[0]["text"] == "Parsed evidence"
    assert transport.uploads == [pdf.read_bytes()]
    download = next(request for request in transport.requests if request.url == zip_url)
    assert "Authorization" not in download.headers
    assert client._session.headers["Authorization"] == "Bearer inherited-session-token"


@pytest.mark.parametrize(
    "zip_url", ["https://mineru.example.invalid/result.zip", "https://mineru.example.invalid:443/result.zip"]
)
def test_same_origin_archive_retains_bearer(archive: bytes, zip_url: str) -> None:
    client, transport = _client(archive, zip_url)

    assert client._download_archive(zip_url) == archive

    assert transport.requests[-1].headers["Authorization"] == f"Bearer {_TOKEN}"


def test_cross_origin_archive_ignores_session_auth(archive: bytes) -> None:
    zip_url = "https://storage.example.invalid/result.zip?signature=test"
    client, transport = _client(archive, zip_url)
    client._session.auth = ("test-only-user", "test-only-password")

    assert client._download_archive(zip_url) == archive

    assert "Authorization" not in transport.requests[-1].headers
    assert client._session.auth == ("test-only-user", "test-only-password")


def test_archive_auth_uses_the_origin_requests_actually_sends(archive: bytes) -> None:
    # 在 authority 中的 @ 之前出现反斜杠时，urllib.parse 与 requests 的判断不一致。
    zip_url = r"https://storage.example.invalid\@mineru.example.invalid/result.zip?signature=test"
    client, transport = _client(archive, zip_url)

    assert client._download_archive(zip_url) == archive

    download = transport.requests[-1]
    assert urlsplit(download.url).hostname == "storage.example.invalid"
    assert "Authorization" not in download.headers


def test_presigned_redirects_cannot_reintroduce_netrc_auth(
    archive: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netrc = tmp_path / "test.netrc"
    netrc.write_text(
        "machine storage.example.invalid login test-only-user password test-only-password\n", encoding="utf-8"
    )
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    zip_url = "https://storage.example.invalid/result.zip?signature=test"
    final_url = "https://storage.example.invalid/final.zip?signature=next%2Fsignature"
    client, transport = _client(archive, zip_url)
    client._session.trust_env = True
    transport.redirects[zip_url] = final_url

    assert client._download_archive(zip_url) == archive

    assert [request.url for request in transport.requests] == [zip_url, final_url]
    assert all("Authorization" not in request.headers for request in transport.requests)


def test_archive_redirect_loop_is_bounded_without_leaking_auth(archive: bytes) -> None:
    zip_url = "https://storage.example.invalid/result.zip?signature=test"
    client, transport = _client(archive, zip_url)
    transport.redirects[zip_url] = zip_url

    with pytest.raises(MinerUError, match="redirects"):
        client._download_archive(zip_url)

    assert len(transport.requests) <= 31
    assert all("Authorization" not in request.headers for request in transport.requests)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_archive_redirects_recheck_exact_origin(archive: bytes, scheme: str) -> None:
    api_base = f"{scheme}://mineru.example.invalid/api/v4"
    zip_url = f"{scheme}://mineru.example.invalid/result.zip"
    same_origin = f"{scheme}://mineru.example.invalid/relative.zip"
    cross_origin = "https://mineru.example.invalid:444/presigned.zip?signature=test%2Fvalue"
    # requests 允许同一主机内 http -> https 跳转时保留 Authorization，
    # 但此处的协议变更已经跨越了安全边界。
    upgraded = "https://mineru.example.invalid/final.zip?signature=test%2Fvalue"
    client, transport = _client(archive, zip_url, api_base=api_base)
    transport.redirects = {zip_url: "relative.zip", same_origin: upgraded, upgraded: cross_origin}

    assert client._download_archive(zip_url) == archive

    assert [request.url for request in transport.requests] == [zip_url, same_origin, upgraded, cross_origin]
    for request in transport.requests:
        if request.url in (zip_url, same_origin) or (scheme == "https" and request.url == upgraded):
            assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
        else:
            assert "Authorization" not in request.headers


@pytest.mark.parametrize(
    "model_version", [".", "..", "", "../vlm", "vlm/child", r"vlm\child", "/vlm", "vlm\x00", "vlm\n"]
)
def test_invalid_model_version_cannot_replace_cache_ancestors(
    pdf: Path, archive: bytes, tmp_path: Path, model_version: str
) -> None:
    cache_root = tmp_path / "cache"
    fingerprint_root = cache_root / file_sha256(pdf)
    fingerprint_root.mkdir(parents=True)
    sentinel = cache_root / "unrelated-package.txt"
    sentinel.write_text("must survive", encoding="utf-8")
    sibling = fingerprint_root / "existing-package.txt"
    sibling.write_text("must also survive", encoding="utf-8")
    client, transport = _client(archive, "https://storage.example.invalid/result.zip?signature=test")

    with pytest.raises(MinerUError, match="model version"):
        cache = MinerUParseCache(cache_root, client=client, token=_TOKEN, model_version=model_version)
        cache.ensure(pdf)

    assert sentinel.read_text(encoding="utf-8") == "must survive"
    assert sibling.read_text(encoding="utf-8") == "must also survive"
    assert transport.requests == []


@pytest.mark.parametrize("link_location", ["fingerprint", "model", "model-to-parent"])
def test_cache_destination_cannot_escape_fingerprint_through_symlinks(
    pdf: Path, archive: bytes, tmp_path: Path, link_location: str
) -> None:
    cache_root = tmp_path / "cache"
    cache_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "unrelated.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    fingerprint_root = cache_root / file_sha256(pdf)
    if link_location == "fingerprint":
        fingerprint_root.symlink_to(outside, target_is_directory=True)
    else:
        fingerprint_root.mkdir()
        target = fingerprint_root if link_location == "model-to-parent" else outside
        (fingerprint_root / "vlm").symlink_to(target, target_is_directory=True)
    client, transport = _client(archive, "https://storage.example.invalid/result.zip?signature=test")
    cache = MinerUParseCache(cache_root, client=client, token=_TOKEN, model_version="vlm")

    with pytest.raises(MinerUError, match="cache path"):
        cache.ensure(pdf)

    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert sorted(path.name for path in outside.iterdir()) == ["unrelated.txt"]
    assert transport.requests == []


@pytest.mark.parametrize("model_version", [".", "..", "", "../vlm"])
def test_client_rejects_invalid_model_version(model_version: str) -> None:
    with pytest.raises(MinerUError, match="model version"):
        MinerUClient(_TOKEN, model_version=model_version)


@pytest.mark.parametrize("model_version", ["vlm", "pipeline", "vlm-1.2_test"])
def test_valid_model_keeps_exact_fingerprint_layout_and_reuses_cache(
    pdf: Path, archive: bytes, tmp_path: Path, model_version: str
) -> None:
    client, transport = _client(archive, "https://storage.example.invalid/result.zip?signature=test")
    cache = MinerUParseCache(tmp_path / "cache", client=client, token=_TOKEN, model_version=model_version)

    first = cache.ensure(pdf)
    request_count = len(transport.requests)
    second = cache.ensure(pdf)

    assert first.root == tmp_path / "cache" / file_sha256(pdf) / model_version
    assert second.root == first.root
    assert first.markdown == "# Paper\nParsed evidence"
    assert (first.root / "raw" / "result" / "full.md").read_text(encoding="utf-8") == first.markdown
    assert json.loads(first.manifest_path.read_text(encoding="utf-8"))["mineru_model_version"] == model_version
    assert len(transport.requests) == request_count


@pytest.mark.parametrize("failure", ["directory", "unreadable"])
def test_ensure_many_isolates_real_hash_read_errors(pdf: Path, archive: bytes, tmp_path: Path, failure: str) -> None:
    broken = tmp_path / "broken.pdf"
    if failure == "directory":
        broken.mkdir()
    else:
        shutil.copyfile(pdf, broken)
        broken.chmod(0o000)
    client, transport = _client(archive, "https://storage.example.invalid/result.zip?signature=test")
    cache = MinerUParseCache(tmp_path / "cache", client=client, token=_TOKEN, model_version="vlm")
    progress: list[tuple[str, int, int, int]] = []
    try:
        results = cache.ensure_many([broken, pdf, broken, pdf], lambda *event: progress.append(event))
    finally:
        if failure == "unreadable":
            broken.chmod(0o600)

    assert set(results) == {broken, pdf}
    assert isinstance(results[broken], MinerUError)
    assert isinstance(results[pdf], MinerUParseResult)
    assert cache.get(pdf) is not None
    assert transport.uploads == [pdf.read_bytes()]
    assert [event for event in progress if event[0] == "cache"] == [("cache", 1, 2, 0), ("cache", 2, 2, 0)]


@pytest.mark.parametrize("manifest", [[], None, "manifest", 7, True], ids=["array", "null", "string", "number", "bool"])
def test_non_object_manifest_is_cache_miss_and_can_be_rebuilt(
    pdf: Path, archive: bytes, tmp_path: Path, manifest: Any
) -> None:
    client, transport = _client(archive, "https://storage.example.invalid/result.zip?signature=test")
    cache = MinerUParseCache(tmp_path / "cache", client=client, token=_TOKEN, model_version="vlm")
    first = cache.ensure(pdf)
    first.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    request_count = len(transport.requests)

    assert cache.get(pdf) is None
    repaired = cache.ensure_many([pdf])[pdf]

    assert isinstance(repaired, MinerUParseResult)
    assert repaired.root == first.root
    assert repaired.markdown == "# Paper\nParsed evidence"
    assert isinstance(json.loads(repaired.manifest_path.read_text(encoding="utf-8")), dict)
    assert len(transport.requests) > request_count


@pytest.mark.parametrize("manifest", [[], None, "manifest", 7, True], ids=["array", "null", "string", "number", "bool"])
def test_entries_skips_non_object_manifests(pdf: Path, archive: bytes, tmp_path: Path, manifest: Any) -> None:
    client, _transport = _client(archive, "https://storage.example.invalid/result.zip?signature=test")
    cache = MinerUParseCache(tmp_path / "cache", client=client, token=_TOKEN, model_version="vlm")
    broken = cache.ensure(pdf)
    broken.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    other_pdf = tmp_path / "other.pdf"
    other_pdf.write_bytes(pdf.read_bytes() + b"\n% distinct valid PDF fixture\n")
    good = cache.ensure(other_pdf)

    entries = cache.entries()

    assert entries == [json.loads(good.manifest_path.read_text(encoding="utf-8"))]


@pytest.mark.parametrize("model_version", [0, ["vlm"]], ids=["number", "array"])
def test_model_version_requires_a_string_component(tmp_path: Path, model_version: Any) -> None:
    with pytest.raises(MinerUError, match="model version"):
        MinerUParseCache(tmp_path / "cache", token=_TOKEN, model_version=model_version)
