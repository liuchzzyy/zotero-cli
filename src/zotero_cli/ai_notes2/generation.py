"""单阶段纯文本生成完整三链，并允许一次结构修复。

成功路径只发送一次分析请求。仅在 JSON 或结构校验失败时追加一轮结构修复
请求，仍使用同一冻结输入；不会增加独立分析阶段，也不会用字面数值比对裁决
科学内容。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.evidence import OUTPUT_RESERVE, SAFETY_RESERVE, model_supports_request
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.inputs import InputSnapshot
from zotero_cli.ai_notes2.models import SCHEMA_VERSION, note2_json_schema
from zotero_cli.ai_notes2.validation import Note2ValidationError, allocate_indexes, populate_source_excerpts

SYSTEM_PROMPT = (
    "你是科研论文的论证整理助手。你只整理作者公开的论证，不评审其正确性。"
    "你必须只输出一个 JSON 对象，不得输出解释、围栏或 Markdown。"
)

PROMPT_VERSION = "ai-note2/1"

_TASK = """请阅读给定的论文文本块与已有 AI 分析笔记，一次性生成完整的三链结构。

写作与结构要求：
1. 使用简体中文，遵循 ASD-STE100 的清晰写作原则：一个概念一个术语，句子说明对象、条件与判断。
2. 思维链（thought）只整理作者公开论证：核心论点 → 分论点 → 论证理由，最多三级。
   相邻层级的支持关系写入 support_edges，推理桥写入 bridge，不新增第四级。
3. 证据链（evidence）记录论文实际报告的结果、处理关系、同源组、联合使用与用途关联。
   不要为整齐而把完全未关联的结果强挂到某个论点。
4. 审查链（review）必须写明主要对象、单一领域、具体判准、依据、范围和结论。
   区分原文问题、解析问题、整理问题和关联问题；保留正向材料、相反材料与未解项。
5. 覆盖记录先列具体单位，再标未检查、已检查、当前不可检查或不适用。
   “已检查”必须引用在该范围完成判断的 RC-Q。
6. 原文由冻结来源块提供。每条记录都要引用实际 block_id；可用可选 span 指定区间，
   程序会从冻结块填入 text_excerpt，你不要自己编造原文片段。
7. 缺失字段必须显式记录为 null 并写入 field_statuses；不得补数值、补条件或补推理桥。
8. 临时 ID 自拟且在本文件内唯一（如 n1、e1、q1）；不要生成全局 index，也不要写 object_registry。
9. 候选对象不进入正式支持图；候选的 decision 与 record_meta.state 必须一致。
10. 不得声称读取图片像素；只能使用文本、表格文字、公式文字、图注与脚注。

