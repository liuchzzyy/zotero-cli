"""客户端必须在真实发送边界维持图片与请求体约束。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx2
import pytest

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes.payloads import build_request, request_bytes
from zotero_cli.config import AiNoteConfig


def test_request_bytes_equal_sdk_http_json():
    body = build_request('中文 "反斜线\\"', [(7, "data:image/png;base64,AAAA")], "group/deepseek", AiNoteConfig())
    assert request_bytes(body) == len(httpx2.Request("POST", "http://localhost/v1/chat/completions", json=body).content)


def test_required_images_survive_capability_fallback():
    client = AiClient(AiNoteConfig(api_key="test", base_url="http://127.0.0.1:1/v1"))
    calls = []

    def respond(**body):
        calls.append(body)
        if len(calls) == 1:
            raise RuntimeError("image input is not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    client._client.chat.completions.create = respond
    assert client.chat("prompt", figures=[(1, "data:image/jpeg;base64,AAAA")], require_images=True) == "ok"
    assert len(calls) == 2
    assert all(any(p.get("type") == "image_url" for p in body["messages"][1]["content"]) for body in calls)
    client._client.close()


def test_actual_send_blocks_overlarge_payload(monkeypatch):
    from zotero_cli.ai_notes import evidence

    monkeypatch.setattr(evidence, "MAX_REQUEST_BYTES", 100)
    client = AiClient(AiNoteConfig(api_key="test", base_url="http://127.0.0.1:1/v1", fallback_models=[]))
    send = MagicMock()
    client._client.chat.completions.create = send
    with pytest.raises(NoteAnalysisError, match="request body"):
        client.chat("文献" * 100)
    assert send.call_count == 0
    client._client.close()
