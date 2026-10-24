"""三链单文档的结构、全局编号、冻结来源及图闭合契约。"""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from zotero_cli.ai_notes2.models import SCHEMA_VERSION, note2_json_schema
from zotero_cli.ai_notes2.validation import (
    Note2ValidationError,
    allocate_indexes,
    parse_document,
    populate_source_excerpts,
    resolve_index,
    validate_document,
)

pytestmark = pytest.mark.unit


def _ref(identifier, field=None):
    return {"kind": "object", "target_id": identifier, "target_field": field}


def _meta(state="active"):
    return {"state": state, "replaces": [], "replaced_by": [], "note": None}


def _location():
    return {
        "library_ref": {"library_type": "user", "library_id": "synthetic"},
        "parent_item_key": "SYNITEM1",
        "source_set_id": "set-1",
        "attachment_key": "SYNATT01",
        "block_id": "B001",
        "span": None,
        "pdf_role": "MS",
        "page_pdf": 1,
        "page_label": None,
        "section_path": [],
        "text_excerpt": "虚构结果：812% 与 1.0e-6；图 1。",
        "table_locator": None,
        "applies_to": [],
    }


def _node(identifier, node_type):
    return {
        "node_id": identifier,
        "record_meta": _meta(),
        "node_type": node_type,
        "text": "虚构命题；数值与原文不必逐字相同",
        "claim_kind": None,
        "argument_angle": None,
        "original_qualifiers": [],
        "attribution": "作者明示",
        "extraction_status": "明确",
        "text_locations": [_location()],
        "presentation_status": "未进一步展开",
        "presentation_note": None,
    }


def _edge(identifier="link", start="branch", end="core"):
    return {
        "edge_id": identifier,
        "record_meta": _meta(),
        "from_node_id": start,
        "to_node_id": end,
        "bridge": "虚构作者联系",
        "target_part": "指定条件下的联系",
        "combination": {"mode": "未明确", "edge_ids": []},
        "attribution": "文本重构",
        "extraction_status": "可重构",
        "text_locations": [_location()],
    }


def _evidence(identifier="result"):
    unknowns = [
        "/primary_aspect",
        "/method_or_acquisition/description",
        "/reported_result/comparison",
        "/reported_result/uncertainty",
        "/author_interpretation",
        "/reference_info",
    ]
    return {
        "evidence_id": identifier,
        "record_meta": _meta(),
        "research_origin": "实验依据",
        "primary_aspect": None,
        "additional_aspects": [],
        "object_and_conditions": {"description": "虚构对象", "conditions": []},
        "field_statuses": [
            {"path": path, "status": "当前输入未找到", "reason": "虚构输入未提供", "source_refs": []}
            for path in unknowns
        ],
        "method_or_acquisition": {"description": None, "parameters": []},
        "reported_result": {
            "statement": "任意原值 999.123e99",
            "values": [],
            "comparison": None,
            "uncertainty": None,
            "original_qualifiers": [],
        },
        "result_type": "数据处理或统计结果",
        "author_interpretation": None,
        "text_locations": [_location()],
        "source_access": {"forms": ["正文文本"], "reading_scope": "当前论文的本研究报告"},
        "reference_info": None,
        "attribution": "作者明示",
        "extraction_status": "明确",
        "figure_refs": [{"source_set_id": "set-1", "attachment_key": "SYNATT01", "figure_id": "1"}],
    }


def _mapping():
    return {
        "mapping_id": "mapping",
        "record_meta": _meta(),
        "subject_ref": _ref("result"),
        "target_ref": _ref("link", "bridge"),
        "target_part": "桥的局部范围",
        "usage_role": "论证依据",
        "use_topic": "机制论证",
        "usage_description": "虚构作者用途",
        "attribution": "作者明示",
        "extraction_status": "明确",
        "text_locations": [_location()],
    }