输出 JSON 的顶层字段：
{schema}
"""


def _schema_hint() -> str:
    """给出紧凑的顶层字段提示；完整契约由校验器负责。"""
    schema = note2_json_schema()
    keys = [key for key in schema["properties"] if key not in {"object_registry"}]
    return json.dumps({"schema_version": SCHEMA_VERSION, "fields": keys}, ensure_ascii=False)


def build_prompt(snapshot: InputSnapshot) -> str:
    """构造唯一一次分析请求的提示词，包含冻结输入与旧笔记。"""
    item = snapshot.item
    metadata = {
        "key": item.key,
        "title": item.title,
        "item_type": item.item_type,
        "date": item.date,
        "doi": item.doi,
        "creators": [creator.full_name for creator in item.creators],
    }
    return "\n\n".join(
        [
            _TASK.format(schema=_schema_hint()),
            "## 父条目元信息\n" + json.dumps(metadata, ensure_ascii=False, indent=2),
            "## 已有 AI 分析笔记（辅助线索，不能代替 PDF 原文）\n" + snapshot.source_note.text,
            "## 论文文本块（无图片）\n" + snapshot.projection,
        ]
    )


@dataclass(frozen=True)
class GenerationResult:
    """一次生成的规范结果与调用事实。"""

    document: dict[str, Any]
    model: str
    usage: dict[str, Any] | None
    repair_used: bool


def _load_strict(raw: str) -> dict[str, Any]:
    """严格读取模型 JSON：拒绝围栏、重复键和非有限数字。"""
    text = raw.strip()
    if text.startswith("```"):
        raise Note2ValidationError("/", "输出不得使用代码围栏")

    def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in entries:
            if key in result:
                raise Note2ValidationError("/", f"JSON 字段重复：{key}")
            result[key] = value
        return result

    try:
        data = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(Note2ValidationError("/", f"JSON 非法常量：{value}")),
        )
    except (json.JSONDecodeError, RecursionError) as error:
        raise Note2ValidationError("/", f"JSON 无效：{error}") from error
    if not isinstance(data, dict):
        raise Note2ValidationError("/", "顶层必须是 JSON 对象")
    return data


def _validate_candidate(raw: str, snapshot: InputSnapshot, run_number: int) -> dict[str, Any]:
    document = _load_strict(raw)
    if document.get("parent_item_key") != snapshot.item.key:
        raise Note2ValidationError("/parent_item_key", "父条目必须与输入一致")
    # 来源清单、库身份、父条目和运行序号只由程序冻结，模型不得改写。
    document.update(
        schema_version=SCHEMA_VERSION,
        library_ref=dict(snapshot.library_ref),
        parent_item_key=snapshot.item.key,
        run_number=run_number,
        inputs={
            "source_note": {"note_key": snapshot.source_note.key, "text_digest": snapshot.source_note.digest},
            "documents": snapshot.documents,
        },
        source_blocks=snapshot.source_blocks,
        object_registry=[],
    )
    for chain in ("thought", "evidence", "review"):
        values = document.get(chain)
        if not isinstance(values, dict):
            raise Note2ValidationError(f"/{chain}", "三链必须是对象")
        for value in values.values():
            if isinstance(value, list):
                for record in value:
                    if isinstance(record, dict):
                        record.pop("index", None)
    document = populate_source_excerpts(document)
    return allocate_indexes(document, start=1).to_dict()


def generate_document(
    client: AiClient,
    snapshot: InputSnapshot,
    *,
    run_number: int,
    progress: Any = None,
) -> GenerationResult:
    """发送一次完整请求；结构失败时最多追加一轮结构修复。"""
    prompt = build_prompt(snapshot)
    model = client.config.model
    output_reserve = max(OUTPUT_RESERVE, client.config.max_tokens)
    if not model_supports_request(model, prompt, 0, output_reserve=output_reserve):
        raise AiNote2Error(
            "输入超出模型上下文或请求预算；不静默截断正文或旧笔记",
            code="validation_error",
            hint_key="input_too_large",
            parkable=True,
        )
    if progress:
        progress("analyze", "单阶段生成完整三链（纯文本，无图片）")

    allowed = {
        candidate
        for candidate in client.get_model_chain()
        if model_supports_request(candidate, prompt, 0, output_reserve=output_reserve)
    }
    if not allowed:
        raise AiNote2Error("没有模型可容纳本次输入", code="validation_error", hint_key="input_too_large")

    raw = client.chat(prompt, figures=[], allowed_models=allowed, progress=progress)
    first_error: str | None = None
    try:
        document = _validate_candidate(raw, snapshot, run_number)
        return GenerationResult(
            document=document, model=client.last_model or model, usage=client.last_usage, repair_used=False
        )
    except Note2ValidationError as exc:
        first_error = str(exc)

    if progress:
        progress("repair", f"结构校验失败，执行一次结构修复：{first_error[:120]}")
    repair_prompt = (
        prompt
        + "\n\n## 结构修复要求\n上一次输出未通过结构校验，错误位置："
        + (first_error or "未知")
        + "\n请完整返回全部三链 JSON，不要只返回被修改的部分，也不要输出解释文字。"
    )
    if not model_supports_request(model, repair_prompt, 0, output_reserve=output_reserve):
        raise AiNote2Error("结构修复请求超出预算", code="validation_error", hint_key="input_too_large", parkable=True)
    raw = client.chat(repair_prompt, figures=[], allowed_models=allowed, progress=progress)
    try:
        document = _validate_candidate(raw, snapshot, run_number)
    except Note2ValidationError as exc:
        raise AiNote2Error(
            f"结构修复后仍未通过校验：{exc}", code="validation_error", hint_key="generation_invalid"
        ) from exc
    return GenerationResult(
        document=document, model=client.last_model or model, usage=client.last_usage, repair_used=True
    )


def budget_summary(snapshot: InputSnapshot, model: str, max_tokens: int) -> dict[str, Any]:
    """供 trial 运行展示的预算预检；不发送请求。"""
    prompt = build_prompt(snapshot)
    supported = model_supports_request(model, prompt, 0, output_reserve=max(OUTPUT_RESERVE, max_tokens))
    return {
        "characters": snapshot.character_count,
        "projection_bytes": len(prompt.encode("utf-8")),
        "output_reserve": max(OUTPUT_RESERVE, max_tokens),
        "safety_reserve": SAFETY_RESERVE,
        "within_budget": supported,
    }
