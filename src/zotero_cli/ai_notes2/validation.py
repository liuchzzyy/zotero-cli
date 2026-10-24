"""严格校验三链结构、冻结来源和图引用，并分配运行内身份。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy
from typing import Any, NoReturn

from zotero_cli.ai_notes2.models import RECORD_TABLES, Note2Document, note2_json_schema


class Note2ValidationError(ValueError):
    """携带 JSON Pointer 的结构错误，可用于有界结构修复。"""

    def __init__(self, path: str, message: str) -> None:
        self.path = path or "/"
        self.message = message
        super().__init__(f"{self.path}: {message}")


def _fail(path: str, message: str) -> NoReturn:
    raise Note2ValidationError(path, message)


def _pointer(path: str, key: str | int) -> str:
    return path + "/" + str(key).replace("~", "~0").replace("/", "~1")


def _check_schema(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "") -> None:
    """解释本模块生成的 Schema 子集；布尔值不作为整数接受。"""
    if "$ref" in schema:
        _check_schema(value, root["$defs"][schema["$ref"].split("/")[-1]], root, path)
        return
    if "anyOf" in schema:
        errors = []
        for option in schema["anyOf"]:
            try:
                _check_schema(value, option, root, path)
                return
            except Note2ValidationError as error:
                errors.append(error)
        # 保留结构最接近的分支位置，便于模型修复。
        raise max(errors, key=lambda error: error.path.count("/"))
    if "enum" in schema and not any(type(value) is type(option) and value == option for option in schema["enum"]):
        _fail(path, f"不在受控枚举中：{schema['enum']}")
    expected = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "null": type(None)}
    if expected and type(value) is not types[expected]:
        _fail(path, f"必须为 {expected}")
    if expected == "object":
        for key in schema.get("required", []):
            if key not in value:
                _fail(_pointer(path, key), "缺少必要字段")
        properties = schema.get("properties", {})
        for key, member in value.items():
            if key in properties:
                _check_schema(member, properties[key], root, _pointer(path, key))
            elif schema.get("additionalProperties") is False:
                _fail(_pointer(path, key), "未知字段")
            elif isinstance(schema.get("additionalProperties"), dict):
                _check_schema(member, schema["additionalProperties"], root, _pointer(path, key))
    elif expected == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            _fail(path, "数组长度不符合约束")
        if schema.get("uniqueItems") and len(
            {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in value}
        ) != len(value):
            _fail(path, "数组成员不得重复")
        for index, member in enumerate(value):
            _check_schema(member, schema["items"], root, _pointer(path, index))
    elif expected == "string" and len(value) < schema.get("minLength", 0):
        _fail(path, "字符串不能为空")
    elif expected == "integer" and value < schema.get("minimum", value):
        _fail(path, "整数超出允许范围")


def _walk(value: Any, path: str = "") -> Any:
    yield path, value
    if isinstance(value, dict):
        for key, member in value.items():
            yield from _walk(member, _pointer(path, key))
    elif isinstance(value, list):
        for index, member in enumerate(value):
            yield from _walk(member, _pointer(path, index))


def _resolve_pointer(value: Any, pointer: str, path: str) -> Any:
    if not pointer.startswith("/") or re.search(r"~(?![01])", pointer):
        _fail(path, "必须为有效的对象字段 JSON Pointer")
    for token in pointer[1:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
            index = int(key)
            if index >= len(value):
                _fail(path, "字段路径越界")
            value = value[index]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            _fail(path, "字段路径不存在")
    return value


def _acyclic(graph: dict[str, set[str]], path: str) -> None:
    """使用拓扑排序避免长处理链触发递归深度限制。"""
    degrees: dict[str, int] = {node: 0 for node in graph}
    for targets in graph.values():
        for target in targets:
            degrees[target] = degrees.get(target, 0) + 1
    pending = [node for node, degree in degrees.items() if degree == 0]
    visited = 0
    while pending:
        node = pending.pop()
        visited += 1
        for target in graph.get(node, set()):
            degrees[target] -= 1
            if degrees[target] == 0:
                pending.append(target)
    if visited != len(degrees):
        _fail(path, "图存在循环")


class _Validator:
    def __init__(self, document: dict[str, Any], *, assigned: bool, canonical: bool) -> None:
        self.document = document
        self.assigned = assigned
        self.canonical = canonical
        self.records: dict[str, tuple[str, dict[str, Any], str]] = {}
        self.blocks: dict[tuple[str, str, str], dict[str, Any]] = {}

    def run(self) -> None:
        schema = note2_json_schema()
        _check_schema(self.document, schema, schema)
        self._identities()
        self._sources()
        self._references()
        self._thought()
        self._evidence()
        self._review()

    def _identities(self) -> None:
        document = self.document
        for chain, table, identity, prefix in RECORD_TABLES:
            for number, record in enumerate(document[chain][table]):
                path = f"/{chain}/{table}/{number}"
                identifier = record[identity]
                if identifier in self.records:
                    _fail(path + "/" + identity, "对象 ID 重复")
                if self.canonical and not re.fullmatch(re.escape(prefix) + r"[0-9]{3,}", identifier):
                    _fail(path + "/" + identity, "类型 ID 必须使用类型前缀和至少三位正整数")
                if self.canonical and int(identifier[len(prefix) :]) < 1:
                    _fail(path + "/" + identity, "类型 ID 必须为正整数")
                self.records[identifier] = (prefix, record, path)
        registry = document["object_registry"]
        indexed = [record for _, record, _ in self.records.values() if "index" in record]
        if not self.assigned:
            if registry or indexed:
                _fail("/object_registry", "模型输入不能提供最终 index 或登记表")
            return
        if len(indexed) != len(self.records) or len(registry) != len(self.records):
            _fail("/object_registry", "登记表必须完整且与全部对象一一对应")
        seen_ids: set[str] = set()
        seen_indexes: set[str] = set()
        prefix = f"{document['parent_item_key']}-{document['run_number']:04d}-"
        for number, entry in enumerate(registry):
            path = f"/object_registry/{number}"
            identifier = entry["object_id"]
            if identifier not in self.records or identifier in seen_ids:
                _fail(path, "登记对象不存在或重复")
            kind, record, _ = self.records[identifier]
            index = entry["index"]
            suffix = index.removeprefix(prefix)
            if (
                not index.startswith(prefix)
                or not re.fullmatch(r"[0-9]{4,}", suffix)
                or int(suffix) < 1
                or suffix != f"{int(suffix):04d}"
            ):
                _fail(path + "/index", "全局 index 必须匹配父条目和运行序号")
            if index in seen_indexes or record["index"] != index or kind != entry["object_type"]:
                _fail(path, "全局 index 重复或登记内容不一致")
            seen_ids.add(identifier)
            seen_indexes.add(index)

    def _sources(self) -> None:
        document = self.document
        documents: dict[tuple[str, str], dict[str, Any]] = {}
        aliases: set[str] = set()
        for number, source in enumerate(document["inputs"]["documents"]):
            path = f"/inputs/documents/{number}"
            key = source["source_set_id"], source["attachment_key"]
            if key in documents:
                _fail(path, "来源文档重复")
            documents[key] = source
            source_aliases = {source["attachment_key"], *source["attachment_aliases"]}
            if aliases & source_aliases:
                _fail(path, "附件身份或别名不能重复登记")
            aliases.update(source_aliases)
            for field in ("pdf_sha256",):
                if not re.fullmatch(r"[0-9a-f]{64}", source[field]):
                    _fail(path + "/" + field, "摘要必须为 SHA-256 十六进制字符串")
            if not re.fullmatch(r"[0-9a-f]{64}", source["parse_metadata"]["parse_artifact_digest"]):
                _fail(path + "/parse_metadata/parse_artifact_digest", "解析摘要必须为 SHA-256")
        if not re.fullmatch(r"[0-9a-f]{64}", document["inputs"]["source_note"]["text_digest"]):
            _fail("/inputs/source_note/text_digest", "笔记摘要必须为 SHA-256")
        for number, block in enumerate(document["source_blocks"]):
            path = f"/source_blocks/{number}"
            key = block["source_set_id"], block["attachment_key"], block["block_id"]
            if key in self.blocks or key[:2] not in documents:
                _fail(path, "来源块重复或未登记附件")
            source = documents[key[:2]]
            if block["pdf_role"] != source["pdf_role"] and not (
                source["pdf_role"] == "COMBINED" and block["pdf_role"] in {"MS", "SI"}
            ):
                _fail(path + "/pdf_role", "块角色与冻结附件角色不一致")
            table = block["table"]
            if table is not None:
                for collection, identity in (("cells", "cell_id"), ("notes", "note_id")):
                    ids = [entry[identity] for entry in table[collection]]
                    if len(set(ids)) != len(ids):
                        _fail(path + "/table/" + collection, "表格定位 ID 重复")
            if block["block_type"] != "table" and table is not None:
                _fail(path + "/table", "只有表格块可以保存结构化表格")
            self.blocks[key] = block
        used = {key[:2] for key in self.blocks}
        if set(documents) != used:
            _fail("/source_blocks", "每个登记附件都必须有冻结来源块")
        for _, record, path in self.records.values():
            for relative, value in _walk(record):
                if isinstance(value, dict) and "block_id" in value and "text_excerpt" in value:
                    self._location(value, path + relative)
                    for pointer in value["applies_to"]:
                        _resolve_pointer(record, pointer, path + relative + "/applies_to")
            for number, figure in enumerate(record.get("figure_refs", [])):
                if not any(
                    key[:2] == (figure["source_set_id"], figure["attachment_key"])
                    and figure["figure_id"] in block["figure_ids"]
                    for key, block in self.blocks.items()
                ):
                    _fail(f"{path}/figure_refs/{number}", "图引用未在对应冻结附件的图注中登记")
        # 覆盖单位的原文引用也必须检查，不限于科学记录内部。
        for path, value in _walk(document["review"]["coverage_scope"], "/review/coverage_scope"):
            if isinstance(value, dict) and "block_id" in value and "text_excerpt" in value:
                self._location(value, path)

    def _location(self, location: dict[str, Any], path: str) -> None:
        key = location["source_set_id"], location["attachment_key"], location["block_id"]
        block = self.blocks.get(key)
        if block is None:
            _fail(path, "未知来源块或附件身份不匹配")
        for field in ("library_ref", "parent_item_key"):
            if location[field] != self.document[field]:
                _fail(path + "/" + field, "来源与文档库或父条目不一致")
        for field in ("pdf_role", "page_pdf", "page_label", "section_path"):
            if location[field] != block[field]:
                _fail(path + "/" + field, "来源定位与冻结块不一致")
        span = location["span"]
        text = block["text"]
        if span is not None:
            start, end = span
            if not 0 <= start < end <= len(text):
                _fail(path + "/span", "来源区间越界或为空")
            text = text[start:end]
        # 原文由冻结块填入；这里不比较科学陈述与原文是否逐字相同。
        if location["text_excerpt"] != text:
            _fail(path + "/text_excerpt", "原文片段必须来自冻结块指定区间")
        locator = location["table_locator"]
        if locator is not None:
            table = block["table"]
            if table is None or locator["table_id"] != table["table_id"]:
                _fail(path + "/table_locator", "未知表格")
            if not set(locator["cell_ids"]) <= {cell["cell_id"] for cell in table["cells"]}:
                _fail(path + "/table_locator/cell_ids", "未知表格单元格")
            if not set(locator["note_ids"]) <= {note["note_id"] for note in table["notes"]}:
                _fail(path + "/table_locator/note_ids", "未知表格脚注")
            if locator["header_path"] != table["header_path"]:
                _fail(path + "/table_locator/header_path", "表头路径不匹配")

    def _target(self, ref: dict[str, Any], allowed: set[str], path: str, *, active: bool = False) -> dict[str, Any]:
        if ref.get("kind") != "object" or ref["target_id"] not in self.records:
            _fail(path, "必须引用已登记对象")
        kind, record, _ = self.records[ref["target_id"]]
        if kind not in allowed:
            _fail(path, f"引用对象类型错误，允许：{sorted(allowed)}")
        if ref["target_field"] is not None and kind != "TC-L":
            _fail(path, "只有支持链可以引用 bridge")
        if active and record["record_meta"]["state"] != "active":
            _fail(path, "现行正式关系不能引用候选或退休对象")
        return record

    def _id(self, identifier: str, allowed: set[str], path: str, *, active: bool = False) -> dict[str, Any]:
        return self._target(
            {"kind": "object", "target_id": identifier, "target_field": None}, allowed, path, active=active
        )

    def _references(self) -> None:
        for path, value in _walk(self.document):
            if not isinstance(value, dict):
                continue
            if value.get("kind") == "object":
                self._target(value, {row[3] for row in RECORD_TABLES}, path)
            elif value.get("kind") == "criterion":
                source = self._id(value["criterion_source_id"], {"RC-K"}, path)
                if value["source_revision"] != source["source_revision"]:
                    _fail(path, "判准版本不匹配")
                positions = [entry for entry in source["locations"] if entry["location_id"] == value["location_id"]]
                if (
                    len(positions) != 1
                    or source["access_scope"] != "已读取相关内容"
                    or source["check_status"] != "已核对"
                    or not source["content_snapshot_ref"]
                    or not positions[0]["text_excerpt"]
                ):
                    _fail(path, "判准引用必须指向实际读取并核对的具体位置")
        for kind, record, path in self.records.values():
            meta = record["record_meta"]
            if kind not in {"TC-C", "EC-C"} and meta["state"] == "resolved":
                _fail(path + "/record_meta/state", "resolved 仅用于候选")
            if "attribution" in record and kind not in {"TC-C", "EC-C"}:
                pairs = {"作者明示": "明确", "文本重构": "可重构"}
                if pairs[record["attribution"]] != record["extraction_status"]:
                    _fail(path + "/extraction_status", "归属与提取状态不匹配")
            for relation in ("replaces", "replaced_by"):
                for ref in meta[relation]:
                    self._target(ref, {kind}, path + "/record_meta/" + relation)
                    if ref["target_id"] == next(
                        identifier for identifier, entry in self.records.items() if entry[1] is record
                    ):
                        _fail(path + "/record_meta/" + relation, "生命周期不能引用自身")

    def _candidate(self, record: dict[str, Any], path: str, results: list[Any]) -> None:
        decision = record["decision"]
        expected = {"pending": "active", "accepted": "resolved", "rejected": "retired", "withdrawn": "retired"}
        if record["record_meta"]["state"] != expected[decision]:
            _fail(path + "/decision", "候选决策与生命周期不匹配")
        if bool(results) != (decision == "accepted"):
            _fail(path, "只有已接受候选必须且可以有正式结果")
        if decision in {"rejected", "withdrawn"} and not record["decision_reason"]:
            _fail(path + "/decision_reason", "拒绝或撤回必须说明原因")

    def _thought(self) -> None:
        graph: dict[str, set[str]] = defaultdict(set)
        ranks = {"核心论点": 1, "分论点": 2, "论证理由": 3}
        for kind, record, path in self.records.values():
            active = record["record_meta"]["state"] == "active"
            if kind == "TC-N":
                if record["presentation_status"] == "层级待整理" and not record["presentation_note"]:
                    _fail(path + "/presentation_note", "层级待整理必须说明未表达部分")
            elif kind == "TC-L":
                start = self._id(record["from_node_id"], {"TC-N"}, path + "/from_node_id", active=active)
                end = self._id(record["to_node_id"], {"TC-N"}, path + "/to_node_id", active=active)
                if ranks[start["node_type"]] != ranks[end["node_type"]] + 1:
                    _fail(path, "支持方向必须为理由到分论点或分论点到核心论点，最多三级")
                if active:
                    graph[record["from_node_id"]].add(record["to_node_id"])
                combination = record["combination"]
                members = combination["edge_ids"]
                if combination["mode"] != "联合使用":
                    if members:
                        _fail(path + "/combination", "非联合支持不得列出联合成员")
                else:
                    if len(members) < 2 or record["edge_id"] not in members:
                        _fail(path + "/combination", "联合组至少两条边且包含自身")
                    for identifier in members:
                        member = self._id(identifier, {"TC-L"}, path + "/combination/edge_ids")
                        if (
                            member["to_node_id"] != record["to_node_id"]
                            or member["combination"]["mode"] != "联合使用"
                            or set(member["combination"]["edge_ids"]) != set(members)
                            or member["record_meta"]["state"] != record["record_meta"]["state"]
                        ):
                            _fail(path + "/combination", "联合组的目标、成员及生命周期必须一致")
            elif kind == "TC-C":
                results = record["resolution_refs"]
                self._candidate(record, path, results)
                if record["candidate_kind"] == "命题候选":
                    if record["from_node_id"] is not None or record["to_node_id"] is not None:
                        _fail(path, "命题候选不能有边端点")
                for field in ("from_node_id", "to_node_id"):
                    if record[field] is not None:
                        self._id(record[field], {"TC-N"}, path + "/" + field)
                for ref in results:
                    allowed = {"TC-N"} if record["candidate_kind"] == "命题候选" else {"TC-L"}
                    target = self._target(ref, allowed, path + "/resolution_refs")
                    if ref["target_field"] is not None:
                        _fail(path + "/resolution_refs", "候选必须形成完整正式对象")
                    if allowed == {"TC-N"} and record["proposed_node_type"] is not None:
                        if record["proposed_node_type"] != target["node_type"]:
                            _fail(path + "/resolution_refs", "接受结果与提议节点类型不一致")
                    if allowed == {"TC-L"}:
                        for field in ("from_node_id", "to_node_id"):
                            if record[field] is not None and record[field] != target[field]:
                                _fail(path + "/resolution_refs", "接受结果与候选端点不一致")
        _acyclic(graph, "/thought/support_edges")

    def _evidence(self) -> None:
        graph: dict[str, set[str]] = defaultdict(set)
        for kind, record, path in self.records.values():
            active = record["record_meta"]["state"] == "active"
            if kind == "EC-E":
                if record["primary_aspect"] in record["additional_aspects"]:
                    _fail(path + "/additional_aspects", "附加角度不能包含主要角度")
                statuses: dict[str, dict[str, Any]] = {}
                for number, status in enumerate(record["field_statuses"]):
                    pointer = status["path"]
                    if pointer in statuses:
                        _fail(path + "/field_statuses", "字段状态重复")
                    _resolve_pointer(record, pointer, path + f"/field_statuses/{number}/path")
                    statuses[pointer] = status
                    if status["status"] != "已定位" and not status["reason"]:
                        _fail(path + f"/field_statuses/{number}/reason", "缺失或不适用状态必须说明原因")
                    if status["status"] == "已定位" and not status["source_refs"]:
                        _fail(path + f"/field_statuses/{number}", "已定位字段必须有来源")
                # 科学字段的显式未知必须逐字段登记，状态记录自身不递归要求状态。
                for field in (
                    "primary_aspect",
                    "object_and_conditions",
                    "method_or_acquisition",
                    "reported_result",
                    "author_interpretation",
                    "reference_info",
                ):
                    for pointer, value in _walk(record[field], "/" + field):
                        if value is None and pointer not in statuses:
                            _fail(path + pointer, "未知科学字段必须有对应 field_statuses")
                interpretation = record["author_interpretation"]
                if interpretation is not None and interpretation["mode"] == "ref":
                    ref = interpretation["ref"]
                    self._target(ref, {"TC-N", "TC-L"}, path + "/author_interpretation/ref")
                    if (
                        ref["target_id"] in self.records
                        and self.records[ref["target_id"]][0] == "TC-L"
                        and ref["target_field"] != "bridge"
                    ):
                        _fail(path + "/author_interpretation/ref", "作者解释只能引用节点或推理桥")
            elif kind == "EC-P":
                inputs, outputs = record["input_evidence_ids"], record["output_evidence_ids"]
                if set(inputs) & set(outputs):
                    _fail(path, "处理输入输出不能相交")
                for identifier in inputs + outputs:
                    self._id(identifier, {"EC-E"}, path, active=active)
                if active:
                    for identifier in inputs:
                        graph[identifier].update(outputs)
            elif kind in {"EC-G", "EC-U"}:
                members = (
                    record["member_evidence_ids"]
                    if kind == "EC-G"
                    else [entry["evidence_id"] for entry in record["members"]]
                )
                if len(set(members)) != len(members):
                    _fail(path, "组成员不能重复")
                for identifier in members:
                    self._id(identifier, {"EC-E"}, path, active=active)
            elif kind in {"EC-M", "EC-C"}:
                self._target(record["subject_ref"], {"EC-E", "EC-U"}, path + "/subject_ref", active=active)
                if kind == "EC-M":
                    self._target(record["target_ref"], {"TC-N", "TC-L"}, path + "/target_ref", active=active)
                else:
                    self._candidate(record, path, record["resolved_mapping_ids"])
                    for ref in record["candidate_targets"]:
                        self._target(ref, {"TC-N", "TC-L"}, path + "/candidate_targets")
                    for identifier in record["resolved_mapping_ids"]:
                        mapping = self._id(identifier, {"EC-M"}, path + "/resolved_mapping_ids")
                        if (
                            mapping["subject_ref"] != record["subject_ref"]
                            or record["candidate_targets"]
                            and mapping["target_ref"] not in record["candidate_targets"]
                        ):
                            _fail(path + "/resolved_mapping_ids", "正式用途必须对应候选主体与已提议目标")
        _acyclic(graph, "/evidence/process_relations")

    def _review(self) -> None:
        review = self.document["review"]
        scope = review["coverage_scope"]
        units: dict[str, dict[str, Any]] = {}
        for number, unit in enumerate(scope["units"]):
            path = f"/review/coverage_scope/units/{number}"
            if unit["scope_unit_id"] in units:
                _fail(path, "覆盖范围单位重复")
            units[unit["scope_unit_id"]] = unit
            self._domain(unit, path)
        coverages: dict[str, dict[str, Any]] = {}
        criteria = review["criterion_sources"]
        if bool(criteria) != bool(review["criterion_source_set_id"]):
            _fail("/review/criterion_source_set_id", "外部判准集合与登记来源必须同时存在或同时为空")
        for kind, record, path in self.records.values():
            if kind in {"RC-Q", "RC-S"}:
                self._domain(record, path)
            if kind == "RC-B" and record["parent_item_ref"] is not None:
                for field in ("library_ref", "parent_item_key"):
                    if record["parent_item_ref"][field] != self.document[field]:
                        _fail(path + "/parent_item_ref", "背景必须引用当前父条目")
            elif kind == "RC-K":
                ids = [entry["location_id"] for entry in record["locations"]]
                if len(ids) != len(set(ids)):
                    _fail(path + "/locations", "判准位置 ID 重复")
            elif kind == "RC-Q":
                for field, allowed in (("related_review_refs", {"RC-Q"}), ("suggestion_refs", {"RC-D"})):
                    self._typed_refs(record[field], allowed, path + "/" + field)
                if record["finding_status"] is not None:
                    if record["primary_target_ref"] is None or not record["basis_refs"]:
                        _fail(path, "实际判断必须有主要目标和依据")
                if record["finding_status"] == "未发现明确问题" and record["problem_locus"] is not None:
                    _fail(path + "/problem_locus", "无明确问题时问题所在层必须为空")
                if (
                    record["criterion"]["criterion_type"] == "特定领域规则"
                    and record["finding_status"] not in {None, "当前无法判断"}
                    and not record["criterion"]["criterion_source_refs"]
                ):
                    _fail(path + "/criterion", "特定领域规则的实际裁决需要已读取的判准引用")
            elif kind == "RC-S":
                self._coverage(record, path, units, coverages)
            elif kind == "RC-A":
                self._assessment(record, path)
            elif kind == "RC-D":
                self._target(
                    record["target_ref"], {"TC-N", "TC-L", "EC-E", "EC-P", "EC-G", "EC-U", "EC-M"}, path + "/target_ref"
                )
                self._typed_refs(record["review_refs"], {"RC-Q"}, path + "/review_refs")
                self._typed_refs(record["assessment_refs"], {"RC-A"}, path + "/assessment_refs")
                self._typed_refs(record["verification_refs"], {"RC-Q"}, path + "/verification_refs")
                if record["status"] in {"已落实待复核", "复核完成"} and not record["result_refs"]:
                    _fail(path + "/result_refs", "已落实建议必须有实际结果")
                if record["status"] == "复核完成":
                    if not record["verification_refs"]:
                        _fail(path + "/verification_refs", "复核完成必须有实际审查")
                    for ref in record["verification_refs"]:
                        question = self._target(ref, {"RC-Q"}, path + "/verification_refs")
                        if (
                            question["finding_status"] in {None, "当前无法判断"}
                            or question["primary_target_ref"] not in record["result_refs"]
                        ):
                            _fail(path + "/verification_refs", "复核必须对实际结果完成判断")
        if scope["declaration"] == "已完成声明范围":
            if not units or set(coverages) != set(units):
                _fail("/review/coverage_scope", "完成声明必须覆盖非空的全部范围单位")
            if any(record["check_status"] not in {"已检查", "不适用"} for record in coverages.values()):
                _fail("/review/coverage_scope", "未检查或当前不可检查不能声明完成")

    def _typed_refs(self, refs: list[dict[str, Any]], allowed: set[str], path: str) -> None:
        for number, ref in enumerate(refs):
            self._target(ref, allowed, path + f"/{number}")
            if ref["target_field"] is not None:
                _fail(path + f"/{number}", "此处必须引用完整对象")

    @staticmethod
    def _domain(record: dict[str, Any], path: str) -> None:
        if (record["review_domain"] == "来源与一致性") != (record["consistency_subtype"] is not None):
            _fail(path + "/consistency_subtype", "一致性子类必须与审查领域配对")

    def _coverage(self, record: dict[str, Any], path: str, units: dict[str, Any], coverages: dict[str, Any]) -> None:
        identifier = record["scope_unit_id"]
        if identifier not in units:
            _fail(path + "/scope_unit_id", "覆盖单位未声明")
        for field in ("target_refs", "review_domain", "consistency_subtype", "criterion_scope"):
            if record[field] != units[identifier][field]:
                _fail(path + "/" + field, "覆盖记录与声明范围不一致")
        if record["record_meta"]["state"] == "active":
            if identifier in coverages:
                _fail(path, "同一单位只能有一条现行覆盖记录")
            coverages[identifier] = record
        self._typed_refs(record["review_refs"], {"RC-Q"}, path + "/review_refs")
        self._typed_refs(record["assessment_refs"], {"RC-A"}, path + "/assessment_refs")
        status = record["check_status"]
        if status == "已检查" and (not record["review_refs"] or not record["inspected_inputs"]):
            _fail(path, "已检查必须有实际输入和完成判断的 RC-Q")
        if status == "未检查" and record["review_refs"]:
            _fail(path + "/review_refs", "未检查不能借用其他审查")
        if (status == "当前不可检查") != bool(record["unavailable_reason"]):
            _fail(path + "/unavailable_reason", "当前不可检查必须且仅在此状态说明原因")
        if (status == "不适用") != bool(record["not_applicable_reason"]):
            _fail(path + "/not_applicable_reason", "不适用必须且仅在此状态说明原因")
        for ref in record["review_refs"]:
            question = self._target(ref, {"RC-Q"}, path + "/review_refs", active=True)
            if (
                question["primary_target_ref"] not in record["target_refs"]
                or question["review_domain"] != record["review_domain"]
                or question["consistency_subtype"] != record["consistency_subtype"]
                or question["review_scope"] != record["criterion_scope"]
            ):
                _fail(path + "/review_refs", "RC-Q 目标、领域和实际检查范围必须对应覆盖单位")
            if status == "已检查" and question["finding_status"] in {None, "当前无法判断"}:
                _fail(path + "/review_refs", "已检查必须引用已完成判断")
            if status == "当前不可检查" and question["finding_status"] != "当前无法判断":
                _fail(path + "/review_refs", "当前不可检查只能引用无法判断的尝试")

    def _assessment(self, record: dict[str, Any], path: str) -> None:
        self._target(record["target_ref"], {"TC-N"}, path + "/target_ref")
        for field, allowed in (
            ("background_refs", {"RC-B"}),
            ("evidence_refs", {"EC-E", "EC-P", "EC-G", "EC-U"}),
            ("mapping_refs", {"EC-M"}),
            ("coverage_refs", {"RC-S"}),
        ):
            self._typed_refs(record[field], allowed, path + "/" + field)
        for field in ("evidence_review", "logic_review", "unresolved_reviews"):
            refs = [entry["review_ref"] for entry in record[field]]
            self._typed_refs(refs, {"RC-Q"}, path + "/" + field)
            for ref in refs:
                question = self._target(ref, {"RC-Q"}, path + "/" + field)
                if question["primary_target_ref"] is None or question["finding_status"] is None:
                    _fail(path + "/" + field, "未定位或未检查审查不能进入正式综合")
        retained = record["retained_claim"]
        if retained is not None:
            suggestion = self._target(retained["suggestion_ref"], {"RC-D"}, path + "/retained_claim")
            if suggestion["target_ref"] != record["target_ref"]:
                _fail(path + "/retained_claim", "保留命题建议必须针对同一命题")
        if record["reliability_statement"]["scope"] != record["assessed_scope"]:
            _fail(path + "/reliability_statement/scope", "可靠性范围必须对应评议范围")
        if record["reliability_statement"]["conclusion"] in {"当前材料内支持较充分", "有支持但须限定范围"}:
            if not record["supporting_basis"] or not record["coverage_refs"]:
                _fail(path, "正向评议需要正向材料与实际覆盖，不能从空问题表推导")
            completed = False
            for ref in record["coverage_refs"]:
                coverage = self._target(ref, {"RC-S"}, path + "/coverage_refs", active=True)
                if coverage["check_status"] == "已检查":
                    completed = True
            if not completed:
                _fail(path + "/coverage_refs", "正向评议必须引用完成判断的覆盖")


def validate_document(document: dict[str, Any] | Note2Document) -> Note2Document:
    """校验最终规范文档，返回隔离副本；不修改调用者数据。"""
    data = document.to_dict() if isinstance(document, Note2Document) else deepcopy(document)
    _Validator(data, assigned=True, canonical=True).run()
    return Note2Document(data)


def allocate_indexes(document: dict[str, Any], *, start: int = 1) -> Note2Document:
    """接收临时 ID，校验后统一改写类型 ID、所有引用和全局号。

    运行号由外部持久层预留；本函数只在给定运行内分配对象号，不承担并发预留。
    已编号文档应使用 validate_document，不能再次编号。start 可供同次运行追加对象。
    """
    if type(start) is not int or start < 1:
        _fail("/start", "起始对象序号必须为正整数")
    data = deepcopy(document)
    validator = _Validator(data, assigned=False, canonical=False)
    validator.run()
    identifiers: dict[str, str] = {}
    ordered = []
    for chain, table, identity, prefix in RECORD_TABLES:
        for number, record in enumerate(data[chain][table], 1):
            old_id = record[identity]
            identifiers[old_id] = f"{prefix}{number:03d}"
            ordered.append((record, identity, prefix, old_id))
    # 只改写契约声明的引用字段；与引用同名的对象主键留到编号阶段处理。
    scalar_keys = {"target_id", "from_node_id", "to_node_id", "evidence_id", "criterion_source_id"}
    list_keys = {"edge_ids", "input_evidence_ids", "output_evidence_ids", "member_evidence_ids", "resolved_mapping_ids"}
    primary_keys = {id(record): identity for record, identity, _, _ in ordered}
    for _, value in _walk(data):
        if isinstance(value, dict):
            for key in scalar_keys & value.keys():
                if key != primary_keys.get(id(value)) and value[key] in identifiers:
                    value[key] = identifiers[value[key]]
            for key in list_keys & value.keys():
                value[key] = [identifiers[identifier] for identifier in value[key]]
    for offset, (record, identity, prefix, old_id) in enumerate(ordered, start):
        # 主键始终从原始临时 ID 映射，避免与其他对象的规范 ID 相同时二次改写。
        identifier = identifiers[old_id]
        record[identity] = identifier
        index = f"{data['parent_item_key']}-{data['run_number']:04d}-{offset:04d}"
        record["index"] = index
        data["object_registry"].append({"object_id": identifier, "object_type": prefix, "index": index})
    return validate_document(data)


def populate_source_excerpts(document: dict[str, Any]) -> dict[str, Any]:
    """从冻结来源填充引文；仅负责区间和来源身份，完整结构仍须校验。"""
    data = deepcopy(document)
    blocks: dict[tuple[str, str, str], dict[str, Any]] = {}
    for number, block in enumerate(data["source_blocks"]):
        key = block["source_set_id"], block["attachment_key"], block["block_id"]
        if key in blocks:
            _fail(f"/source_blocks/{number}", "来源块重复")
        blocks[key] = block
    for path, value in _walk(data):
        if isinstance(value, dict) and "block_id" in value and "text_excerpt" in value:
            key = value["source_set_id"], value["attachment_key"], value["block_id"]
            if key not in blocks:
                _fail(path, "未知来源块")
            text = blocks[key]["text"]
            span = value["span"]
            if span is not None:
                if (
                    type(span) is not list
                    or len(span) != 2
                    or any(type(part) is not int for part in span)
                    or not 0 <= span[0] < span[1] <= len(text)
                ):
                    _fail(path + "/span", "来源区间越界或无效")
                text = text[span[0] : span[1]]
            value["text_excerpt"] = text
    return data


def parse_document(raw: str) -> Note2Document:
    """严格读取最终 JSON；拒绝重复键、非有限数字、围栏和尾随内容。"""

    def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in entries:
            if key in result:
                _fail("/", f"JSON 字段重复：{key}")
            result[key] = value
        return result

    try:
        data = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=lambda value: _fail("/", f"JSON 非法常量：{value}")
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise Note2ValidationError("/", f"JSON 无效：{error}") from error
    return validate_document(data)


def resolve_index(document: Note2Document, reference: dict[str, Any]) -> str:
    """取得正式对象或桥的可见号，桥不单独占用对象序号。"""
    data = validate_document(document).to_dict()
    schema = note2_json_schema()
    _check_schema(reference, schema["$defs"]["ObjectRef"], schema, "/reference")
    identifier = reference["target_id"]
    for entry in data["object_registry"]:
        if entry["object_id"] == identifier:
            if reference["target_field"] == "bridge" and entry["object_type"] != "TC-L":
                _fail("/reference", "只有支持链具有 bridge")
            return entry["index"] + (".bridge" if reference["target_field"] else "")
    _fail("/reference", "未知对象")