def _question():
    return {
        "review_id": "question",
        "record_meta": _meta(),
        "primary_target_ref": _ref("result"),
        "related_target_refs": [],
        "related_review_refs": [],
        "review_domain": "来源与一致性",
        "consistency_subtype": "提取一致",
        "question": "报告是否可定位？",
        "issue_origin": "审查者提出",
        "problem_locus": None,
        "observed_basis": "已有冻结文本",
        "criterion": {"statement": "定位应对应冻结块", "criterion_type": "一般逻辑", "criterion_source_refs": []},
        "basis_refs": [
            {"ref": {"kind": "source", "source_ref": _location()}, "role": "缓解疑点", "relevance": "可定位"}
        ],
        "reasoning": "仅检查来源结构",
        "finding_status": "未发现明确问题",
        "impact": None,
        "impact_reason": "不涉及负面影响",
        "direct_effect": "只证明可定位",
        "response_refs": [],
        "resolution_status": None,
        "resolution_requirements": None,
        "review_scope": "虚构结果的来源定位",
        "suggestion_refs": [],
        "text_locations": [_location()],
    }


def _coverage():
    return {
        "coverage_id": "coverage",
        "record_meta": _meta(),
        "scope_unit_id": "scope-1",
        "target_refs": [_ref("result")],
        "review_domain": "来源与一致性",
        "consistency_subtype": "提取一致",
        "criterion_scope": "虚构结果的来源定位",
        "check_status": "已检查",
        "inspected_inputs": [{"kind": "source", "source_ref": _location()}],
        "review_refs": [_ref("question")],
        "assessment_refs": [],
        "unavailable_reason": None,
        "not_applicable_reason": None,
    }


def _assessment():
    return {
        "assessment_id": "assessment",
        "record_meta": _meta(),
        "target_ref": _ref("core"),
        "assessed_scope": "虚构条件",
        "background_refs": [],
        "evidence_refs": [_ref("result")],
        "mapping_refs": [_ref("mapping")],
        "evidence_review": [{"review_ref": _ref("question"), "role": "来源结构"}],
        "logic_review": [],
        "provenance_handling": "仅一项结果，不重复计数",
        "supporting_basis": [],
        "counter_basis": [],
        "unresolved_reviews": [],
        "synthesis": {
            field: "未核对科学有效性"
            for field in (
                "load_bearing_dependencies",
                "material_balance",
                "issue_handling",
                "remaining_gaps",
                "conclusion_reason",
            )
        },
        "reliability_statement": {"conclusion": "当前无法评议", "scope": "虚构条件", "rationale": "仅校验结构"},
        "retained_claim": None,
        "review_scope": "虚构文本",
        "unassessed_parts": [{"part": "科学有效性", "reason": "未检查"}],
        "coverage_refs": [_ref("coverage")],
    }


def _candidate():
    return {
        "candidate_id": "candidate",
        "record_meta": _meta(),
        "candidate_kind": "命题候选",
        "candidate_text": None,
        "proposed_node_type": None,
        "from_node_id": None,
        "to_node_id": None,
        "unresolved_description": "输入不足",
        "attribution": None,
        "extraction_status": "未明确",
        "text_locations": [_location()],
        "resolution_refs": [],
        "decision": "pending",
        "decision_reason": None,
    }


