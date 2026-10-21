"""单次请求、带来源标注的 MinerU 笔记证据回归测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from zotero_cli.ai_notes.analysis import analyze_item
from zotero_cli.ai_notes.evidence import (
    build_evidence,
    model_supports_request,
    plan_request,
    plan_with_coverage,
)
from zotero_cli.ai_pdf import MinerUParseResult
from zotero_cli.config import AiNoteConfig
from zotero_cli.zotero.models import Attachment, Creator, Item


def _parse(tmp_path: Path, name: str, blocks: list[dict], *, image_count: int = 0):
    root = tmp_path / name
    images = root / "raw" / "images"
    images.mkdir(parents=True)
    for i in range(image_count):
        (images / f"{i}.jpg").write_bytes(b"\xff\xd8\xff" + b"a" * 5000)
    return SimpleNamespace(root=root, content_list=blocks, fingerprint=name)


def test_evidence_preserves_table_source_page_and_image_order(tmp_path):
    main = _parse(
        tmp_path,
        "main",
        [
            {"type": "header", "text": "Publisher", "page_idx": 0},
            {"type": "text", "text": "Results", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "Main finding", "page_idx": 0},
            {"type": "chart", "img_path": "images/0.jpg", "chart_caption": ["Figure 1."], "page_idx": 1},
            {"type": "image", "img_path": "images/1.jpg", "image_caption": ["Figure 2."], "page_idx": 2},
        ],
        image_count=2,
    )
    si = _parse(
        tmp_path,
        "si",
        [
            {"type": "text", "text": "Methods", "text_level": 1, "page_idx": 0},
            {
                "type": "table",
                "table_caption": ["Table S8"],
                "table_body": '<table><tr><td colspan="2">Cathode</td></tr><tr><td>0.55</td><td>9.16</td></tr></table>',
                "page_idx": 61,
            },
            {"type": "image", "img_path": "images/0.jpg", "image_caption": ["Figure S1"], "page_idx": 3},
        ],
        image_count=1,
    )
    evidence = build_evidence([("MAIN", "main", main), ("SI", "supplementary", si)])
    assert "Publisher" not in evidence.text
    assert "[MAIN | main | PDF p.1 | Results] Main finding" in evidence.text
    assert '<td colspan="2">Cathode</td>' in evidence.text
    assert "[SI | supplementary | PDF p.62 | Methods] Table S8" in evidence.text
    assert "【图 1】" in evidence.text and "【图 3】" in evidence.text
    assert [n for n, _ in evidence.main_images] == [1, 2]
    assert [n for n, _ in evidence.si_candidates] == [3]


def test_more_than_thirty_si_images_are_sampled_not_only_the_front(tmp_path):
    si = _parse(
        tmp_path,
        "si",
        [
            {"type": "chart", "img_path": f"images/{i}.jpg", "chart_caption": [f"Figure S{i + 1}"], "page_idx": i}
            for i in range(45)
        ],
        image_count=45,
    )
    evidence = build_evidence([("SI", "supplementary", si)])
    plan = plan_request(evidence, "Prompt " + evidence.text, "deepseek-flash")
    assert len(plan.si_images) == 30
    assert plan.si_images[-1][0] > 30
    assert len(plan.images) == 30
    assert "未附图像" not in evidence.text


def test_plan_never_truncates_required_text_or_main_images(tmp_path):
    main = _parse(tmp_path, "m", [{"type": "chart", "img_path": "images/0.jpg", "page_idx": 0}], image_count=1)
    evidence = build_evidence([("MAIN", "main", main)])
    with pytest.raises(ValueError, match="required text"):
        plan_request(evidence, "X" * 600_000, "deepseek-flash")
    (main.root / "raw" / "images" / "0.jpg").unlink()
    with pytest.raises(ValueError, match="main image"):
        plan_request(evidence, evidence.text, "deepseek-flash")


def test_unknown_route_cannot_claim_verified_500k_budget(tmp_path):
    evidence = build_evidence(
        [("MAIN", "main", _parse(tmp_path, "m", [{"type": "text", "text": "body", "page_idx": 0}]))]
    )
    with pytest.raises(ValueError, match="unverified"):
        plan_request(evidence, evidence.text, "unknown-proxy-model")


def test_supported_routes_include_gpt_5_6_sol(tmp_path):
    evidence = build_evidence(
        [("MAIN", "main", _parse(tmp_path, "m", [{"type": "text", "text": "body", "page_idx": 0}]))]
    )
    plan = plan_request(evidence, evidence.text, "gpt-5.6-sol")
    assert plan.model == "gpt-5.6-sol"


def test_missing_si_image_is_skipped_not_fatal(tmp_path):
    # 缺失的补充材料图片应标记为跳过，而不是中断整个计划
    si = _parse(tmp_path, "si", [{"type": "image", "img_path": "images/0.jpg", "page_idx": 0}], image_count=1)
    # 删除已创建的图片文件，模拟磁盘上缺少补充材料图片
    (si.root / "raw" / "images" / "0.jpg").unlink()
    main = _parse(tmp_path, "main", [{"type": "text", "text": "body", "page_idx": 0}])
    evidence = build_evidence([("MAIN", "main", main), ("SI", "supplementary", si)])
    plan = plan_request(evidence, "prompt", "deepseek-flash")
    assert 1 in plan.skipped
    assert len(plan.images) == 0


def test_missing_main_image_raises_error(tmp_path):
    main = _parse(tmp_path, "main", [{"type": "image", "img_path": "images/0.jpg", "page_idx": 0}], image_count=1)
    (main.root / "raw" / "images" / "0.jpg").unlink()
    evidence = build_evidence([("MAIN", "main", main)])
    with pytest.raises(ValueError, match="required main image 1 is unavailable"):
        plan_request(evidence, "prompt", "deepseek-flash")


def test_fallback_budget_accepts_exact_image_boundary_but_not_overflow():
    prompt = "x" * (500_000 - 32_768 - 50_000 - 4_096)
    assert model_supports_request("deepseek-flash", prompt, 1)
    assert not model_supports_request("deepseek-flash", prompt, 2)
    assert not model_supports_request("unverified-model", prompt, 1)


def test_coverage_notice_replans_when_its_own_text_removes_an_si_image(tmp_path):
    si = _parse(
        tmp_path,
        "si",
        [
            {"type": "image", "img_path": "images/0.jpg", "page_idx": 0},
            {"type": "image", "img_path": "images/1.jpg", "page_idx": 0},
        ],
        image_count=2,
    )
    evidence = build_evidence([("SI", "supplementary", si)])
    prompt = "x" * (500_000 - 32_768 - 50_000 - 4_096)
    plan, final_prompt, omitted = plan_with_coverage(evidence, prompt, "deepseek-flash")
    assert not plan.images
    assert omitted == [1, 2]
    assert "【图 1】" in final_prompt and "【图 2】" in final_prompt


def test_si_budget_drops_all_images_only_when_required_text_fits(tmp_path):
    si = _parse(tmp_path, "si", [{"type": "image", "img_path": "images/0.jpg", "page_idx": 0}], image_count=1)
    evidence = build_evidence([("SI", "supplementary", si)])
    plan = plan_request(evidence, "x" * 416_900, "deepseek-flash")
    assert plan.images == []
    assert "【图 1】" in evidence.text
    assert "未附图像" not in evidence.text


def test_image_budget_does_not_undercount_a_large_image(tmp_path):
    main = _parse(tmp_path, "m", [{"type": "image", "img_path": "images/0.jpg", "page_idx": 0}], image_count=1)
    evidence = build_evidence([("M", "main", main)])
    with pytest.raises(ValueError, match="required main images"):
        plan_request(evidence, "x" * 416_000, "deepseek-flash")


def test_image_block_without_a_path_is_rejected_at_build_time(tmp_path):
    parsed = _parse(tmp_path, "m", [{"type": "image", "page_idx": 0}], image_count=0)
    with pytest.raises(ValueError, match="image block without img_path"):
        build_evidence([("M", "main", parsed)])


def test_degenerate_spacer_image_is_skipped_and_reported_not_fatal(tmp_path):
    # MinerU 会输出 1x1 的占位图；151 px 的分图约 4 kB，因此只有不足 1 kB 的
    # 文件才被视为退化产物。丢弃时必须显式告知，并且
    # 绝不能中断本可正常使用的分析。
    parsed = _parse(tmp_path, "m", [{"type": "image", "img_path": "images/0.jpg", "page_idx": 0}], image_count=0)
    normal = parsed.root / "raw" / "images" / "0.jpg"
    normal.write_bytes(b"\xff\xd8\xff" + b"a" * 5000)
    small = parsed.root / "raw" / "images" / "1.jpg"
    small.write_bytes(b"\xff\xd8\xff" + b"a" * 200)
    parsed.content_list = [
        {"type": "image", "img_path": "images/0.jpg", "page_idx": 0},
        {"type": "image", "img_path": "images/1.jpg", "page_idx": 0},
    ]
    evidence = build_evidence([("M", "main", parsed)])
    plan = plan_request(evidence, "prompt", "deepseek-flash")
    assert [number for number, _ in plan.images] == [1]
    assert plan.skipped == [2]


def test_supplementary_images_referenced_by_main_are_prioritized(tmp_path):
    main = _parse(tmp_path, "m", [{"type": "text", "text": "As seen in Figure S45 and Figure S44", "page_idx": 0}])
    si = _parse(
        tmp_path,
        "si",
        [
            {"type": "chart", "img_path": f"images/{i}.jpg", "chart_caption": [f"Figure S{i + 1}."], "page_idx": i}
            for i in range(45)
        ],
        image_count=45,
    )
    evidence = build_evidence([("M", "main", main), ("SI", "supplementary", si)])
    plan = plan_request(evidence, evidence.text, "deepseek-flash")
    assert len(plan.si_images) == 30
    assert {44, 45}.issubset({n for n, _ in plan.si_images})


def test_parse_retry_keeps_full_image_set_until_explicit_text_only_fallback(tmp_path):
    parsed = _parse(tmp_path, "m", [{"type": "image", "img_path": "images/0.jpg", "page_idx": 0}], image_count=1)
    evidence = build_evidence([("M", "main", parsed)])
    assert len(plan_request(evidence, evidence.text, "deepseek-flash").images) == 1


def test_real_client_rejects_truncated_output(monkeypatch):
    from zotero_cli.ai_notes.access.client import AiClient
    from zotero_cli.config import AiNoteConfig

    config = AiNoteConfig(api_key="test", base_url="http://127.0.0.1:1/v1", model="deepseek-flash")
    client = AiClient(config)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"sections": []}'), finish_reason="length")],
        usage=SimpleNamespace(prompt_tokens=123, completion_tokens=9),
    )
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: response)
    with pytest.raises(RuntimeError, match="truncated"):
        client.chat("prompt")


def test_real_client_records_usage_without_exposing_the_payload(monkeypatch):
    from zotero_cli.ai_notes.access.client import AiClient
    from zotero_cli.config import AiNoteConfig

    config = AiNoteConfig(api_key="test", base_url="http://127.0.0.1:1/v1", model="deepseek-flash")
    client = AiClient(config)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="OK"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=123, completion_tokens=9),
    )
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: response)
    assert client.chat("prompt") == "OK"
    assert client.last_usage == {"prompt_tokens": 123, "completion_tokens": 9}


def test_long_note_fallback_uses_only_preflighted_routes(monkeypatch):
    from zotero_cli.ai_notes.access.client import AiClient
    from zotero_cli.ai_notes.evidence import model_supports_request

    config = AiNoteConfig(
        api_key="test",
        base_url="http://127.0.0.1:1/v1",
        model="deepseek-flash",
        fallback_models=["unverified-model", "gemini-3.8-flash-high"],
    )
    client = AiClient(config)
    attempted: list[str] = []

    def fake_create(**kwargs):
        attempted.append(kwargs["model"])
        if kwargs["model"] == "deepseek-flash":
            raise RuntimeError("provider temporarily unavailable")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"), finish_reason="stop")],
            usage=None,
        )

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    prompt = "x" * 200_000
    allowed = {model for model in client.get_model_chain() if model_supports_request(model, prompt, 10)}
    assert client.chat(prompt, allowed_models=allowed) == "OK"
    assert attempted == ["deepseek-flash", "gemini-3.8-flash-high"]


def test_image_rejection_drops_all_images_but_keeps_the_whole_text(monkeypatch):
    from zotero_cli.ai_notes.access.client import AiClient
    from zotero_cli.config import AiNoteConfig

    config = AiNoteConfig(api_key="test", base_url="http://127.0.0.1:1/v1", model="deepseek-flash", fallback_models=[])
    client = AiClient(config)
    calls: list[dict] = []

    def fake_create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("This model does not support image input")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"), finish_reason="stop")],
            usage=None,
        )

    monkeypatch.setattr(client._client.chat.completions, "create", fake_create)
    answer = client.chat("FULL-EVIDENCE-TEXT", figures=[(1, "data:image/jpeg;base64,AAAA")])
    assert answer == "OK"
    retried = calls[1]["messages"][1]["content"]
    assert isinstance(retried, str)
    assert "FULL-EVIDENCE-TEXT" in retried
    assert "本次请求未附任何图片" in retried


def test_usage_history_records_every_call(monkeypatch):
    from zotero_cli.ai_notes.access.client import AiClient
    from zotero_cli.config import AiNoteConfig

    config = AiNoteConfig(api_key="test", base_url="http://127.0.0.1:1/v1", model="deepseek-flash", fallback_models=[])
    client = AiClient(config)
    responses = [
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="one"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=1),
            model="deepseek-flash",
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="two"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=20, completion_tokens=2),
            model="deepseek-flash",
        ),
    ]
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: responses.pop(0))
    client.chat("first")
    client.chat("second")
    assert [entry["prompt_tokens"] for entry in client.usage_history] == [10, 20]


def test_pipeline_uses_structured_blocks_and_does_not_write_on_preview(tmp_path):
    parsed = _parse(
        tmp_path,
        "main",
        [
            {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0},
            {"type": "text", "text": "Verified evidence", "page_idx": 0},
            {"type": "chart", "img_path": "images/0.jpg", "chart_caption": ["Figure 1"], "page_idx": 1},
        ],
        image_count=1,
    )
    (parsed.root / "document.md").write_text("WRONG-MARKDOWN", encoding="utf-8")
    (parsed.root / "content_list.json").write_text(json.dumps(parsed.content_list), encoding="utf-8")
    (parsed.root / "manifest.json").write_text("{}", encoding="utf-8")
    pdf = parsed.root / "main.pdf"
    pdf.write_bytes(b"%PDF dummy")
    actual = MinerUParseResult(
        source_path=pdf,
        fingerprint="a" * 64,
        model_version="vlm",
        root=parsed.root,
        markdown_path=parsed.root / "document.md",
        content_list_path=parsed.root / "content_list.json",
        manifest_path=parsed.root / "manifest.json",
    )
    att = Attachment("MAIN", "ABC123", "main.pdf", "application/pdf", pdf)
    item = Item(
        key="ABC123",
        item_type="journalArticle",
        title="Test Paper",
        creators=[Creator("A", "B", "author")],
        abstract="",
        date="2026",
        url=None,
        doi="10.1234/test",
        tags=[],
        collections=[],
        date_added="2026",
        date_modified="2026",
        extra={},
    )
    reader = MagicMock()
    reader.get_item.return_value = item
    reader.get_pdf_attachments.return_value = [att]
    writer = MagicMock()
    ai_client = MagicMock()
    ai_client.config = AiNoteConfig(api_key="k", base_url="http://x", model="deepseek-flash")
    with patch("zotero_cli.ai_notes.documents.MinerUParseCache.ensure_many", return_value={pdf: actual}):
        result = analyze_item(reader, writer, ai_client, "ABC123", dry_run=True)
    assert result["status"] == "dry_run"
    assert result["main_images"] == 1
    assert "Verified evidence" in result["prompt_preview"]
    assert "WRONG-MARKDOWN" not in result["prompt_preview"]
    writer.add_note.assert_not_called()
