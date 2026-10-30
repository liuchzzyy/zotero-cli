"""单篇三链笔记的严格结构契约；不包含历史分链或发布格式。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "ai-note2/1.0"
SOURCE_ASSESSMENT_STATUSES = ("支持", "部分支持", "不支持", "无法核对")


def _object(**properties: Any) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array(items: Any, minimum: int = 0, *, unique: bool = False) -> dict[str, Any]:
    return {"type": "array", "items": items, "minItems": minimum, "uniqueItems": unique}


def _enum(*values: str | None) -> dict[str, Any]:
    return {"enum": list(values)}


def _nullable(schema: Any) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


_TEXT = {"type": "string", "minLength": 1}
_STRING = {"type": "string"}
_INT = {"type": "integer", "minimum": 1}
_STRINGS = _array(_TEXT, unique=True)
_NULL_TEXT = _nullable(_TEXT)
_ASPECT = _enum("制备", "表征", "性能", "理论", "机制")
_DOMAIN = _enum("来源与一致性", "实验条件", "测试参数", "数据分析", "结论匹配", "实证推断", "支持关系与推理桥")
_SUBTYPE = _enum("提取一致", "论文内部一致", "物理化学一致", None)
_NODE_TYPE = _enum("核心论点", "分论点", "论证理由")
_ROLE = _enum("MS", "SI", "COMBINED", "UNKNOWN", "RELATED")
_ATTRIBUTION = _enum("作者明示", "文本重构")
_EXTRACTION = _enum("明确", "可重构")
_FINDING = _enum("未发现明确问题", "存在条件性疑点", "已定位具体问题", "当前无法判断", None)
_BACKGROUND_STATUS = _enum("未取得", "已记录待核对", "已核对", "存在冲突", "不适用")
_REFS = _array(_ref("ObjectRef"))
_MATERIALS = _array(_ref("MaterialRef"))
_LOCATIONS = _array(_ref("Location"), 1)
_SOURCES = _array(_ref("SourceRef"))

# 表顺序仅用于首次编号；显示顺序不参与身份分配。
RECORD_TABLES = (
    ("thought", "nodes", "node_id", "TC-N"),
    ("thought", "support_edges", "edge_id", "TC-L"),
    ("thought", "candidate_records", "candidate_id", "TC-C"),
    ("evidence", "evidence_records", "evidence_id", "EC-E"),
    ("evidence", "process_relations", "process_id", "EC-P"),
    ("evidence", "provenance_groups", "group_id", "EC-G"),
    ("evidence", "use_groups", "use_group_id", "EC-U"),
    ("evidence", "mappings", "mapping_id", "EC-M"),
    ("evidence", "candidate_mappings", "candidate_id", "EC-C"),
    ("review", "background_records", "background_id", "RC-B"),
    ("review", "criterion_sources", "criterion_source_id", "RC-K"),
    ("review", "review_items", "review_id", "RC-Q"),
    ("review", "coverage_records", "coverage_id", "RC-S"),
    ("review", "claim_assessments", "assessment_id", "RC-A"),
    ("review", "change_suggestions", "suggestion_id", "RC-D"),
)

_DEFS: dict[str, Any] = {
    "LibraryRef": _object(library_type=_enum("user", "group"), library_id=_TEXT),
    "ObjectRef": _object(kind=_enum("object"), target_id=_TEXT, target_field=_enum("bridge", None)),
    "SourceRef": _object(kind=_enum("source"), source_ref=_ref("Location")),
    "CriterionRef": _object(
        kind=_enum("criterion"), criterion_source_id=_TEXT, source_revision=_INT, location_id=_TEXT
    ),
    "MaterialRef": {"anyOf": [_ref("ObjectRef"), _ref("SourceRef"), _ref("CriterionRef")]},
    "TableLocator": _object(table_id=_TEXT, cell_ids=_STRINGS, header_path=_array(_TEXT), note_ids=_STRINGS),
    "Location": _object(
        library_ref=_ref("LibraryRef"),
        parent_item_key=_TEXT,
        source_set_id=_TEXT,
        attachment_key=_TEXT,
        block_id=_TEXT,
        span=_nullable({"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 2, "maxItems": 2}),
        pdf_role=_ROLE,
        page_pdf=_nullable(_INT),
        page_label=_NULL_TEXT,
        section_path=_array(_TEXT),
        text_excerpt=_STRING,
        table_locator=_nullable(_ref("TableLocator")),
        applies_to=_STRINGS,
    ),
    "SourceBlock": _object(
        source_set_id=_TEXT,
        attachment_key=_TEXT,
        block_id=_TEXT,
        block_type=_enum("text", "table", "equation", "caption", "footnote", "reference"),
        pdf_role=_ROLE,
        page_pdf=_nullable(_INT),
        page_label=_NULL_TEXT,
        section_path=_array(_TEXT),
        text=_STRING,
        table=_nullable(_ref("Table")),
        figure_ids=_STRINGS,
    ),
    "Table": _object(
        table_id=_TEXT,
        cells=_array(_object(cell_id=_TEXT, text=_STRING)),
        header_path=_array(_TEXT),
        notes=_array(_object(note_id=_TEXT, text=_STRING)),
    ),
    "FigureRef": _object(source_set_id=_TEXT, attachment_key=_TEXT, figure_id=_TEXT),
    "RecordMeta": _object(
        state=_enum("active", "resolved", "retired"),
        replaces=_REFS,
        replaced_by=_REFS,
        note=_NULL_TEXT,
    ),
    "Condition": _object(name=_TEXT, raw_value=_NULL_TEXT, unit_raw=_NULL_TEXT, applies_to=_TEXT, source_refs=_SOURCES),
    "FieldStatus": _object(
        path=_TEXT,
        status=_enum("已定位", "当前输入未找到", "原文明确未报告或未测量", "不适用", "文本损坏"),
        reason=_NULL_TEXT,
        source_refs=_SOURCES,
    ),
    "Value": _object(
        quantity=_TEXT,
        raw_value=_TEXT,
        unit_raw=_NULL_TEXT,
        normalization_basis=_NULL_TEXT,
        applies_to=_TEXT,
        source_refs=_SOURCES,
    ),
    "Interpretation": {
        "anyOf": [
            _object(mode=_enum("ref"), ref=_ref("ObjectRef")),
            _object(mode=_enum("local"), text=_TEXT, text_locations=_LOCATIONS),
        ]
    },
    "SourceAccess": _object(
        forms=_array(_enum("正文文本", "结构化表格", "图注文本", "公式推导文本", "脚注文本"), 1, unique=True),
        reading_scope=_enum("当前论文的本研究报告", "当前论文对既有研究的转述", "另有提供的原始文献文本"),
    ),
    "ReferenceInfo": _object(
        citation_label=_NULL_TEXT,
        bibliographic_text=_NULL_TEXT,
        doi=_NULL_TEXT,
        other_identifiers={"type": "object", "additionalProperties": _TEXT},
        reading_scope=_enum("当前论文对既有研究的转述", "另有提供的原始文献文本"),
    ),
    "ScopeUnit": _object(
        scope_unit_id=_TEXT,
        target_refs=_array(_ref("MaterialRef"), 1),
        review_domain=_DOMAIN,
        consistency_subtype=_SUBTYPE,
        criterion_scope=_TEXT,
    ),
    "Basis": _object(ref=_ref("MaterialRef"), relevance=_TEXT, conditions_match=_TEXT, response_refs=_MATERIALS),
    "ReviewRole": _object(review_ref=_ref("ObjectRef"), role=_TEXT),
    "SourceIdentity": _object(
        creators=_array(_TEXT),
        title=_NULL_TEXT,
        year=_NULL_TEXT,
        doi=_NULL_TEXT,
        standard_id=_NULL_TEXT,
        url=_NULL_TEXT,
    ),
    "SourceLocator": _object(section=_NULL_TEXT, page_label=_NULL_TEXT, clause=_NULL_TEXT, text_excerpt=_NULL_TEXT),
    "PersonalRating": _object(
        raw_tags=_STRINGS,
        parsed_rating=_NULL_TEXT,
        scale=_NULL_TEXT,
        parsing_rule=_NULL_TEXT,
        rating_meaning=_NULL_TEXT,
        user_note=_NULL_TEXT,
        snapshot_ref=_NULL_TEXT,
        read_at=_NULL_TEXT,
        check_status=_BACKGROUND_STATUS,
    ),
    "JournalMetric": _object(
        metric_name=_TEXT,
        raw_value=_NULL_TEXT,
        metric_year=_nullable(_INT),
        subject_category=_NULL_TEXT,
        source_identity=_nullable(_ref("SourceIdentity")),
        source_locator=_nullable(_ref("SourceLocator")),
        retrieved_at=_NULL_TEXT,
        reading_scope=_NULL_TEXT,
        check_status=_BACKGROUND_STATUS,
        custom_score=_NULL_TEXT,
        scale=_NULL_TEXT,
        scoring_rule=_NULL_TEXT,
    ),
}


def _record(identity: str, **fields: Any) -> dict[str, Any]:
    schema = _object(**{identity: _TEXT}, record_meta=_ref("RecordMeta"), **fields)
    # 两项仅为展示建议；全局号由程序登记，不接受任意扩展字段。
    schema["properties"]["display"] = _object(label=_NULL_TEXT, order=_nullable({"type": "integer"}))
    schema["properties"]["index"] = _TEXT
    schema["properties"]["figure_refs"] = _array(_ref("FigureRef"), unique=True)
    return schema


_DEFS.update(
    {
        "TC-N": _record(
            "node_id",
            node_type=_NODE_TYPE,
            text=_TEXT,
            claim_kind=_enum("描述", "比较", "机制", "因果", "方法有效性", "推广性", None),
            argument_angle=_NULL_TEXT,
            original_qualifiers=_array(_TEXT),
            attribution=_ATTRIBUTION,
            extraction_status=_EXTRACTION,
            text_locations=_LOCATIONS,
            presentation_status=_enum("已组织", "未进一步展开", "层级待整理"),
            presentation_note=_NULL_TEXT,
        ),
        "TC-L": _record(
            "edge_id",
            from_node_id=_TEXT,
            to_node_id=_TEXT,
            bridge=_TEXT,
            target_part=_TEXT,
            combination=_object(mode=_enum("单独提供理由", "联合使用", "未明确"), edge_ids=_STRINGS),
            attribution=_ATTRIBUTION,
            extraction_status=_EXTRACTION,
            text_locations=_LOCATIONS,
        ),
        "TC-C": _record(
            "candidate_id",
            candidate_kind=_enum("命题候选", "联系候选"),
            candidate_text=_NULL_TEXT,
            proposed_node_type=_nullable(_NODE_TYPE),
            from_node_id=_NULL_TEXT,
            to_node_id=_NULL_TEXT,
            unresolved_description=_TEXT,
            attribution={"type": "null"},
            extraction_status=_enum("未明确"),
            text_locations=_LOCATIONS,
            resolution_refs=_REFS,
            decision=_enum("pending", "accepted", "rejected", "withdrawn"),
            decision_reason=_NULL_TEXT,
        ),
        "EC-E": _record(
            "evidence_id",
            research_origin=_enum("实验依据", "文献依据", "未明确"),
            primary_aspect=_nullable(_ASPECT),
            additional_aspects=_array(_ASPECT, unique=True),
            object_and_conditions=_object(description=_TEXT, conditions=_array(_ref("Condition"))),
            field_statuses=_array(_ref("FieldStatus")),
            method_or_acquisition=_object(description=_NULL_TEXT, parameters=_array(_ref("Condition"))),
            reported_result=_object(
                statement=_TEXT,
                values=_array(_ref("Value")),
                comparison=_NULL_TEXT,
                uncertainty=_NULL_TEXT,
                original_qualifiers=_array(_TEXT),
            ),
            result_type=_enum(
                "实验测量结果", "数据处理或统计结果", "模型拟合结果", "模拟计算结果", "理论推导结果", "未明确"
            ),
            author_interpretation=_nullable(_ref("Interpretation")),
            text_locations=_LOCATIONS,
            source_access=_ref("SourceAccess"),
            reference_info=_nullable(_ref("ReferenceInfo")),
            attribution=_ATTRIBUTION,
            extraction_status=_EXTRACTION,
        ),
        "EC-P": _record(
            "process_id",
            input_evidence_ids=_array(_TEXT, 1, unique=True),
            output_evidence_ids=_array(_TEXT, 1, unique=True),
            process_type=_enum("处理得到", "拟合得到", "计算得到", "汇总得到"),
            operation=_TEXT,
            parameters_or_model=_NULL_TEXT,
            attribution=_ATTRIBUTION,
            extraction_status=_EXTRACTION,
            text_locations=_LOCATIONS,
        ),
        "EC-G": _record(
            "group_id",
            member_evidence_ids=_array(_TEXT, 2, unique=True),
            shared_source_description=_TEXT,
            text_locations=_LOCATIONS,
        ),
        "EC-U": _record(
            "use_group_id",
            members=_array(_object(evidence_id=_TEXT, role=_TEXT), 2),
            group_type=_enum("联合论证", "比较使用", "解释汇集"),
            combination_description=_TEXT,
            comparison_statement=_NULL_TEXT,
            attribution=_ATTRIBUTION,
            extraction_status=_EXTRACTION,
            text_locations=_LOCATIONS,
        ),
        "EC-M": _record(
            "mapping_id",
            subject_ref=_ref("ObjectRef"),
            target_ref=_ref("ObjectRef"),
            target_part=_TEXT,
            usage_role=_enum("论证依据", "比较材料", "背景依据", "方法依据"),
            use_topic=_enum("机制论证", "其他", "未明确"),
            usage_description=_TEXT,
            attribution=_ATTRIBUTION,
            extraction_status=_EXTRACTION,
            text_locations=_LOCATIONS,
        ),
        "EC-C": _record(
            "candidate_id",
            subject_ref=_ref("ObjectRef"),
            candidate_targets=_REFS,
            unresolved_description=_TEXT,
            attribution={"type": "null"},
            extraction_status=_enum("未明确"),
            text_locations=_LOCATIONS,
            resolved_mapping_ids=_STRINGS,
            decision=_enum("pending", "accepted", "rejected", "withdrawn"),
            decision_reason=_NULL_TEXT,
        ),
        "RC-B": _record(
            "background_id",
            parent_item_ref=_nullable(
                _object(library_ref=_ref("LibraryRef"), parent_item_key=_TEXT, snapshot_ref=_NULL_TEXT)
            ),
            journal_identity=_nullable(_object(title=_NULL_TEXT, issn=_STRINGS)),
            journal_metrics=_array(_ref("JournalMetric")),
            personal_rating=_ref("PersonalRating"),
            publication_notes=_NULL_TEXT,
            acquisition_status=_enum("未取得", "部分取得", "已取得"),
            recorded_at=_NULL_TEXT,
            context_note=_NULL_TEXT,
        ),
        "RC-K": _record(
            "criterion_source_id",
            source_identity=_ref("SourceIdentity"),
            edition_or_version=_NULL_TEXT,
            source_revision=_INT,
            content_snapshot_ref=_NULL_TEXT,
            retrieved_at=_NULL_TEXT,
            access_scope=_enum("仅书目信息", "仅摘要", "已读取相关内容"),
            check_status=_enum("待核对", "已核对", "存在冲突"),
            locations=_array(
                _object(
                    location_id=_TEXT,
                    section=_NULL_TEXT,
                    page_label=_NULL_TEXT,
                    clause=_NULL_TEXT,
                    text_excerpt=_NULL_TEXT,
                )
            ),
            purpose_note=_TEXT,
        ),
        "RC-Q": _record(
            "review_id",
            primary_target_ref=_nullable(_ref("MaterialRef")),
            related_target_refs=_MATERIALS,
            related_review_refs=_REFS,
            review_domain=_DOMAIN,
            consistency_subtype=_SUBTYPE,
            question=_TEXT,
            issue_origin=_enum("作者自述", "审查者提出"),
            problem_locus=_enum("原文报告", "解析文本", "结构化整理", "跨模块关联", "当前无法区分", None),
            observed_basis=_TEXT,
            criterion=_object(
                statement=_TEXT,
                criterion_type=_enum("一般逻辑", "方法学原则", "特定领域规则"),
                criterion_source_refs=_array(_ref("CriterionRef")),
            ),
            basis_refs=_array(
                _object(
                    ref=_ref("MaterialRef"), role=_enum("支持疑点", "缓解疑点", "限定范围", "提供判准"), relevance=_TEXT
                )
            ),
            reasoning=_TEXT,
            finding_status=_FINDING,
            impact=_enum("关键", "重要", "局部", "待定", None),
            impact_reason=_TEXT,
            direct_effect=_TEXT,
            response_refs=_MATERIALS,
            resolution_status=_enum("开放", "已有针对性处理", "需复核", None),
            resolution_requirements=_NULL_TEXT,
            review_scope=_TEXT,
            suggestion_refs=_REFS,
            text_locations=_array(_ref("Location")),
        ),
        "RC-S": _record(
            "coverage_id",
            scope_unit_id=_TEXT,
            target_refs=_array(_ref("MaterialRef"), 1),
            review_domain=_DOMAIN,
            consistency_subtype=_SUBTYPE,
            criterion_scope=_TEXT,
            check_status=_enum("未检查", "已检查", "当前不可检查", "不适用"),
            inspected_inputs=_MATERIALS,
            review_refs=_REFS,
            assessment_refs=_REFS,
            unavailable_reason=_NULL_TEXT,
            not_applicable_reason=_NULL_TEXT,
        ),
        "RC-A": _record(
            "assessment_id",
            target_ref=_ref("ObjectRef"),
            assessed_scope=_TEXT,
            background_refs=_REFS,
            evidence_refs=_REFS,
            mapping_refs=_REFS,
            evidence_review=_array(_ref("ReviewRole")),
            logic_review=_array(_ref("ReviewRole")),
            provenance_handling=_TEXT,
            supporting_basis=_array(_ref("Basis")),
            counter_basis=_array(_ref("Basis")),
            unresolved_reviews=_array(_object(review_ref=_ref("ObjectRef"), remaining_effect=_TEXT)),
            synthesis=_object(
                load_bearing_dependencies=_TEXT,
                material_balance=_TEXT,
                issue_handling=_TEXT,
                remaining_gaps=_TEXT,
                conclusion_reason=_TEXT,
            ),
            reliability_statement=_object(
                conclusion=_enum(
                    "当前材料内支持较充分",
                    "有支持但须限定范围",
                    "关键支持仍待核实",
                    "存在实质性反对或不一致，尚未协调",
                    "当前无法评议",
                ),
                scope=_TEXT,
                rationale=_TEXT,
            ),
            retained_claim=_nullable(_object(origin=_enum("审查者建议"), suggestion_ref=_ref("ObjectRef"))),
            review_scope=_TEXT,
            unassessed_parts=_array(_object(part=_TEXT, reason=_TEXT)),
            coverage_refs=_REFS,
        ),
        "RC-D": _record(
            "suggestion_id",
            origin=_enum("审查者建议"),
            review_refs=_REFS,
            assessment_refs=_REFS,
            target_ref=_ref("ObjectRef"),
            proposed_object_kind=_nullable(_enum("TC-N", "TC-L", "EC-E", "EC-P", "EC-G", "EC-U", "EC-M")),
            change_type=_enum(
                "调整命题表述", "调整层级或连接", "更正证据字段", "补充证据登记", "调整用途关联", "补充来源或说明"
            ),
            proposed_content=_TEXT,
            rationale=_TEXT,
            status=_enum("待处理", "已采纳待落实", "已拒绝", "暂缓", "已落实待复核", "复核完成"),
            result_refs=_REFS,
            verification_refs=_REFS,
        ),
    }
)

_DOCUMENT = _object(
    schema_version=_enum(SCHEMA_VERSION),
    library_ref=_ref("LibraryRef"),
    parent_item_key=_TEXT,
    run_number=_INT,
    inputs=_object(
        source_note=_object(note_key=_TEXT, text_digest=_TEXT),
        documents=_array(
            _object(
                source_set_id=_TEXT,
                attachment_key=_TEXT,
                attachment_aliases=_STRINGS,
                pdf_sha256=_TEXT,
                pdf_role=_ROLE,
                document_identity_status=_enum("已匹配父条目", "身份未明确", "不属于目标论文"),
                role_basis=_TEXT,
                parse_metadata=_object(
                    parser=_TEXT,
                    parser_version=_NULL_TEXT,
                    adapter_version=_TEXT,
                    parsed_at=_NULL_TEXT,
                    parse_artifact_digest=_TEXT,
                ),
            ),
            1,
        ),
    ),
    source_blocks=_array(_ref("SourceBlock"), 1),
    thought=_object(
        nodes=_array(_ref("TC-N")), support_edges=_array(_ref("TC-L")), candidate_records=_array(_ref("TC-C"))
    ),
    evidence=_object(
        **{table: _array(_ref(prefix)) for chain, table, _, prefix in RECORD_TABLES if chain == "evidence"}
    ),
    review=_object(
        **{table: _array(_ref(prefix)) for chain, table, _, prefix in RECORD_TABLES if chain == "review"},
        judgment_author=_enum("AI", "人工", "共同评阅"),
        criterion_source_set_id=_NULL_TEXT,
        coverage_scope=_object(
            units=_array(_ref("ScopeUnit")), exclusions=_array(_TEXT), declaration=_enum("部分覆盖", "已完成声明范围")
        ),
    ),
    object_registry=_array(
        _object(object_id=_TEXT, object_type=_enum(*(row[3] for row in RECORD_TABLES)), index=_TEXT)
    ),
)


def note2_json_schema() -> dict[str, Any]:
    """返回独立副本，调用者不能更改后续校验使用的契约。"""
    return deepcopy(
        {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": SCHEMA_VERSION, **_DOCUMENT, "$defs": _DEFS}
    )


@dataclass(frozen=True, slots=True)
class Note2Document:
    """已校验文档的值载体；通过校验入口构造，读写边界均复制数据。"""

    _data: dict[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_data", deepcopy(self._data))

    def to_dict(self) -> dict[str, Any]:
        """导出可序列化副本。"""
        return deepcopy(self._data)

    @property
    def schema_version(self) -> str:
        return self._data["schema_version"]

    @property
    def parent_item_key(self) -> str:
        return self._data["parent_item_key"]

    @property
    def run_number(self) -> int:
        return self._data["run_number"]