@pytest.fixture
def document():
    coverage = _coverage()
    return {
        "schema_version": SCHEMA_VERSION,
        "library_ref": {"library_type": "user", "library_id": "synthetic"},
        "parent_item_key": "SYNITEM1",
        "run_number": 1,
        "inputs": {
            "source_note": {"note_key": "SYNNOTE1", "text_digest": "a" * 64},
            "documents": [
                {
                    "source_set_id": "set-1",
                    "attachment_key": "SYNATT01",
                    "attachment_aliases": [],
                    "pdf_sha256": "b" * 64,
                    "pdf_role": "MS",
                    "document_identity_status": "身份未明确",
                    "role_basis": "虚构 fixture",
                    "parse_metadata": {
                        "parser": "synthetic",
                        "parser_version": None,
                        "adapter_version": "1",
                        "parsed_at": None,
                        "parse_artifact_digest": "c" * 64,
                    },
                }
            ],
        },
        "source_blocks": [
            {
                "source_set_id": "set-1",
                "attachment_key": "SYNATT01",
                "block_id": "B001",
                "block_type": "caption",
                "pdf_role": "MS",
                "page_pdf": 1,
                "page_label": None,
                "section_path": [],
                "text": _location()["text_excerpt"],
                "table": None,
                "figure_ids": ["1"],
            }
        ],
        "thought": {
            "nodes": [_node("core", "核心论点"), _node("branch", "分论点"), _node("reason", "论证理由")],
            "support_edges": [_edge(), _edge("lower-link", "reason", "branch")],
            "candidate_records": [_candidate()],
        },
        "evidence": {
            "evidence_records": [_evidence()],
            "process_relations": [],
            "provenance_groups": [],
            "use_groups": [],
            "mappings": [_mapping()],
            "candidate_mappings": [
                {
                    "candidate_id": "candidate-use",
                    "record_meta": _meta(),
                    "subject_ref": _ref("result"),
                    "candidate_targets": [],
                    "unresolved_description": "用途未明确",
                    "attribution": None,
                    "extraction_status": "未明确",
                    "text_locations": [_location()],
                    "resolved_mapping_ids": [],
                    "decision": "pending",
                    "decision_reason": None,
                }
            ],
        },
        "review": {
            "background_records": [],
            "criterion_sources": [],
            "review_items": [_question()],
            "coverage_records": [coverage],
            "claim_assessments": [_assessment()],
            "change_suggestions": [],
            "judgment_author": "AI",
            "criterion_source_set_id": None,
            "coverage_scope": {
                "units": [
                    {
                        key: deepcopy(coverage[key])
                        for key in (
                            "scope_unit_id",
                            "target_refs",
                            "review_domain",
                            "consistency_subtype",
                            "criterion_scope",
                        )
                    }
                ],
                "exclusions": ["未检查科学有效性"],
                "declaration": "部分覆盖",
            },
        },
        "object_registry": [],
    }


def test_minimal_complete_fixture_and_global_indexes(document):
    original = deepcopy(document)
    note = allocate_indexes(document)
    data = note.to_dict()
    assert document == original
    assert note.schema_version == "ai-note2/1.0"
    assert len(data["object_registry"]) == 12
    assert len({entry["index"] for entry in data["object_registry"]}) == 12
    assert data["thought"]["candidate_records"][0]["index"] == "SYNITEM1-0001-0006"
    assert data["evidence"]["mappings"][0]["target_ref"] == _ref("TC-L001", "bridge")
    assert data["review"]["coverage_scope"]["units"][0]["target_refs"] == [_ref("EC-E001")]
    assert resolve_index(note, _ref("TC-L001", "bridge")) == "SYNITEM1-0001-0004.bridge"
    assert parse_document(json.dumps(data)).to_dict() == data
    data["thought"]["nodes"].reverse()
    assert validate_document(data).to_dict()["object_registry"] == note.to_dict()["object_registry"]


def test_schema_is_independent_and_rejects_legacy_container(document):
    schema = note2_json_schema()
    schema["$defs"].clear()
    assert note2_json_schema()["$defs"]
    document["bundle"] = {}
    with pytest.raises(Note2ValidationError, match="未知字段"):
        allocate_indexes(document)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("run_number",), True),
        (("run_number",), 0),
        (("schema_version",), "bundle/1.0"),
        (("thought", "nodes", 0, "node_type"), "第四级"),
        (("evidence", "evidence_records", 0, "reported_result", "values"), "非数组"),
        (("evidence", "evidence_records", 0, "source_access", "forms"), ["图像像素"]),
        (("thought", "nodes", 0, "extraction_status"), "可重构"),
        (("thought", "nodes", 0, "text_locations"), []),
        (("thought", "candidate_records", 0, "attribution"), "作者明示"),
    ],
)
def test_strict_types_enums_and_attribution(document, path, value):
    owner = document
    for key in path[:-1]:
        owner = owner[key]
    owner[path[-1]] = value
    with pytest.raises(Note2ValidationError):
        allocate_indexes(document)


