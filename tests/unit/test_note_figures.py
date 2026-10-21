"""图片以载荷形式送达模型；纯文本模型应降级而不是失败。"""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from zotero_cli.ai_notes.client import TEXT_ONLY_NOTICE, AiClient
from zotero_cli.config import AiNoteConfig

_FIGURE = [(1, "data:image/png;base64,AAA")]


class _FakeCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))  # the client mutates kwargs in place on retry
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _response(outcome)


def _response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=None,
        model="m1",
    )


def _client(outcomes):
    """返回一个按 `outcomes`（文本或异常）重放的传输层客户端。"""
    client = AiClient(AiNoteConfig(model="m1", api_key="k", base_url="http://127.0.0.1:1/v1"))
    completions = _FakeCompletions(outcomes)
    client._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


class TestAiClientFigures:
    def test_text_only_prompt_stays_a_string(self):
        client, completions = _client(["ok"])
        client.chat("prompt")
        assert completions.calls[0]["messages"][1]["content"] == "prompt"

    def test_figures_become_image_parts_in_order(self):
        client, completions = _client(["ok"])
        client.chat("prompt", figures=_FIGURE)
        content = completions.calls[0]["messages"][1]["content"]
        assert content[0] == {"type": "text", "text": "prompt"}
        assert content[1]["type"] == "text" and "图 1" in content[1]["text"]
        assert content[2] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}

    def test_generic_image_mention_does_not_trigger_text_only_retry(self):
        # 载荷体积类错误可能提到 "image"，但这并不代表模型不支持图片；
        # 此时静默丢弃图片会掩盖真正的失败原因。
        client, completions = _client([Exception("Request too large: image size exceeds provider limit")])
        client.config.fallback_models = []
        with pytest.raises(Exception):
            client.chat("prompt", figures=_FIGURE)
        assert len(completions.calls) == 1
        assert isinstance(completions.calls[0]["messages"][1]["content"], list)

    def test_generic_content_type_error_does_not_trigger_text_only_retry(self):
        client, completions = _client([Exception("400 Unsupported content type: application/octet-stream")])
        client.config.fallback_models = []
        with pytest.raises(Exception):
            client.chat("prompt", figures=_FIGURE)
        assert len(completions.calls) == 1
        assert isinstance(completions.calls[0]["messages"][1]["content"], list)

    def test_text_only_model_retries_without_images(self):
        rejection = Exception("BadRequestError: image input is not supported")
        client, completions = _client([rejection, "degraded ok"])
        assert client.chat("prompt", figures=_FIGURE) == "degraded ok"
        assert isinstance(completions.calls[0]["messages"][1]["content"], list)
        # 所有文本块都保留，只丢弃图片部分。
        assert completions.calls[1]["messages"][1]["content"] == "prompt" + TEXT_ONLY_NOTICE

    def test_unrelated_error_skips_the_image_retry_and_uses_fallback(self):
        client, completions = _client([Exception("rate limited"), "from m2"])
        client.config.fallback_models = ["m2"]
        assert client.chat("prompt", figures=_FIGURE) == "from m2"
        # 不重试纯文本：首个错误与图片毫无关系
        assert len(completions.calls) == 2
        assert [c["model"] for c in completions.calls] == ["m1", "m2"]
        assert isinstance(completions.calls[1]["messages"][1]["content"], list)

    def test_fallback_model_still_gets_figures_after_a_text_only_one(self):
        rejection = Exception("image input is not supported")
        client, completions = _client([rejection, Exception("boom"), "from m2"])
        client.config.fallback_models = ["m2"]
        assert client.chat("prompt", figures=_FIGURE) == "from m2"
        # m1 先带图片再退化为纯文本；m2 仍须收到图片部分
        assert isinstance(completions.calls[0]["messages"][1]["content"], list)
        assert completions.calls[1]["messages"][1]["content"] == "prompt" + TEXT_ONLY_NOTICE
        assert isinstance(completions.calls[2]["messages"][1]["content"], list)
