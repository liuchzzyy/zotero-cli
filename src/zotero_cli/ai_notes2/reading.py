"""将已登记的论点、证据和审查组织为阅读单元。"""

from __future__ import annotations

from typing import Any

from zotero_cli.ai_notes2.models import Note2Document


def build_reading_sections(document: Note2Document, verification: dict[str, Any] | None) -> list[tuple[int, str]]:
    """只投影直接登记的关系，不推导新的科学判断。"""
    data = document.to_dict()
    thought, evidence, review = data["thought"], data["evidence"], data["review"]
    assessments = review["claim_assessments"]
    verdicts = {v["object_id"]: v for v in (verification or {}).get("verdicts", [])}
    records = {r["evidence_id"]: r for r in evidence["evidence_records"]}
    groups = {g["use_group_id"]: g for g in evidence["use_groups"]}
    nodes = {n["node_id"]: n for n in thought["nodes"]}
    edges = [e for e in thought["support_edges"] if e["record_meta"]["state"] == "active"]
    mappings = [m for m in evidence["mappings"] if m["record_meta"]["state"] == "active"]
    questions = [q for q in review["review_items"] if q["record_meta"]["state"] == "active"]
    sections: list[tuple[int, str]] = [
        (2, "运行与边界"),
        (
            0,
            f"父条目 {data['parent_item_key']}；运行 {data['run_number']:04d}；运行标识 {data['parent_item_key']}-{data['run_number']:04d}-。",
        ),
        (0, "输入包括文本、表格文字、公式与图注；未检查图像像素。来源评估不等于科学结论成立。"),
        (0, f"图形附件：{data['parent_item_key']}-{data['run_number']:04d}-tree.svg。"),
    ]
    for document_source in data["inputs"]["documents"]:
        sections.append(
            (
                0,
                f"来源附件 {document_source['attachment_key']}；角色 {document_source['pdf_role']}；{document_source['document_identity_status']}。",
            )
        )

    def source(record: dict[str, Any]) -> str:
        locs = record.get("text_locations", [])
        if not locs:
            return "来源：通过对象引用回溯。"
        return (
            "来源："
            + "；".join(f"{loc['attachment_key']} / PDF p.{loc['page_pdf'] or '?'} / {loc['block_id']}" for loc in locs)
            + "。"
        )

    def verdict(identifier: str) -> str:
        item = verdicts.get(identifier)
        return f"来源评估：{item['status']}；{item['note']}" if item else "来源评估：未提供。"

    displayed_reviews: set[str] = set()

    def put_question(question: dict[str, Any]) -> None:
        displayed_reviews.add(question["review_id"])
        actual = question.get("primary_target_ref") or {}
        if actual.get("kind") == "source":
            location = actual["source_ref"]
            target = f"{location['attachment_key']} / {location['block_id']}"
        elif actual.get("kind") == "criterion":
            target = f"{actual['criterion_source_id']} / {actual['location_id']}"
        else:
            target = actual.get("target_id", "未指定") + (".bridge" if actual.get("target_field") == "bridge" else "")
        sections.append(
            (
                0,
                f"定点审查 {question['review_id']}｜{question['index']}（主要目标 {target}）：{question['question']} 判准：{question['criterion']['statement']} 依据：{question['observed_basis']} 推理：{question['reasoning']} 判断：{question['finding_status'] or '尚未判断'}；范围：{question['review_scope']}；影响：{question['direct_effect']}；状态：{question['resolution_status'] or '未提供'}。{verdict(question['review_id'])}",
            )
        )

    def put_node(node: dict[str, Any], level: int) -> None:
        node_id = node["node_id"]
        sections.append((level, f"{node['node_type']} {node_id}｜{node['index']}｜{node['text']}"))
        sections.append((0, f"作者表述：{node['attribution']}；{source(node)} {verdict(node_id)}"))
        related_edges = [e for e in edges if e["from_node_id"] == node_id]
        targets = {node_id, *(e["edge_id"] for e in related_edges)}
        for edge in related_edges:
            sections.append(
                (
                    0,
                    f"作者推理 {edge['edge_id']}｜{edge['index']}：{edge['bridge']}。目标 {edge['to_node_id']}；{edge['attribution']}。{source(edge)} {verdict(edge['edge_id'])}",
                )
            )
        related_maps = [m for m in mappings if m["target_ref"]["target_id"] in targets]
        related_material: set[str] = set()
        for mapping in related_maps:
            target = mapping["target_ref"]
            suffix = ".bridge" if target["target_field"] == "bridge" else ""
            subject_id = mapping["subject_ref"]["target_id"]
            related_material.update((subject_id, mapping["mapping_id"]))
            sections.append(
                (
                    0,
                    f"作者用途 {mapping['mapping_id']}｜{mapping['index']}：{subject_id} → {target['target_id']}{suffix}；{mapping['usage_description']}。{source(mapping)} {verdict(mapping['mapping_id'])}",
                )
            )
            subjects = (
                [records[subject_id]]
                if subject_id in records
                else [
                    records[m["evidence_id"]]
                    for m in groups.get(subject_id, {}).get("members", [])
                    if m["evidence_id"] in records
                ]
            )
            for item in subjects:
                eid = item["evidence_id"]
                related_material.add(eid)
                result = item["reported_result"]
                conditions = "；".join(
                    f"{v['name']}：{v['raw_value'] or '未提供'} {v['unit_raw'] or ''}（{v['applies_to']}）"
                    for v in item["object_and_conditions"]["conditions"]
                )
                method = item["method_or_acquisition"]["description"] or "未提供"
                qualifiers = "；".join(result["original_qualifiers"]) or "未提供"
                sections.append(
                    (
                        0,
                        f"报告材料 {eid}｜{item['index']}：{result['statement']}；类型 {item['result_type']}；对象 {item['object_and_conditions']['description']}；方法 {method}；条件 {conditions or '未提供'}；限定 {qualifiers}；不确定度 {result['uncertainty'] or '未提供'}。{source(item)} {verdict(eid)}",
                    )
                )
        for question in questions:
            refs = [question.get("primary_target_ref"), *question["related_target_refs"]]
            matched = [
                r
                for r in refs
                if isinstance(r, dict) and r.get("kind") == "object" and r["target_id"] in targets | related_material
            ]
            if not matched:
                continue
            put_question(question)
        relevant = [
            a for a in assessments if a["record_meta"]["state"] == "active" and a["target_ref"]["target_id"] == node_id
        ]
        if relevant:
            for a in relevant:
                result = a["reliability_statement"]
                gaps = "；".join(f"{x['part']}：{x['reason']}" for x in a["unassessed_parts"]) or "无已登记项"
                sections.append(
                    (
                        0,
                        f"综合评议 {a['assessment_id']}｜{a['index']}：{result['conclusion']}；范围：{result['scope']}；理由：{result['rationale']}；未评部分：{gaps}。{verdict(a['assessment_id'])}",
                    )
                )
        else:
            sections.append((0, "未有针对该命题的综合评议。"))

    sections.append((2, "核心论点"))
    visited: set[str] = set()
    for core in [n for n in nodes.values() if n["node_type"] == "核心论点" and n["record_meta"]["state"] == "active"]:
        put_node(core, 3)
        visited.add(core["node_id"])
        for edge in [e for e in edges if e["to_node_id"] == core["node_id"]]:
            branch = nodes[edge["from_node_id"]]
            put_node(branch, 3)
            visited.add(branch["node_id"])
            for child_edge in [e for e in edges if e["to_node_id"] == branch["node_id"]]:
                child = nodes[child_edge["from_node_id"]]
                put_node(child, 4)
                visited.add(child["node_id"])
    for node in nodes.values():
        if node["node_id"] not in visited and node["record_meta"]["state"] == "active":
            put_node(node, 3)
    sections.append((2, "未关联材料与待核范围"))
    linked = {m["subject_ref"]["target_id"] for m in mappings}
    for group_id in linked & groups.keys():
        linked.update(member["evidence_id"] for member in groups[group_id]["members"])
    for identifier, item in records.items():
        if identifier not in linked:
            sections.append(
                (
                    0,
                    f"未关联材料 {identifier}｜{item['index']}：{item['reported_result']['statement']}。{source(item)} {verdict(identifier)}",
                )
            )
            for question in questions:
                refs = [question.get("primary_target_ref"), *question["related_target_refs"]]
                if any(
                    isinstance(ref, dict) and ref.get("kind") == "object" and ref.get("target_id") == identifier
                    for ref in refs
                ):
                    put_question(question)
    for question in questions:
        if question["review_id"] not in displayed_reviews:
            put_question(question)
    for candidate in thought["candidate_records"] + evidence["candidate_mappings"]:
        if candidate["decision"] == "pending":
            sections.append((0, f"候选待核：{candidate['unresolved_description']}。"))
    for coverage in review["coverage_records"]:
        if coverage["check_status"] != "已检查":
            sections.append(
                (0, f"覆盖 {coverage['coverage_id']}：{coverage['criterion_scope']}；{coverage['check_status']}。")
            )
    return sections