def test_required_nullable_field_must_be_present(document):
    del document["thought"]["nodes"][0]["claim_kind"]
    with pytest.raises(Note2ValidationError, match="claim_kind.*缺少必要字段"):
        allocate_indexes(document)


@pytest.mark.parametrize("field", ["score", "confidence", "truth", "extensions"])
def test_undeclared_record_fields_are_rejected(document, field):
    document["thought"]["nodes"][0][field] = 1
    with pytest.raises(Note2ValidationError, match="未知字段"):
        allocate_indexes(document)


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "{} trailing", "```json\n{}\n```"])
def test_strict_json(raw):
    with pytest.raises(Note2ValidationError):
        parse_document(raw)


@pytest.mark.parametrize(
    "field,value",
    [
        ("page_pdf", 9),
        ("attachment_key", "OTHER"),
        ("block_id", "missing"),
        ("pdf_role", "SI"),
        ("parent_item_key", "OTHER"),
        ("text_excerpt", "伪造引文"),
        ("span", [0, 9999]),
        ("span", [1, 1]),
    ],
)
def test_frozen_source_identity_and_span(document, field, value):
    document["thought"]["nodes"][0]["text_locations"][0][field] = value
    with pytest.raises(Note2ValidationError):
        allocate_indexes(document)


def test_unicode_spans_are_filled_from_snapshot_without_mutation(document):
    location = document["thought"]["nodes"][0]["text_locations"][0]
    location["span"] = [2, 7]
    location["text_excerpt"] = "模型不负责原文"
    filled = populate_source_excerpts(document)
    assert (
        filled["thought"]["nodes"][0]["text_locations"][0]["text_excerpt"] == document["source_blocks"][0]["text"][2:7]
    )
    assert location["text_excerpt"] == "模型不负责原文"
    allocate_indexes(filled)


@pytest.mark.parametrize("span", [[True, 2], [-1, 2], [2, 2], [0, 999], [0], "0:2"])
def test_excerpt_population_rejects_invalid_spans(document, span):
    document["thought"]["nodes"][0]["text_locations"][0]["span"] = span
    with pytest.raises(Note2ValidationError, match="区间"):
        populate_source_excerpts(document)


def test_table_locator_and_applies_to(document):
    block = document["source_blocks"][0]
    block["block_type"] = "table"
    block["table"] = {
        "table_id": "T1",
        "cells": [{"cell_id": "C1", "text": "812%"}],
        "header_path": ["测试值"],
        "notes": [{"note_id": "F1", "text": "限定条件"}],
    }
    location = document["thought"]["nodes"][0]["text_locations"][0]
    location["table_locator"] = {"table_id": "T1", "cell_ids": ["C1"], "header_path": ["测试值"], "note_ids": ["F1"]}
    location["applies_to"] = ["/text"]
    allocate_indexes(document)
    location["table_locator"]["cell_ids"] = ["invented"]
    with pytest.raises(Note2ValidationError, match="单元格"):
        allocate_indexes(document)
    location["table_locator"]["cell_ids"] = ["C1"]
    location["applies_to"] = ["/missing"]
    with pytest.raises(Note2ValidationError, match="字段路径"):
        allocate_indexes(document)


