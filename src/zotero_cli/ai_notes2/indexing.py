"""从 `Note2Document` 确定性派生对象索引和类型关系投影。"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from hashlib import sha256
from typing import Any

from zotero_cli.ai_notes2.models import RECORD_TABLES, Note2Document

INDEX_SCHEMA_VERSION = "ai-note2-index/1"

_CHAIN_NAMES = {"thought": "思维", "evidence": "证据", "review": "审查"}
_TYPE_NAMES = {
    "TC-N": "论点",
    "TC-L": "支持关系",
    "TC-C": "思维候选",
    "EC-E": "报告结果",
    "EC-P": "处理或派生",
    "EC-G": "共享来源",
    "EC-U": "联合使用",
    "EC-M": "证据用途",
    "EC-C": "用途候选",
    "RC-B": "背景",
    "RC-K": "判准来源",
    "RC-Q": "审查问题",
    "RC-S": "审查覆盖",
    "RC-A": "命题评议",
    "RC-D": "修改建议",
}


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _compact_text(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _identity_ref(identifier: str, target_field: str | None = None) -> dict[str, str | None]:
    return {"object_id": identifier, "target_field": target_field}


def _summary(prefix: str, record: dict[str, Any]) -> str:
    if prefix == "TC-N":
        return _compact_text(record["text"])
    if prefix == "TC-L":
        return _compact_text(record["bridge"])
    if prefix in {"TC-C", "EC-C"}:
        return _compact_text(record["unresolved_description"])
    if prefix == "EC-E":
        return _compact_text(record["reported_result"]["statement"])
    if prefix == "EC-P":
        return _compact_text(f"{record['process_type']}：{record['operation']}")
    if prefix == "EC-G":
        return _compact_text(record["shared_source_description"])
    if prefix == "EC-U":
        return _compact_text(record["combination_description"])
    if prefix == "EC-M":
        return _compact_text(record["usage_description"])
    if prefix == "RC-B":
        return _compact_text(record["context_note"] or record["publication_notes"] or record["acquisition_status"])
    if prefix == "RC-K":
        return _compact_text(record["purpose_note"])
    if prefix == "RC-Q":
        return _compact_text(record["question"])
    if prefix == "RC-S":
        return _compact_text(f"{record['check_status']}：{record['criterion_scope']}")
    if prefix == "RC-A":
        statement = record["reliability_statement"]
        return _compact_text(f"{statement['conclusion']}：{statement['scope']}")
    if prefix == "RC-D":
        return _compact_text(record["proposed_content"])
    return ""


def _walk_locations(value: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(value, dict):
        if {"source_set_id", "attachment_key", "block_id", "text_excerpt"} <= set(value):
            found.append((path or "/", value))
        for key, nested in value.items():
            token = str(key).replace("~", "~0").replace("/", "~1")
            found.extend(_walk_locations(nested, f"{path}/{token}"))
    elif isinstance(value, list):
        for number, nested in enumerate(value):
            found.extend(_walk_locations(nested, f"{path}/{number}"))
    return found


def _relation(
    relation_type: str,
    record: dict[str, Any],
    identifier: str,
    *,
    from_refs: list[dict[str, str | None]] | None = None,
    to_refs: list[dict[str, str | None]] | None = None,
    member_refs: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    return {
        "relation_id": f"{relation_type}:{identifier}",
        "relation_type": relation_type,
        "source_object_id": identifier,
        "status": status or record["record_meta"]["state"],
        "from_refs": from_refs or [],
        "to_refs": to_refs or [],
        "member_refs": member_refs or [],
        "summary": _summary(next(prefix for prefix in _TYPE_NAMES if identifier.startswith(prefix)), record),
        "details": details or {},
    }


def _build_relations(data: dict[str, Any]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    for record in data["thought"]["support_edges"]:
        relations.append(
            _relation(
                "argument_support",
                record,
                record["edge_id"],
                from_refs=[_identity_ref(record["from_node_id"])],
                to_refs=[_identity_ref(record["to_node_id"])],
                details={
                    "target_part": record["target_part"],
                    "bridge": record["bridge"],
                    "combination": record["combination"],
                    "attribution": record["attribution"],
                    "extraction_status": record["extraction_status"],
                },
            )
        )
    for record in data["thought"]["candidate_records"]:
        if record["candidate_kind"] == "联系候选":
            relations.append(
                _relation(
                    "candidate_argument",
                    record,
                    record["candidate_id"],
                    from_refs=[_identity_ref(record["from_node_id"])] if record["from_node_id"] else [],
                    to_refs=[_identity_ref(record["to_node_id"])] if record["to_node_id"] else [],
                    details={"decision_reason": record["decision_reason"]},
                    status=record["decision"],
                )
            )
    for record in data["evidence"]["process_relations"]:
        relations.append(
            _relation(
                "processing_derivation",
                record,
                record["process_id"],
                from_refs=[_identity_ref(identifier) for identifier in record["input_evidence_ids"]],
                to_refs=[_identity_ref(identifier) for identifier in record["output_evidence_ids"]],
                details={
                    "process_type": record["process_type"],
                    "operation": record["operation"],
                    "parameters_or_model": record["parameters_or_model"],
                },
            )
        )
    for record in data["evidence"]["provenance_groups"]:
        relations.append(
            _relation(
                "shared_provenance",
                record,
                record["group_id"],
                member_refs=[
                    {"ref": _identity_ref(identifier), "role": None} for identifier in record["member_evidence_ids"]
                ],
                details={"shared_source_description": record["shared_source_description"]},
            )
        )
    for record in data["evidence"]["use_groups"]:
        relations.append(
            _relation(
                "combined_use",
                record,
                record["use_group_id"],
                member_refs=[
                    {"ref": _identity_ref(member["evidence_id"]), "role": member["role"]}
                    for member in record["members"]
                ],
                details={
                    "group_type": record["group_type"],
                    "combination_description": record["combination_description"],
                    "comparison_statement": record["comparison_statement"],
                },
            )
        )
    for record in data["evidence"]["mappings"]:
        subject = record["subject_ref"]
        target = record["target_ref"]
        relations.append(
            _relation(
                "evidence_use",
                record,
                record["mapping_id"],
                from_refs=[_identity_ref(subject["target_id"], subject["target_field"])],
                to_refs=[_identity_ref(target["target_id"], target["target_field"])],
                details={
                    "target_part": record["target_part"],
                    "usage_role": record["usage_role"],
                    "use_topic": record["use_topic"],
                    "attribution": record["attribution"],
                    "extraction_status": record["extraction_status"],
                },
            )
        )
    for record in data["evidence"]["candidate_mappings"]:
        subject = record["subject_ref"]
        relations.append(
            _relation(
                "candidate_evidence_use",
                record,
                record["candidate_id"],
                from_refs=[_identity_ref(subject["target_id"], subject["target_field"])],
                to_refs=[_identity_ref(ref["target_id"], ref["target_field"]) for ref in record["candidate_targets"]],
                details={
                    "resolved_mapping_ids": record["resolved_mapping_ids"],
                    "decision_reason": record["decision_reason"],
                },
                status=record["decision"],
            )
        )
    for record in data["review"]["review_items"]:
        targets = []
        if record["primary_target_ref"] is not None:
            target = record["primary_target_ref"]
            if target["kind"] == "object":
                targets.append(_identity_ref(target["target_id"], target["target_field"]))
        for target in record["related_target_refs"]:
            if target["kind"] == "object":
                targets.append(_identity_ref(target["target_id"], target["target_field"]))
        if targets:
            relations.append(
                _relation(
                    "reviews",
                    record,
                    record["review_id"],
                    to_refs=targets,
                    details={
                        "review_domain": record["review_domain"],
                        "finding_status": record["finding_status"],
                        "review_scope": record["review_scope"],
                        "issue_origin": record["issue_origin"],
                    },
                )
            )
    for record in data["review"]["coverage_records"]:
        targets = [
            _identity_ref(target["target_id"], target["target_field"])
            for target in record["target_refs"]
            if target["kind"] == "object"
        ]
        relations.append(
            _relation(
                "review_coverage",
                record,
                record["coverage_id"],
                to_refs=targets,
                details={
                    "scope_unit_id": record["scope_unit_id"],
                    "review_domain": record["review_domain"],
                    "check_status": record["check_status"],
                    "criterion_scope": record["criterion_scope"],
                },
            )
        )
    for record in data["review"]["claim_assessments"]:
        target = record["target_ref"]
        relations.append(
            _relation(
                "claim_assessment",
                record,
                record["assessment_id"],
                to_refs=[_identity_ref(target["target_id"], target["target_field"])],
                details={
                    "assessed_scope": record["assessed_scope"],
                    "conclusion": record["reliability_statement"]["conclusion"],
                    "unassessed_parts": record["unassessed_parts"],
                },
            )
        )
    return sorted(relations, key=lambda relation: relation["relation_id"])


def build_object_index(
    document: Note2Document,
    *,
    verification: dict[str, Any] | None = None,
    validate_report: bool = True,
) -> dict[str, Any]:
    """构建可丢弃、可重建的索引；不改写科学对象。"""
    from zotero_cli.ai_notes2.validation import validate_document, validate_verification_report

    validated = validate_document(document)
    if validate_report:
        validate_verification_report(validated, verification)
    data = validated.to_dict()
    records: dict[str, tuple[str, str, str, dict[str, Any]]] = {}
    for chain, table, identity, prefix in RECORD_TABLES:
        for number, record in enumerate(data[chain][table]):
            records[record[identity]] = (chain, prefix, f"/{chain}/{table}/{number}", record)

    source_pointers = {
        (block["source_set_id"], block["attachment_key"], block["block_id"]): f"/source_blocks/{number}"
        for number, block in enumerate(data["source_blocks"])
    }
    relations = _build_relations(data)
    relation_ids: dict[str, list[str]] = defaultdict(list)
    for relation in relations:
        relation_ids[relation["source_object_id"]].append(relation["relation_id"])
        for ref in relation["from_refs"] + relation["to_refs"]:
            relation_ids[str(ref["object_id"])].append(relation["relation_id"])
        for member in relation["member_refs"]:
            relation_ids[str(member["ref"]["object_id"])].append(relation["relation_id"])

    active_mapped_evidence: set[str] = set()
    active_use_groups: set[str] = set()
    for mapping in data["evidence"]["mappings"]:
        if mapping["record_meta"]["state"] != "active":
            continue
        identifier = mapping["subject_ref"]["target_id"]
        if identifier.startswith("EC-E"):
            active_mapped_evidence.add(identifier)
        elif identifier.startswith("EC-U"):
            active_use_groups.add(identifier)
    for group in data["evidence"]["use_groups"]:
        if group["use_group_id"] in active_use_groups:
            active_mapped_evidence.update(member["evidence_id"] for member in group["members"])
    candidate_evidence = {
        record["subject_ref"]["target_id"]
        for record in data["evidence"]["candidate_mappings"]
        if record["decision"] == "pending" and record["subject_ref"]["target_id"].startswith("EC-E")
    }
    assessed_claims = {
        record["target_ref"]["target_id"]
        for record in data["review"]["claim_assessments"]
        if record["record_meta"]["state"] == "active"
    }
    verdicts = {verdict["object_id"]: verdict for verdict in (verification or {}).get("verdicts", [])}
    model_used = bool((verification or {}).get("model_used"))

    objects: list[dict[str, Any]] = []
    for registry in data["object_registry"]:
        identifier = registry["object_id"]
        chain, prefix, pointer, record = records[identifier]
        flags: list[str] = []
        state = record["record_meta"]["state"]
        if state != "active":
            flags.append(state)
        if prefix in {"TC-C", "EC-C"}:
            flags.append("candidate")
            if record["decision"] == "pending":
                flags.append("unresolved_candidate")
        if prefix == "EC-E":
            statuses = {field["status"] for field in record["field_statuses"]}
            if statuses & {"当前输入未找到", "文本损坏"}:
                flags.append("missing_context")
            if "原文明确未报告或未测量" in statuses:
                flags.append("explicitly_not_reported")
            if state == "active" and identifier not in active_mapped_evidence:
                flags.append("unlinked_evidence")
                if identifier in candidate_evidence:
                    flags.append("candidate_link_only")
        if prefix == "TC-N" and state == "active" and identifier not in assessed_claims:
            flags.append("unassessed_claim")

        locations: list[dict[str, Any]] = []
        for relative_path, location in _walk_locations(record):
            source_key = (location["source_set_id"], location["attachment_key"], location["block_id"])
            locations.append(
                {
                    "source_set_id": source_key[0],
                    "attachment_key": source_key[1],
                    "block_id": source_key[2],
                    "span": location["span"],
                    "page_pdf": location["page_pdf"],
                    "page_label": location["page_label"],
                    "section_path": location["section_path"],
                    "record_location_pointer": pointer + relative_path,
                    "source_block_pointer": source_pointers[source_key],
                }
            )
        verdict = verdicts.get(identifier)
        if model_used and verdict is not None:
            source_assessment: dict[str, Any] = {
                "state": "recorded",
                "status": verdict["status"],
                "source_refs": verdict["source_refs"],
                "note": verdict["note"],
            }
            if verdict["status"] != "支持":
                flags.append("source_assessment_attention")
        else:
            source_assessment = {"state": "absent"}
            flags.append("source_assessment_absent")
        objects.append(
            {
                "short_id": identifier,
                "canonical_ref": {
                    "library_ref": data["library_ref"],
                    "parent_item_key": data["parent_item_key"],
                    "run_number": data["run_number"],
                    "object_id": identifier,
                },
                "index": registry["index"],
                "chain": chain,
                "chain_label": _CHAIN_NAMES[chain],
                "object_type": prefix,
                "type_label": _TYPE_NAMES[prefix],
                "state": state,
                "summary": _summary(prefix, record),
                "full_object_pointer": pointer,
                "source_locations": locations,
                "figure_refs": record.get("figure_refs", []),
                "relation_ids": sorted(set(relation_ids.get(identifier, []))),
                "flags": sorted(set(flags)),
                "model_source_assessment": source_assessment,
            }
        )

    flag_counts = Counter(flag for entry in objects for flag in entry["flags"])
    type_counts = Counter(entry["object_type"] for entry in objects)
    chain_counts = Counter(entry["chain"] for entry in objects)
    relation_counts = Counter(relation["relation_type"] for relation in relations)
    verification_digest = _canonical_digest(verification) if verification is not None else None
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "source": {
            "analysis_schema_version": data["schema_version"],
            "library_ref": data["library_ref"],
            "parent_item_key": data["parent_item_key"],
            "run_number": data["run_number"],
            "namespace": (
                f"{data['library_ref']['library_type']}:{data['library_ref']['library_id']}:"
                f"{data['parent_item_key']}:{data['run_number']:04d}"
            ),
            "analysis_sha256": _canonical_digest(data),
            "input_snapshot_sha256": _canonical_digest(
                {"inputs": data["inputs"], "source_blocks": data["source_blocks"]}
            ),
            "verification_sha256": verification_digest,
        },
        "validation_scope": {
            "schema_and_references": "passed",
            "source_location_consistency": "passed",
            "model_source_assessment": "recorded" if model_used else "absent",
            "scientific_correctness": "not_asserted",
            "figure_pixels": "not_examined",
        },
        "counts": {
            "objects": len(objects),
            "relations": len(relations),
            "by_chain": dict(sorted(chain_counts.items())),
            "by_type": dict(sorted(type_counts.items())),
            "by_relation_type": dict(sorted(relation_counts.items())),
            "flags": dict(sorted(flag_counts.items())),
        },
        "objects": objects,
        "relations": relations,
    }
