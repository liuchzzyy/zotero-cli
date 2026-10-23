"""真实 requests 调用链的下载预算与响应释放回归。"""

import io

import pytest
from requests import Response, Session
from requests.adapters import BaseAdapter
from requests.exceptions import ConnectionError

from zotero_cli.ai_pdf.access import client as client_module
from zotero_cli.ai_pdf.access.client import MinerUClient
from zotero_cli.ai_pdf.errors import MinerUError


class DownloadBody(io.BytesIO):
    def release_conn(self):
        self.close()


class DownloadAdapter(BaseAdapter):
    """替换网络传输，保留 Session 的默认响应体消费行为。"""

    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.bodies = []
        self.responses = []
        self.stream_flags = []

    def send(self, request, **kwargs):
        self.stream_flags.append(kwargs.get("stream"))
        status = next(self.statuses)
        response = Response()
        response.status_code = status
        response.request = request
        response.url = request.url
        body = DownloadBody(b"oversized archive")
        response.raw = body
        response.headers["Content-Length"] = "9999"
        if status == 302:
            response.headers["Location"] = "https://download.invalid/final.zip"
            response.headers["Content-Length"] = "0"
            body = DownloadBody(b"")
            response.raw = body
        self.bodies.append(body)
        self.responses.append(response)
        return response

    def close(self):
        pass


@pytest.mark.parametrize("statuses", [[200], [302, 200]])
def test_download_rejects_declared_oversize_without_prereading_body(monkeypatch, statuses):
    adapter = DownloadAdapter(statuses)
    session = Session()
    session.trust_env = False
    session.mount("https://", adapter)
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_BYTES", 100)
    with session:
        with pytest.raises(MinerUError, match="exceeds"):
            MinerUClient("synthetic", session=session)._download_archive("https://download.invalid/result.zip")
        assert all(adapter.stream_flags)
        assert adapter.responses[-1]._content is False
        assert all(body.closed for body in adapter.bodies)


def test_download_closes_error_response_without_consuming_unbounded_text():
    adapter = DownloadAdapter([500])
    with Session() as session:
        session.trust_env = False
        session.mount("https://", adapter)
        with pytest.raises(MinerUError, match="HTTP 500"):
            MinerUClient("synthetic", session=session)._download_archive("https://download.invalid/result.zip")
        assert adapter.responses[0]._content is False
        assert adapter.bodies[0].closed


def test_stream_retry_uses_streaming_and_closes_both_responses(monkeypatch):
    adapter = DownloadAdapter([200, 200])
    original = client_module._read_limited_body
    attempts = 0

    def read(response):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("interrupted")
        return original(response)

    monkeypatch.setattr(client_module, "_read_limited_body", read)
    monkeypatch.setattr(client_module, "_MAX_ARCHIVE_BYTES", 10000)
    with Session() as session:
        session.trust_env = False
        session.mount("https://", adapter)
        result = MinerUClient("synthetic", session=session)._download_archive("https://download.invalid/result.zip")
        assert result == b"oversized archive"
        assert adapter.stream_flags == [True, True]
        assert all(body.closed for body in adapter.bodies)