@pytest.mark.parametrize("field,value", [("figure_id", "S99"), ("attachment_key", "other"), ("source_set_id", "other")])
def test_figures_are_scoped_to_frozen_attachment(document, field, value):
    document["evidence"]["evidence_records"][0]["figure_refs"][0][field] = value
    with pytest.raises(Note2ValidationError, match="图引用"):
        allocate_indexes(document)


@pytest.mark.parametrize("target", [_ref("missing"), _ref("candidate"), _ref("result"), _ref("core", "bridge")])
def test_dangling_wrong_type_candidate_and_invalid_bridge_references(document, target):
    document["evidence"]["mappings"][0]["target_ref"] = target
    with pytest.raises(Note2ValidationError):
        allocate_indexes(document)


@pytest.mark.parametrize(
    "start,end", [("core", "branch"), ("reason", "core"), ("branch", "branch"), ("candidate", "core")]
)
def test_thought_direction_depth_and_candidates(document, start, end):
    edge = document["thought"]["support_edges"][0]
    edge["from_node_id"], edge["to_node_id"] = start, end
    with pytest.raises(Note2ValidationError):
        allocate_indexes(document)


def test_joint_support_membership_and_retirement(document):
    document["thought"]["nodes"].append(_node("branch-2", "分论点"))
    edge = document["thought"]["support_edges"][0]
    second = _edge("joint-link", "branch-2", "core")
    document["thought"]["support_edges"].append(second)
    for member in (edge, second):
        member["combination"] = {"mode": "联合使用", "edge_ids": ["link", "joint-link"]}
    allocate_indexes(document)
    second["combination"]["edge_ids"] = ["joint-link"]
    with pytest.raises(Note2ValidationError, match="联合组"):
        allocate_indexes(document)
    second["combination"]["edge_ids"] = ["link", "joint-link"]
    edge["record_meta"]["state"] = "retired"
    with pytest.raises(Note2ValidationError, match="生命周期"):
        allocate_indexes(document)
    second["record_meta"]["state"] = "retired"
    document["evidence"]["mappings"][0]["record_meta"]["state"] = "retired"
    allocate_indexes(document)


def _process(identifier, start, end):
    return {
        "process_id": identifier,
        "record_meta": _meta(),
        "input_evidence_ids": [start],
        "output_evidence_ids": [end],
        "process_type": "处理得到",
        "operation": "虚构积分",
        "parameters_or_model": None,
        "attribution": "作者明示",
        "extraction_status": "明确",
        "text_locations": [_location()],
    }


def test_process_provenance_and_use_groups_have_distinct_semantics(document):
    evidence = document["evidence"]
    evidence["evidence_records"].append(_evidence("result-2"))
    evidence["process_relations"] = [_process("processing", "result", "result-2")]
    evidence["provenance_groups"] = [
        {
            "group_id": "provenance",
            "record_meta": _meta(),
            "member_evidence_ids": ["result", "result-2"],
            "shared_source_description": "同源说明",
            "text_locations": [_location()],
        }
    ]
    evidence["use_groups"] = [
        {
            "use_group_id": "joint",
            "record_meta": _meta(),
            "members": [{"evidence_id": "result", "role": "输入"}, {"evidence_id": "result-2", "role": "输出"}],
            "group_type": "联合论证",
            "combination_description": "作者联合使用",
            "comparison_statement": None,
            "attribution": "作者明示",
            "extraction_status": "明确",
            "text_locations": [_location()],
        }
    ]
    evidence["mappings"][0]["subject_ref"] = _ref("joint")
    note = allocate_indexes(document).to_dict()
    assert note["evidence"]["process_relations"][0]["output_evidence_ids"] == ["EC-E002"]
    assert note["evidence"]["use_groups"][0]["members"][0]["evidence_id"] == "EC-E001"
    evidence["process_relations"].append(_process("cycle", "result-2", "result"))
    with pytest.raises(Note2ValidationError, match="循环"):
        allocate_indexes(document)
    evidence["process_relations"] = [_process("self", "result", "result")]
    with pytest.raises(Note2ValidationError, match="相交"):
        allocate_indexes(document)


