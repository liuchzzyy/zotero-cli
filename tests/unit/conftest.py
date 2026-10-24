"""ai-note2 单元测试共用的合成三链文档夹具。"""

from __future__ import annotations

from copy import deepcopy

import pytest

from zotero_cli.ai_notes2.models import SCHEMA_VERSION


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


def build_document() -> dict:
    """构造与契约测试一致的最小完整文档。"""
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


@pytest.fixture
def document() -> dict:
    return build_document()
