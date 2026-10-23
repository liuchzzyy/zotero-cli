"""真实 HTTP 序列化验证两阶段请求顺序，不连接外部服务。"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx2
from tests.support import jpeg_bytes

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.analysis import analyze_item
from zotero_cli.ai_pdf import MinerUParseResult
from zotero_cli.config import AiNoteConfig
from zotero_cli.zotero.models import Item


def test_two_stage_http_keeps_fulltext_out_of_visual_request(tmp_path):
    raw = tmp_path / "raw" / "images"
    raw.mkdir(parents=True)
    (raw / "0.jpg").write_bytes(jpeg_bytes())
    fulltext = "UNIQUE_FULLTEXT_ONLY: electrochemical evidence from control experiments."
    (tmp_path / "document.md").write_text(fulltext)
    (tmp_path / "content_list.json").write_text(
        json.dumps(
            [
                {"type": "text", "text": "UNREFERENCED_PAGE_ONLY: " + "irrelevant long body " * 100, "page_idx": 7},
                {"type": "text", "text": fulltext, "page_idx": 0},
                {
                    "type": "image",
                    "img_path": "images/0.jpg",
                    "image_caption": ["Figure 1: control curve"],
                    "page_idx": 1,
                },
            ]
        )
    )
    parsed = MinerUParseResult(
        source_path=tmp_path / "paper.pdf",
        fingerprint="a" * 64,
        model_version="vlm",
        root=tmp_path,
        markdown_path=tmp_path / "document.md",
        content_list_path=tmp_path / "content_list.json",
        manifest_path=tmp_path / "manifest.json",
    )
    item = Item(
        key="ITEM1",
        item_type="book",
        title="QA",
        creators=[],
        abstract="",
        date="2024",
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="",
        date_modified="",
    )
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            content = json.dumps(
                {
                    "evidence": [
                        {
                            "source": "[ATT1 | main | PDF p.1]",
                            "claim": "control evidence",
                            "conditions": "未知",
                            "data": "未知",
                            "figure_refs": [1],
                        }
                    ],
                    "unresolved": [],
                }
            )
        else:
            content = json.dumps({"sections": [{"type": "paragraph", "text": "Control observation"}] * 12})
        return httpx2.Response(
            200,
            json={
                "id": "qa",
                "object": "chat.completion",
                "created": 0,
                "model": "group/deepseek",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    client = AiClient(AiNoteConfig(api_key="test", base_url="http://127.0.0.1:1/v1", fallback_models=[]))
    client._client._client.close()
    client._client._client = httpx2.Client(transport=httpx2.MockTransport(handle))
    reader = SimpleNamespace(get_item=lambda key: item)
    notes = []
    writer = SimpleNamespace(add_note=lambda key, html: notes.append(html) or "NOTE1")
    try:
        with patch("zotero_cli.ai_notes.analysis.collect_documents", return_value=[("ATT1", "main", parsed)]):
            result = analyze_item(reader, writer, client, "ITEM1", no_tag=True, no_short_note=True)
        assert result["status"] == "ok"
        assert len(notes) == 1
        assert len(requests) == 2
        assert fulltext in requests[0]["messages"][1]["content"]
        parts = requests[1]["messages"][1]["content"]
        assert sum(part["type"] == "image_url" for part in parts) == 1
        assert fulltext in parts[0]["text"]  # 对应来源片段保留，未重发整篇原文。
        assert "source_excerpt" in parts[0]["text"]
        assert "UNREFERENCED_PAGE_ONLY" in requests[0]["messages"][1]["content"]
        assert "UNREFERENCED_PAGE_ONLY" not in parts[0]["text"]
        assert "Figure 1: control curve" in parts[0]["text"]
        assert "control evidence" in parts[0]["text"]
    finally:
        client._client.close()