def test_candidate_acceptance_creates_new_objects_and_preserves_candidate(document):
    candidate = document["thought"]["candidate_records"][0]
    candidate.update(decision="accepted", resolution_refs=[_ref("reason")])
    candidate["record_meta"]["state"] = "resolved"
    use = document["evidence"]["candidate_mappings"][0]
    use.update(decision="accepted", resolved_mapping_ids=["mapping"], candidate_targets=[_ref("link", "bridge")])
    use["record_meta"]["state"] = "resolved"
    data = allocate_indexes(document).to_dict()
    assert data["thought"]["candidate_records"][0]["resolution_refs"] == [_ref("TC-N003")]
    assert data["evidence"]["candidate_mappings"][0]["resolved_mapping_ids"] == ["EC-M001"]
    assert data["thought"]["candidate_records"][0]["index"] != data["thought"]["nodes"][2]["index"]
    use["candidate_targets"] = [_ref("core")]
    with pytest.raises(Note2ValidationError, match="候选主体"):
        allocate_indexes(document)


@pytest.mark.parametrize(
    "decision,state,results,reason",
    [
        ("accepted", "active", [], None),
        ("accepted", "resolved", [], None),
        ("pending", "active", [_ref("core")], None),
        ("rejected", "retired", [], None),
        ("withdrawn", "retired", [_ref("core")], "撤回"),
    ],
)
def test_candidate_state_combinations(document, decision, state, results, reason):
    candidate = document["thought"]["candidate_records"][0]
    candidate.update(decision=decision, resolution_refs=results, decision_reason=reason)
    candidate["record_meta"]["state"] = state
    with pytest.raises(Note2ValidationError):
        allocate_indexes(document)


def test_missing_scientific_fields_need_status_but_arbitrary_numbers_are_allowed(document):
    evidence = document["evidence"]["evidence_records"][0]
    for raw in ("1.0e-6", "812.3%", "187.2", "1.448 g ZnSO4", "0.50156", "$10^{-99}$"):
        evidence["reported_result"]["statement"] = raw
        allocate_indexes(document)
    evidence["field_statuses"].pop()
    with pytest.raises(Note2ValidationError, match="field_statuses"):
        allocate_indexes(document)


@pytest.mark.parametrize(
    "mutation", ["no_reviews", "wrong_scope", "wrong_domain", "unknown_unit", "unknown_finding", "no_inputs"]
)
def test_checked_coverage_requires_actual_matching_review(document, mutation):
    coverage = document["review"]["coverage_records"][0]
    question = document["review"]["review_items"][0]
    if mutation == "no_reviews":
        coverage["review_refs"] = []
    elif mutation == "wrong_scope":
        question["review_scope"] = "局部检查不能冒充整链"
    elif mutation == "wrong_domain":
        question.update(review_domain="数据分析", consistency_subtype=None)
    elif mutation == "unknown_unit":
        coverage["scope_unit_id"] = "unknown"
    elif mutation == "unknown_finding":
        question.update(finding_status="当前无法判断", problem_locus="当前无法区分")
    elif mutation == "no_inputs":
        coverage["inspected_inputs"] = []
    with pytest.raises(Note2ValidationError):
        allocate_indexes(document)


@pytest.mark.parametrize(
    "status,reason_field",
    [("未检查", None), ("当前不可检查", "unavailable_reason"), ("不适用", "not_applicable_reason")],
)
def test_coverage_missing_and_inapplicable_states(document, status, reason_field):
    coverage = document["review"]["coverage_records"][0]
    coverage.update(check_status=status, review_refs=[], inspected_inputs=[])
    if reason_field:
        coverage[reason_field] = "输入不足或范围不适用"
    allocate_indexes(document)
    document["review"]["coverage_scope"]["declaration"] = "已完成声明范围"
    if status == "不适用":
        allocate_indexes(document)
    else:
        with pytest.raises(Note2ValidationError, match="声明完成"):
            allocate_indexes(document)


