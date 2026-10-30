"""ai-note2 派生索引、类型关系与分链视图。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from zotero_cli.ai_notes2.indexing import build_object_index
from zotero_cli.ai_notes2.rendering import render_chain_markdown, render_html, render_overview_markdown
from zotero_cli.ai_notes2.validation import (
    Note2ValidationError,
    allocate_indexes,
    validate_document,
    validate_object_index,
)

pytestmark = pytest.mark.unit


def _ref(identifier: str, field: str | None = None) -> dict[str, str | None]:
    return {"kind": "object", "target_id": identifier, "target_field": field}


def _meta() -> dict:
    return {"state": "active", "replaces": [], "replaced_by": [], "note": None}


def _expanded_document(document: dict) -> dict:
    data = deepcopy(document)
    data["inputs"]["documents"].append(
        {
            "source_set_id": "set-2",
            "attachment_key": "SYNSI001",
            "attachment_aliases": [],
            "pdf_sha256": "d" * 64,
            "pdf_role": "SI",
            "document_identity_status": "身份未明确",
            "role_basis": "合成 fixture 的第二份附件",
            "parse_metadata": {
                "parser": "synthetic",
                "parser_version": None,
                "adapter_version": "1",
                "parsed_at": None,
                "parse_artifact_digest": "e" * 64,
            },
        }
    )
    data["source_blocks"].append(
        {
            "source_set_id": "set-2",
            "attachment_key": "SYNSI001",
            "block_id": "B001",
            "block_type": "caption",
            "pdf_role": "SI",
            "page_pdf": 2,
            "page_label": None,
            "section_path": ["Synthetic SI"],
            "text": "合成 SI 结果：条件未报告；图 S1。",
            "table": None,
            "figure_ids": ["S1"],
        }
    )

    template = data["evidence"]["evidence_records"][0]
    derived = deepcopy(template)
    derived["evidence_id"] = "derived"
    derived["reported_result"]["statement"] = "由合成原始结果处理得到的结果"
    derived["figure_refs"] = []

    si_result = deepcopy(template)
    si_result["evidence_id"] = "si-result"
    si_result["reported_result"]["statement"] = "SI 中报告的合成结果"
    si_result["text_locations"] = [
        {
            "library_ref": {"library_type": "user", "library_id": "synthetic"},
            "parent_item_key": "SYNITEM1",
            "source_set_id": "set-2",
            "attachment_key": "SYNSI001",
            "block_id": "B001",
            "span": None,
            "pdf_role": "SI",
            "page_pdf": 2,
            "page_label": None,
            "section_path": ["Synthetic SI"],
            "text_excerpt": "合成 SI 结果：条件未报告；图 S1。",
            "table_locator": None,
            "applies_to": [],
        }
    ]
    si_result["figure_refs"] = [{"source_set_id": "set-2", "attachment_key": "SYNSI001", "figure_id": "S1"}]

    unlinked = deepcopy(template)
    unlinked["evidence_id"] = "unlinked"
    unlinked["reported_result"]["statement"] = "尚未与任何论点建立正式用途的合成结果"
    unlinked["figure_refs"] = []
    data["evidence"]["evidence_records"].extend([derived, si_result, unlinked])
    data["evidence"]["process_relations"].append(
        {
            "process_id": "process",
            "record_meta": _meta(),
            "input_evidence_ids": ["result"],
            "output_evidence_ids": ["derived"],
            "process_type": "处理得到",
            "operation": "合成处理步骤",
            "parameters_or_model": None,
            "attribution": "作者明示",
            "extraction_status": "明确",
            "text_locations": [deepcopy(template["text_locations"][0])],
        }
    )
    data["evidence"]["provenance_groups"].append(
        {
            "group_id": "provenance",
            "record_meta": _meta(),
            "member_evidence_ids": ["result", "derived"],
            "shared_source_description": "共用同一合成原始数据，不是独立证据",
            "text_locations": [deepcopy(template["text_locations"][0])],
        }
    )
    data["evidence"]["use_groups"].append(
        {
            "use_group_id": "combined",
            "record_meta": _meta(),
            "members": [
                {"evidence_id": "derived", "role": "处理结果"},
                {"evidence_id": "si-result", "role": "SI 对照"},
            ],
            "group_type": "比较使用",
            "combination_description": "作者在合成范围内联合比较两项结果",
            "comparison_statement": "只登记作者的联合用途",
            "attribution": "作者明示",
            "extraction_status": "明确",
            "text_locations": [deepcopy(template["text_locations"][0])],
        }
    )
    data["evidence"]["mappings"].append(
        {
            "mapping_id": "combined-mapping",
            "record_meta": _meta(),
            "subject_ref": _ref("combined"),
            "target_ref": _ref("link", "bridge"),
            "target_part": "合成联合比较的局部范围",
            "usage_role": "比较材料",
            "use_topic": "其他",
            "usage_description": "联合组被用于比较，不改写为因果证明",
            "attribution": "作者明示",
            "extraction_status": "明确",
            "text_locations": [deepcopy(template["text_locations"][0])],
        }
    )
    data["evidence"]["candidate_mappings"].append(
        {
            "candidate_id": "unlinked-candidate",
            "record_meta": _meta(),
            "subject_ref": _ref("unlinked"),
            "candidate_targets": [_ref("branch")],
            "unresolved_description": "未找到作者将该结果用于分论点的明确位置",
            "attribution": None,
            "extraction_status": "未明确",
            "text_locations": [deepcopy(template["text_locations"][0])],
            "resolved_mapping_ids": [],
            "decision": "pending",
            "decision_reason": None,
        }
    )

    assessment = data["review"]["claim_assessments"][0]
    assessment["reliability_statement"] = {
        "conclusion": "关键支持仍待核实",
        "scope": assessment["assessed_scope"],
        "rationale": "来源可定位不能解决科学解释范围的缺口",
    }
    return data


def _verification_report(note) -> dict:
    without_report = build_object_index(note)
    verdicts = []
    for entry in without_report["objects"]:
        source_refs = []
        seen = set()
        for source in entry["source_locations"]:
            key = (source["source_set_id"], source["attachment_key"], source["block_id"])
            if key in seen:
                continue
            seen.add(key)
            source_refs.append({"source_set_id": key[0], "attachment_key": key[1], "block_id": key[2]})
        verdicts.append(
            {
                "object_id": entry["short_id"],
                "status": "支持",
                "source_refs": source_refs,
                "note": "只记录合成文本的来源评估",
            }
        )
    return {
        "schema_version": "ai-note2-verification/2",
        "checked_objects": len(verdicts),
        "model_used": True,
        "errors": [],
        "counts": {"支持": len(verdicts), "部分支持": 0, "不支持": 0, "无法核对": 0},
        "verdicts": verdicts,
    }


@pytest.fixture
def expanded_note(document):
    return allocate_indexes(_expanded_document(document))


def test_index_separates_typed_relations_and_evidence_independence(expanded_note):
    index = build_object_index(expanded_note)
    relation_types = {relation["relation_type"] for relation in index["relations"]}
    assert {
        "argument_support",
        "processing_derivation",
        "shared_provenance",
        "combined_use",
        "evidence_use",
        "candidate_evidence_use",
        "reviews",
        "review_coverage",
        "claim_assessment",
    } <= relation_types
    shared = next(relation for relation in index["relations"] if relation["relation_type"] == "shared_provenance")
    assert "不是独立证据" in shared["details"]["shared_source_description"]
    unlinked = next(entry for entry in index["objects"] if "尚未与任何论点" in entry["summary"])
    assert {"unlinked_evidence", "candidate_link_only", "missing_context"} <= set(unlinked["flags"])
    assert index["counts"]["flags"]["unassessed_claim"] >= 1


def test_multiple_attachments_keep_full_source_identity(expanded_note):
    index = build_object_index(expanded_note)
    locations = {
        (source["source_set_id"], source["attachment_key"], source["block_id"])
        for entry in index["objects"]
        for source in entry["source_locations"]
    }
    assert ("set-1", "SYNATT01", "B001") in locations
    assert ("set-2", "SYNSI001", "B001") in locations
    report = _verification_report(expanded_note)
    verified = build_object_index(expanded_note, verification=report)
    assert verified["validation_scope"]["model_source_assessment"] == "recorded"
    si_entry = next(entry for entry in verified["objects"] if "SI 中报告" in entry["summary"])
    assert si_entry["model_source_assessment"]["source_refs"] == [
        {"source_set_id": "set-2", "attachment_key": "SYNSI001", "block_id": "B001"}
    ]


def test_source_assessment_and_scientific_review_remain_distinct(expanded_note):
    report = _verification_report(expanded_note)
    index = build_object_index(expanded_note, verification=report)
    core = next(entry for entry in index["objects"] if entry["short_id"] == "TC-N001")
    assessment = next(relation for relation in index["relations"] if relation["relation_type"] == "claim_assessment")
    assert core["model_source_assessment"]["status"] == "支持"
    assert assessment["details"]["conclusion"] == "关键支持仍待核实"
    overview = render_overview_markdown(expanded_note, verification=report)
    assert "schema 与对象引用" in overview
    assert "不代表科学正确" in overview
    assert "未检查图像像素" in overview


def test_absent_verification_and_missing_conditions_are_visible(expanded_note):
    index = build_object_index(expanded_note)
    assert index["validation_scope"]["model_source_assessment"] == "absent"
    assert all(entry["model_source_assessment"] == {"state": "absent"} for entry in index["objects"])
    evidence = render_chain_markdown(expanded_note, "evidence")
    assert "实验或解读上下文缺失" in evidence
    assert "缺失（未推导为通过）" in evidence


def test_reordering_preserves_existing_object_identity(expanded_note):
    before = build_object_index(expanded_note)
    data = expanded_note.to_dict()
    data["thought"]["nodes"].reverse()
    data["evidence"]["evidence_records"].reverse()
    reordered = validate_document(data)
    after = build_object_index(reordered)
    before_identity = {entry["short_id"]: (entry["canonical_ref"], entry["index"]) for entry in before["objects"]}
    after_identity = {entry["short_id"]: (entry["canonical_ref"], entry["index"]) for entry in after["objects"]}
    assert after_identity == before_identity
    assert before["source"]["analysis_sha256"] != after["source"]["analysis_sha256"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda index: index["source"].__setitem__("run_number", 99),
        lambda index: index["relations"][0]["to_refs"][0].__setitem__("object_id", "MISSING"),
        lambda index: index["objects"][0]["canonical_ref"].__setitem__("parent_item_key", "OTHER"),
        lambda index: index["objects"][0]["source_locations"][0].__setitem__("attachment_key", "OTHER"),
    ],
)
def test_index_validation_rejects_stale_run_dangling_relation_and_source_mismatch(expanded_note, mutation):
    index = build_object_index(expanded_note)
    mutation(index)
    with pytest.raises(Note2ValidationError, match="派生索引与当前分析"):
        validate_object_index(expanded_note, index)


def test_verification_rejects_attachment_ambiguous_block_reference(expanded_note):
    report = _verification_report(expanded_note)
    si_verdict = next(
        verdict
        for verdict in report["verdicts"]
        if verdict["source_refs"] and verdict["source_refs"][0]["source_set_id"] == "set-2"
    )
    si_verdict["source_refs"][0]["attachment_key"] = "SYNATT01"
    with pytest.raises(Note2ValidationError, match="来源引用未登记"):
        build_object_index(expanded_note, verification=report)


def test_markdown_html_and_zotero_view_are_static_and_traceable(expanded_note):
    report = _verification_report(expanded_note)
    overview = render_overview_markdown(expanded_note, verification=report)
    thought = render_chain_markdown(expanded_note, "thought", verification=report)
    evidence = render_chain_markdown(expanded_note, "evidence", verification=report)
    review = render_chain_markdown(expanded_note, "review", verification=report)
    html = render_html(expanded_note, title="Synthetic", verification=report)
    assert "object-index" not in overview.lower() or "对象索引" in overview
    assert "analysis.json#/thought/nodes/0" in thought
    assert "shared_provenance" in evidence
    assert "关键支持仍待核实" in review
    assert "<script" not in html
    assert "<svg" not in html
    assert "href=" not in html
    assert "EC-E" in html and "RC-A" in html and "TC-N" in html
