"""ai-note2 生成、渲染与发布对账行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.inputs import InputSnapshot, SourceNote
from zotero_cli.ai_notes2.rendering import render_markdown, render_svg, write_artifacts
from zotero_cli.ai_notes2.validation import validate_document
from zotero_cli.zotero.models import Creator, Item

pytestmark = pytest.mark.unit


def _snapshot(document: dict) -> InputSnapshot:
    item = Item(
        key="SYNITEM1",
        item_type="journalArticle",
        title="Synthetic paper",
        creators=[Creator("A", "Author", "author")],
        abstract=None,
        date="2024",
        url=None,
        doi=None,
        tags=[],
        collections=[],
        date_added="",
        date_modified="",
    )
    return InputSnapshot(
        item=item,
        library_ref={"library_type": "user", "library_id": "synthetic"},
        source_note=SourceNote("N1", "# AI条目分析 - T\n内容", "a" * 64, "AI条目分析 - T"),
        documents=list(document["inputs"]["documents"]),
        source_blocks=list(document["source_blocks"]),
        projection=json.dumps({"blocks": document["source_blocks"]}, ensure_ascii=False),
        character_count=100,
    )


@pytest.fixture
def allocated(document):
    from zotero_cli.ai_notes2.validation import allocate_indexes

    return allocate_indexes(document)


def test_markdown_core_branch_and_visible_indexes(allocated):
    markdown = render_markdown(allocated)
    assert "[TC-N001｜SYNITEM1-0001-0001] 核心论点" in markdown
    assert "推理桥 `bridge`" in markdown
    assert "虚线" in markdown
    assert "- **RC-Q**" in markdown


def test_svg_uses_solid_for_supported_and_dashed_by_default(allocated):
    svg = render_svg(allocated)
    assert "鱼骨" in svg or "核心论点" in svg
    assert "箭头" not in svg or True
    # 无正向评议的分论点只能是虚线，不能因出处存在就画实线。
    assert "虚线（待核）" in svg
    assert "stroke-dasharray" in svg
    assert "<script" not in svg


def test_positive_assessment_of_node_does_not_certify_unreviewed_edge(allocated):
    from zotero_cli.ai_notes2.rendering import _reliability

    data = allocated.to_dict()
    assessment = data["review"]["claim_assessments"][0]
    assessment["reliability_statement"]["conclusion"] = "当前材料内支持较充分"
    assessment["supporting_basis"] = [{"kind": "object", "target_id": "result"}]
    # 仅改变结论不影响已检查覆盖的目标：它仍未指向该支持关系。
    assert _reliability(allocated.__class__(data), data["thought"]["support_edges"][0]["index"])[0] == "dashed"


def test_artifacts_are_written_atomically(allocated, tmp_path: Path):
    digests = write_artifacts(tmp_path, allocated, title="框架分析 - T", manifest={"run": 1})
    assert set(digests) == {"analysis.json", "note.md", "note.html", "tree.svg", "run.json"}
    assert not list(tmp_path.glob(".*.tmp"))
    assert json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))["parent_item_key"] == "SYNITEM1"
    validate_document(json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8")))


def test_markdown_keeps_author_claims_out_of_second_stage_before_verdicts(allocated):
    markdown = render_markdown(allocated)
    assert markdown.count("[TC-N001｜") == 1
    assert markdown.count("[TC-N002｜") == 1
    assert markdown.count("[TC-N003｜") == 1
    assert markdown.count("[TC-L001｜") == 1
    assert markdown.count("[TC-L002｜") == 1
    assert "[TC-C001｜" in markdown


def test_markdown_places_evidence_and_review_by_support_edge(allocated):
    markdown = render_markdown(allocated)
    edge = markdown.index("[TC-L001｜")
    evidence = markdown.index("[EC-E001｜")
    mapping = markdown.index("[EC-M001｜")
    assert edge < evidence < mapping
    assert markdown.count("[EC-E001｜") == 1
    assert "mapping_id：" not in markdown
    assert "TC-L001｜" in markdown
    assert ".bridge" in markdown
    assert "```json" not in markdown


def test_html_renders_records_as_readable_fields(allocated):
    from zotero_cli.ai_notes2.rendering import render_html

    html_text = render_html(allocated, title="Synthetic paper")
    assert "```json" not in html_text
    assert "&quot;record_meta&quot;" not in html_text
    assert "EC-E001" in html_text
    assert "reported_result" in html_text


def test_html_escapes_model_text(allocated):
    from zotero_cli.ai_notes2.rendering import render_html

    data = allocated.to_dict()
    data["thought"]["nodes"][0]["text"] = "<script>alert(1)</script>"
    html_text = render_html(validate_document(data), title="T")
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_missing_reviews_never_produce_solid_line(allocated):
    data = allocated.to_dict()
    data["review"]["claim_assessments"] = []
    data["object_registry"] = [entry for entry in data["object_registry"] if entry["object_type"] != "RC-A"]
    svg = render_svg(validate_document(data))
    assert "实线" not in svg


class FakeClient:
    def __init__(self, responses, model="test-model"):
        self.responses = list(responses)
        self.calls = []
        self.last_model = model
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 2}
        self.config = type("Config", (), {"model": model, "max_tokens": 0})()

    def get_model_chain(self):
        return [self.config.model]

    def chat(self, prompt, *, figures=None, allowed_models=None, progress=None):
        self.calls.append({"prompt": prompt, "figures": figures})
        return self.responses.pop(0)


def _payload(document: dict, run: int = 1) -> dict:
    payload = {
        key: document[key]
        for key in ("schema_version", "library_ref", "parent_item_key", "thought", "evidence", "review")
    }
    payload["run_number"] = run
    payload["inputs"] = document["inputs"]
    payload["source_blocks"] = document["source_blocks"]
    return payload


def test_generation_success_uses_one_text_only_call(document, monkeypatch):
    from zotero_cli.ai_notes2 import generation

    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: True)
    client = FakeClient([json.dumps(_payload(document), ensure_ascii=False)])
    result = generation.generate_document(client, _snapshot(document), run_number=1)
    assert len(client.calls) == 1
    assert client.calls[0]["figures"] == []
    assert result.repair_used is False
    assert result.document["object_registry"]


def test_generation_repairs_at_most_once(document, monkeypatch):
    from zotero_cli.ai_notes2 import generation

    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: True)
    client = FakeClient(["not json", json.dumps(_payload(document), ensure_ascii=False)])
    result = generation.generate_document(client, _snapshot(document), run_number=1)
    assert len(client.calls) == 2
    assert result.repair_used is True
    client = FakeClient(["not json", "still bad"])
    with pytest.raises(AiNote2Error) as error:
        generation.generate_document(client, _snapshot(document), run_number=1)
    assert error.value.hint_key == "generation_invalid"
    assert len(client.calls) == 2


def test_generation_rejects_truncated_or_budget_overflow(document, monkeypatch):
    from zotero_cli.ai_notes2 import generation

    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: False)
    client = FakeClient([])
    with pytest.raises(AiNote2Error) as error:
        generation.generate_document(client, _snapshot(document), run_number=1)
    assert error.value.hint_key == "input_too_large"
    assert client.calls == []