def test_positive_assessment_cannot_come_from_empty_problem_table(document):
    assessment = document["review"]["claim_assessments"][0]
    assessment["reliability_statement"]["conclusion"] = "当前材料内支持较充分"
    with pytest.raises(Note2ValidationError, match="正向材料"):
        allocate_indexes(document)
    assessment["supporting_basis"] = [
        {"ref": _ref("result"), "relevance": "虚构报告", "conditions_match": "仅所列条件", "response_refs": []}
    ]
    allocate_indexes(document)
    coverage = document["review"]["coverage_records"][0]
    coverage.update(check_status="未检查", review_refs=[], inspected_inputs=[])
    with pytest.raises(Note2ValidationError, match="完成判断"):
        allocate_indexes(document)


def test_external_criterion_revision_and_actual_location(document):
    review = document["review"]
    review["criterion_source_set_id"] = "external-set"
    review["criterion_sources"] = [
        {
            "criterion_source_id": "criterion",
            "record_meta": _meta(),
            "source_identity": {
                "creators": [],
                "title": "虚构判准",
                "year": None,
                "doi": None,
                "standard_id": None,
                "url": None,
            },
            "edition_or_version": None,
            "source_revision": 1,
            "content_snapshot_ref": "synthetic-snapshot",
            "retrieved_at": None,
            "access_scope": "已读取相关内容",
            "check_status": "已核对",
            "locations": [
                {
                    "location_id": "clause",
                    "section": None,
                    "page_label": None,
                    "clause": None,
                    "text_excerpt": "虚构规则",
                }
            ],
            "purpose_note": "仅提供判准",
        }
    ]
    criterion_ref = {
        "kind": "criterion",
        "criterion_source_id": "criterion",
        "source_revision": 1,
        "location_id": "clause",
    }
    question = review["review_items"][0]
    question["criterion"].update(criterion_type="特定领域规则", criterion_source_refs=[criterion_ref])
    note = allocate_indexes(document).to_dict()
    assert (
        note["review"]["review_items"][0]["criterion"]["criterion_source_refs"][0]["criterion_source_id"] == "RC-K001"
    )
    criterion_ref["source_revision"] = 2
    with pytest.raises(Note2ValidationError, match="版本"):
        allocate_indexes(document)
    criterion_ref["source_revision"] = 1
    review["criterion_sources"][0]["access_scope"] = "仅书目信息"
    with pytest.raises(Note2ValidationError, match="实际读取"):
        allocate_indexes(document)


def test_duplicates_registry_and_reallocation(document):
    note = allocate_indexes(document)
    with pytest.raises(Note2ValidationError, match="最终 index"):
        allocate_indexes(note.to_dict())
    broken = note.to_dict()
    broken["object_registry"][1]["index"] = broken["object_registry"][0]["index"]
    with pytest.raises(Note2ValidationError, match="重复|不一致"):
        validate_document(broken)
    document["thought"]["nodes"][1]["node_id"] = "core"
    with pytest.raises(Note2ValidationError, match="ID 重复"):
        allocate_indexes(document)


def test_index_expansion_library_scope_and_export_copy(document):
    document["run_number"] = 10000
    note = allocate_indexes(document, start=9999)
    indexes = [entry["index"] for entry in note.to_dict()["object_registry"]]
    assert indexes[:2] == ["SYNITEM1-10000-9999", "SYNITEM1-10000-10000"]
    exported = note.to_dict()
    exported["library_ref"]["library_id"] = "other"
    assert note.to_dict()["library_ref"]["library_id"] == "synthetic"
    with pytest.raises(Note2ValidationError):
        resolve_index(note, _ref("TC-N001", "bridge"))
    with pytest.raises(Note2ValidationError):
        allocate_indexes(document, start=True)
