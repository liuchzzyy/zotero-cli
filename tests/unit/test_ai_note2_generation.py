"""ai-note2 生成、渲染与发布对账行为。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.inputs import InputSnapshot, SourceNote
from zotero_cli.ai_notes2.rendering import (
    render_chain_markdown,
    render_markdown,
    render_overview_markdown,
    render_svg,
    write_artifacts,
)
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


def test_missing_required_node_field_returns_repair_error(document):
    from zotero_cli.ai_notes2.generation import _candidate_errors

    snapshot = _snapshot(document)
    payload = _payload(document)
    del payload["thought"]["nodes"][0]["node_type"]
    result, errors = _candidate_errors(json.dumps(payload, ensure_ascii=False), snapshot, 1)
    assert result is None
    assert any("node_type" in error for error in errors)


def test_unlinked_evidence_keeps_its_review_and_global_ids(allocated):
    from zotero_cli.ai_notes2.reading import build_reading_sections

    data = allocated.to_dict()
    data["evidence"]["mappings"] = []
    evidence = data["evidence"]["evidence_records"][0]
    review = data["review"]["review_items"][0]
    text = "\n".join(text for _, text in build_reading_sections(allocated.__class__(data), None))
    assert evidence["index"] in text
    assert review["index"] in text
    assert text.index(evidence["evidence_id"]) < text.index(review["review_id"])


def test_svg_displays_linked_evidence_and_review(allocated):
    data = allocated.to_dict()
    svg = render_svg(allocated)
    assert data["evidence"]["evidence_records"][0]["index"] in svg
    assert data["review"]["review_items"][0]["index"] in svg


def test_svg_does_not_mark_conditional_review_as_positive(allocated):
    from zotero_cli.ai_notes2.rendering import _reliability

    data = allocated.to_dict()
    edge = data["thought"]["support_edges"][0]
    review = data["review"]["review_items"][0]
    review["primary_target_ref"] = {"kind": "object", "target_id": edge["edge_id"], "target_field": None}
    review["finding_status"] = "存在条件性疑点"
    coverage = data["review"]["coverage_records"][0]
    coverage["target_refs"] = [review["primary_target_ref"]]
    assessment = data["review"]["claim_assessments"][0]
    assessment["unassessed_parts"] = []
    assessment["reliability_statement"]["conclusion"] = "有支持但须限定范围"
    assessment["supporting_basis"] = [{"ref": {"kind": "object", "target_id": "EC-E001", "target_field": None}}]
    assert _reliability(allocated.__class__(data), edge["index"])[0] == "dashed"


def test_svg_accepts_review_targeting_a_source(document):
    from zotero_cli.ai_notes2.validation import allocate_indexes

    review = document["review"]["review_items"][0]
    source = review["basis_refs"][0]["ref"]
    review["primary_target_ref"] = source
    document["review"]["coverage_records"][0]["target_refs"] = [source]
    document["review"]["coverage_scope"]["units"][0]["target_refs"] = [source]
    allocated = allocate_indexes(document)
    from zotero_cli.ai_notes2.reading import build_reading_sections

    rendered = "\n".join(text for _, text in build_reading_sections(allocated, None))
    assert "定点审查 RC-Q001" in rendered
    assert "SYNATT01 / B001" in rendered
    svg = render_svg(allocated)
    assert "RC-Q001" in svg
    assert "B001" in svg


def test_compact_note_and_thought_view_keep_visible_identities(allocated):
    markdown = render_markdown(allocated)
    thought = render_chain_markdown(allocated, "thought")
    assert '## <span style="color:#17365D">核心论点</span>' in markdown
    assert "作者推理" in markdown
    assert "报告材料" in markdown
    assert "定点审查" in markdown
    assert markdown.index("TC-N002") < markdown.index("EC-E001") < markdown.index("RC-Q001")
    assert "TC-L001.bridge" in markdown
    assert "未有针对该命题的综合评议" in markdown
    assert "#17365D" in markdown
    assert "[TC-N001｜SYNITEM1-0001-0001]" in thought
    assert "`bridge`" in thought
    assert "argument_support:TC-L001" in thought


def test_svg_uses_solid_for_supported_and_dashed_by_default(allocated):
    svg = render_svg(allocated)
    assert "鱼骨" in svg or "核心论点" in svg
    assert "箭头" not in svg or True
    # 无正向评议的分论点只能是虚线，不能因出处存在就画实线。
    assert "虚线（待核）" in svg
    assert "stroke-dasharray" in svg
    assert "<script" not in svg


def test_svg_reasons_do_not_overlap_horizontally_or_vertically(allocated):
    svg = render_svg(allocated)
    import re

    # 提取所有理由文本的 y 坐标
    coords = [
        float(y)
        for y in re.findall(r'<text x="[0-9.]+" y="([0-9.]+)" font-size="9">SYNITEM1-0001-[0-9]+ 理由</text>', svg)
    ]
    if len(coords) >= 2:
        assert len(coords) == len(set(coords))


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
    assert set(digests) == {
        "analysis.json",
        "object-index.json",
        "overview.md",
        "thought.md",
        "evidence.md",
        "review.md",
        "note.md",
        "note.html",
        "tree.svg",
        "run.json",
    }
    assert not list(tmp_path.glob(".*.tmp"))
    assert json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8"))["parent_item_key"] == "SYNITEM1"
    validate_document(json.loads((tmp_path / "analysis.json").read_text(encoding="utf-8")))
    object_index = json.loads((tmp_path / "object-index.json").read_text(encoding="utf-8"))
    assert object_index["source"]["parent_item_key"] == "SYNITEM1"
    assert object_index["validation_scope"]["model_source_assessment"] == "absent"


def test_split_views_define_each_chain_object_once(allocated):
    thought = render_chain_markdown(allocated, "thought")
    assert thought.count("## [TC-N001｜") == 1
    assert thought.count("## [TC-N002｜") == 1
    assert thought.count("## [TC-N003｜") == 1
    assert thought.count("## [TC-L001｜") == 1
    assert thought.count("## [TC-L002｜") == 1
    assert "## [TC-C001｜" in thought


def test_overview_projects_typed_cross_chain_relations_without_merging_chains(allocated):
    overview = render_overview_markdown(allocated)
    evidence = render_chain_markdown(allocated, "evidence")
    review = render_chain_markdown(allocated, "review")
    assert "evidence_use:EC-M001" in overview
    assert "EC-E001 → TC-L001.bridge" in overview
    assert evidence.count("## [EC-E001｜") == 1
    assert "## [TC-N001｜" not in evidence
    assert review.count("## [RC-Q001｜") == 1
    assert "```json" not in overview


def test_html_renders_records_as_readable_fields(allocated):
    from zotero_cli.ai_notes2.rendering import render_html

    html_text = render_html(allocated, title="Synthetic paper")
    assert "```json" not in html_text
    assert "&quot;record_meta&quot;" not in html_text
    assert "EC-E001" in html_text
    assert "报告材料" in html_text
    assert "核心论点" in html_text
    assert "SYNITEM1-0001-" in html_text
    assert "<script" not in html_text


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


def test_normalization_does_not_rewrite_scientific_scope_or_assign_sources(document):
    from copy import deepcopy

    from zotero_cli.ai_notes2.generation import _normalize_candidate

    data = deepcopy(document)
    item = data["review"]["review_items"][0]
    item["review_scope"] = "作者描述的局部范围"
    assessment = data["review"]["claim_assessments"][0]
    assessment["reliability_statement"]["scope"] = "受限于特定条件"
    status = data["evidence"]["evidence_records"][0]["field_statuses"][0]
    status.update(status="已定位", source_refs=[])
    _normalize_candidate(data)
    assert item["review_scope"] == "作者描述的局部范围"
    assert assessment["reliability_statement"]["scope"] == "受限于特定条件"
    assert status["source_refs"] == []


def test_generation_retries_truncated_output_without_publishing_partial(document, monkeypatch):
    from zotero_cli.ai_notes.errors import OutputTruncatedError
    from zotero_cli.ai_notes2 import generation

    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: True)

    class TruncatedClient(FakeClient):
        def chat(self, prompt, *, figures=None, allowed_models=None, progress=None):
            self.calls.append({"prompt": prompt, "allowed_models": allowed_models})
            if len(self.calls) == 1:
                raise OutputTruncatedError(response_text='{"thought":')
            return json.dumps(_payload(document), ensure_ascii=False)

    client = TruncatedClient([])
    result = generation.generate_document(client, _snapshot(document), run_number=1)
    assert len(client.calls) == 2
    assert "完整" in client.calls[1]["prompt"]
    assert "条件" in client.calls[1]["prompt"]
    assert result.document["object_registry"]


def test_generation_success_uses_one_text_only_call(document, monkeypatch):
    from zotero_cli.ai_notes2 import generation

    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: True)
    client = FakeClient([json.dumps(_payload(document), ensure_ascii=False)])
    result = generation.generate_document(client, _snapshot(document), run_number=1)
    assert len(client.calls) == 1
    assert json.dumps(_snapshot(document).library_ref, ensure_ascii=False) in client.calls[0]["prompt"]
    assert '"Location"' in client.calls[0]["prompt"]
    assert client.calls[0]["figures"] == []
    assert result.repair_used is False
    assert result.document["object_registry"]


def test_generation_repairs_within_configured_attempts(document, monkeypatch):
    """修复轮次由 repair_attempts 决定；成功即停止，耗尽则报错。"""
    from zotero_cli.ai_notes2 import generation
    from zotero_cli.config.runtime import section

    attempts = int(section("ai_notes2")["repair_attempts"])
    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: True)
    client = FakeClient(["not json", json.dumps(_payload(document), ensure_ascii=False)])
    result = generation.generate_document(client, _snapshot(document), run_number=1)
    assert len(client.calls) == 2
    assert result.repair_used is True

    client = FakeClient(["not json"] + ["still bad"] * attempts)
    with pytest.raises(AiNote2Error) as error:
        generation.generate_document(client, _snapshot(document), run_number=1)
    assert error.value.hint_key == "generation_invalid"
    assert len(client.calls) == attempts + 1


def test_repair_keeps_failed_json_in_the_correction_request(document, monkeypatch):
    from zotero_cli.ai_notes2 import generation

    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: True)
    bad = _payload(document)
    bad["thought"]["nodes"][0]["claim_kind"] = "invalid_enum"
    raw = json.dumps(bad, ensure_ascii=False)
    fixed = _payload(document)
    fixed["thought"]["nodes"][0]["claim_kind"] = "描述"
    client = FakeClient([raw, json.dumps(fixed, ensure_ascii=False)])
    result = generation.generate_document(client, _snapshot(document), run_number=1)
    assert result.repair_used
    assert raw in client.calls[1]["prompt"]
    assert "/thought/nodes/0/claim_kind" in client.calls[1]["prompt"]
    # 修复请求必须明确禁止回显或拼接上一次输出，否则模型会输出两个 JSON 对象。
    repair = client.calls[1]["prompt"]
    assert "不得先重复原 JSON" in repair
    assert "从 `{` 开始、到 `}` 结束" in repair


def test_template_marks_type_dictionary_as_not_output_fields():
    from zotero_cli.ai_notes2 import templates

    template = templates.load_template()
    assert "类型字典，不是要输出的字段" in template
    assert "不要输出 `records`、`shared_types` 或 `$ref` 本身" in template


def test_prompt_lists_referenceable_figure_ids_per_attachment(document):
    """图号白名单必须按附件列出，模型才能逐字复制而不是自行推断编号。"""
    from zotero_cli.ai_notes2 import generation

    prompt = generation.build_prompt(_snapshot(document))
    assert "可引用图号" in prompt
    assert "source_set_id=`set-1`，attachment_key=`SYNATT01`：`1`" in prompt


def test_prompt_states_no_figures_when_catalog_is_empty(document):
    from zotero_cli.ai_notes2 import generation

    snapshot = _snapshot(document)
    snapshot.source_blocks[0]["figure_ids"] = []
    prompt = generation.build_prompt(snapshot)
    assert "没有任何可引用图号" in prompt
    assert "所有 figure_refs 必须为空数组" in prompt


def test_generation_rejects_truncated_or_budget_overflow(document, monkeypatch):
    from zotero_cli.ai_notes2 import generation

    monkeypatch.setattr(generation, "model_supports_request", lambda *args, **kwargs: False)
    client = FakeClient([])
    with pytest.raises(AiNote2Error) as error:
        generation.generate_document(client, _snapshot(document), run_number=1)
    assert error.value.hint_key == "input_too_large"
    assert client.calls == []
