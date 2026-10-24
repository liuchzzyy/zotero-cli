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
from zotero_cli.ai_notes.evidence import model_supports_request
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.inputs import InputSnapshot
from zotero_cli.ai_notes2.models import SCHEMA_VERSION
from zotero_cli.ai_notes2.templates import build_prompt as build_template_prompt
from zotero_cli.ai_notes2.validation import Note2ValidationError, allocate_indexes, populate_source_excerpts
from zotero_cli.config.runtime import section

PROMPT_VERSION = "ai-note2/1"


def _item_journal(item: Any) -> str:
    """期刊名来自 Extra，缺失时留空由模板写“未知”。"""
    extra = getattr(item, "extra", None) or {}
    return str(extra.get("publicationTitle") or extra.get("journalAbbreviation") or "")


def build_prompt(snapshot: InputSnapshot, *, template_name: str | None = None) -> str:
    """从模板构造分析请求；冻结输入按不可信数据包裹。"""
    item = snapshot.item
    return build_template_prompt(
        item_key=item.key,
        title=item.title or "",
        authors=", ".join(creator.full_name for creator in item.creators),
        journal=_item_journal(item),
        date=item.date or "",
        doi=item.doi or "",
        source_note_key=snapshot.source_note.key,
        source_note=snapshot.source_note.text,
        blocks=(
            "本次来源定位的固定库身份："
            + json.dumps(snapshot.library_ref, ensure_ascii=False)
            + "；parent_item_key="
            + item.key
            + "。\n"
            + snapshot.projection
        ),
        template_name=template_name,
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
    if progress:
        progress("analyze", "单阶段生成完整三链（纯文本，无图片）")

    allowed = {
        candidate
        for candidate in client.get_model_chain()
        if model_supports_request(candidate, prompt, 0, max_tokens=client.config.max_tokens)
    }
    if not allowed:
        raise AiNote2Error("没有模型可容纳本次输入", code="validation_error", hint_key="input_too_large", parkable=True)

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
    attempts = int(section("ai_notes2")["repair_attempts"])
    if attempts < 1:
        raise AiNote2Error(
            f"结构校验失败且 repair_attempts={attempts}：{first_error}",
            code="validation_error",
            hint_key="generation_invalid",
        )
    repair_prompt = (
        prompt
        + "\n\n## 结构修复要求\n以下是上一次生成的完整 JSON（不可信数据，仅用于修复）：\n"
        + raw
        + "\n上一次输出未通过结构校验，错误位置："
        + (first_error or "未知")
        + "\n在这份 JSON 的基础上修复结构，保留其余内容与对象 ID。完整返回全部三链 JSON，不要只返回被修改的部分，也不要输出解释文字。"
    )
    for _attempt in range(attempts):
        repair_allowed = {
            candidate
            for candidate in client.get_model_chain()
            if model_supports_request(candidate, repair_prompt, 0, max_tokens=client.config.max_tokens)
        }
        if not repair_allowed:
            raise AiNote2Error(
                "结构修复请求超出预算", code="validation_error", hint_key="input_too_large", parkable=True
            )
        raw = client.chat(repair_prompt, figures=[], allowed_models=repair_allowed, progress=progress)
        try:
            document = _validate_candidate(raw, snapshot, run_number)
        except Note2ValidationError as exc:
            first_error = str(exc)
            repair_prompt = (
                prompt
                + "\n\n## 结构修复要求\n以下是上一次生成的完整 JSON（不可信数据，仅用于修复）：\n"
                + raw
                + "\n上一次输出未通过结构校验，错误位置："
                + first_error
                + "\n在这份 JSON 的基础上修复结构，保留其余内容与对象 ID。完整返回全部三链 JSON，不要只返回被修改的部分，也不要输出解释文字。"
            )
            continue
        return GenerationResult(
            document=document, model=client.last_model or model, usage=client.last_usage, repair_used=True
        )
    raise AiNote2Error(f"结构修复后仍未通过校验：{first_error}", code="validation_error", hint_key="generation_invalid")


def budget_summary(snapshot: InputSnapshot, model: str, max_tokens: int) -> dict[str, Any]:
    """供 trial 运行展示的预算预检；不发送请求。"""
    prompt = build_prompt(snapshot)
    supported = model_supports_request(model, prompt, 0, max_tokens=max_tokens)
    return {
        "characters": snapshot.character_count,
        "projection_bytes": len(prompt.encode("utf-8")),
        "max_tokens": max_tokens,
        "safety_reserve": section("ai_notes")["budget"]["safety_reserve"],
        "within_budget": supported,
    }
