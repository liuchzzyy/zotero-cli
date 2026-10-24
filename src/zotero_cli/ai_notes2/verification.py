"""第二步：核对生成笔记中的每条内容都能在冻结 PDF 文本中准确定位。

核对分两层：

1. 程序层：每个正式对象的每条来源定位必须指向本次冻结块，且 `text_excerpt`
   与冻结块（含 span）逐字一致；对象引用的图号必须在该附件图注中出现。
2. 模型层：把对象清单与冻结文本块一起发给模型逐条复核，判断陈述是否被原文
   直接支持。模型只能返回判定，不能改写对象；判定结果附在运行清单与笔记中，
   不删除、不重写三链内容。

任何程序层不一致都直接失败：那说明文档已经不再忠实于本次输入。模型层的
`不支持` 或 `无法核对` 不阻止发布，但会写入可见记录并标为待人工复核。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.evidence import model_supports_request
from zotero_cli.ai_notes.generation import wrap_untrusted
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.inputs import InputSnapshot
from zotero_cli.ai_notes2.models import RECORD_TABLES
from zotero_cli.ai_notes2.templates import format_template, load_template
from zotero_cli.ai_notes2.validation import Note2Document, Note2ValidationError, validate_document

VERIFICATION_STATUSES = ("支持", "部分支持", "不支持", "无法核对")


@dataclass
class VerificationReport:
    """两步核对的结论；`errors` 非空表示不得发布。"""

    checked_objects: int
    verdicts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    model_used: bool = False

    @property
    def unsupported(self) -> list[dict[str, Any]]:
        return [item for item in self.verdicts if item["status"] != "支持"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_objects": self.checked_objects,
            "model_used": self.model_used,
            "errors": list(self.errors),
            "counts": {
                status: sum(1 for item in self.verdicts if item["status"] == status) for status in VERIFICATION_STATUSES
            },
            "verdicts": list(self.verdicts),
        }


def _iter_records(document: Note2Document) -> list[tuple[str, str, dict[str, Any]]]:
    """按契约表顺序返回 `(前缀, 表名, 记录)`。"""
    data = document.to_dict()
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for chain, table, _identity, prefix in RECORD_TABLES:
        for record in data[chain][table]:
            rows.append((prefix, table, record))
    return rows


def _locations(record: dict[str, Any]) -> list[dict[str, Any]]:
    """收集记录内任意层级的来源定位。"""
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "block_id" in value and "text_excerpt" in value and "source_set_id" in value:
                found.append(value)
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(record)
    return found


def _has_object_ref(record: dict[str, Any]) -> bool:
    """判断记录是否通过引用其他对象间接获得出处。"""
    found = False

    def walk(value: Any) -> None:
        nonlocal found
        if found:
            return
        if isinstance(value, dict):
            if value.get("kind") == "object":
                found = True
                return
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(record)
    return found


def check_sources(document: Note2Document, snapshot: InputSnapshot) -> list[str]:
    """程序层核对：来源块、原文片段与图号必须与冻结输入一致。

    评议、覆盖与候选等派生记录可以没有自己的原文定位，但必须引用其他已定位
    对象；否则整条内容无法回溯到本次冻结文本。
    """
    errors: list[str] = []
    frozen = {
        (block["source_set_id"], block["attachment_key"], block["block_id"]): block for block in snapshot.source_blocks
    }
    figures: dict[tuple[str, str], set[str]] = {}
    for block in snapshot.source_blocks:
        figures.setdefault((block["source_set_id"], block["attachment_key"]), set()).update(block["figure_ids"])

    for prefix, _table, record in _iter_records(document):
        identity = next(key for key in record if key.endswith("_id") and key != "target_id")
        label = f"{prefix}:{record[identity]}"
        locations = _locations(record)
        if not locations and not _has_object_ref(record):
            errors.append(f"{label} 既没有来源定位也没有引用其他对象")
            continue
        for location in locations:
            key = (location["source_set_id"], location["attachment_key"], location["block_id"])
            block = frozen.get(key)
            if block is None:
                errors.append(f"{label} 引用了未冻结的来源块 {key}")
                continue
            text = block["text"]
            span = location.get("span")
            if span is not None:
                start, end = span
                if not 0 <= start < end <= len(text):
                    errors.append(f"{label} 的来源区间越界 {span}")
                    continue
                text = text[start:end]
            if location["text_excerpt"] != text:
                errors.append(f"{label} 的原文片段与冻结块不一致")
        for figure in record.get("figure_refs", []):
            allowed = figures.get((figure["source_set_id"], figure["attachment_key"]), set())
            if figure["figure_id"] not in allowed:
                errors.append(f"{label} 引用了未在冻结图注中出现的图 {figure['figure_id']}")
    return errors


def _object_summary(document: Note2Document) -> str:
    """给核对模型的对象清单：只含 ID、类型与关键陈述，不含原文片段。"""
    lines: list[str] = []
    for prefix, _table, record in _iter_records(document):
        identity = next(key for key in record if key.endswith("_id") and key != "target_id")
        block_ids = sorted({loc["block_id"] for loc in _locations(record)})
        summary = {
            "object_id": record[identity],
            "object_type": prefix,
            "index": record.get("index", ""),
            "source_block_ids": block_ids,
        }
        for candidate in ("text", "bridge", "question", "statement", "description", "reported_result"):
            value = record.get(candidate)
            if isinstance(value, str) and value.strip():
                summary[candidate] = value
            elif isinstance(value, dict) and isinstance(value.get("statement"), str):
                summary[candidate] = value["statement"]
        summary["record"] = record
        lines.append(json.dumps(summary, ensure_ascii=False))
    return "\n".join(lines)


def _parse_verdicts(raw: str, allowed_ids: set[str]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw.strip())
    except (json.JSONDecodeError, RecursionError) as error:
        raise Note2ValidationError("/", f"核对结果不是有效 JSON：{error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
        raise Note2ValidationError("/verdicts", "核对结果缺少 verdicts 数组")
    verdicts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in payload["verdicts"]:
        if not isinstance(entry, dict):
            raise Note2ValidationError("/verdicts", "核对条目必须是对象")
        identifier = entry.get("object_id")
        status = entry.get("status")
        if not isinstance(identifier, str) or identifier not in allowed_ids:
            raise Note2ValidationError("/verdicts", f"核对结果包含未知对象 {identifier}")
        if identifier in seen:
            raise Note2ValidationError("/verdicts", f"核对结果重复对象 {identifier}")
        seen.add(identifier)
        if status not in VERIFICATION_STATUSES:
            raise Note2ValidationError("/verdicts", f"未知核对状态 {status}")
        source_ids = entry.get("source_block_ids")
        if not isinstance(source_ids, list) or any(not isinstance(item, str) for item in source_ids):
            raise Note2ValidationError("/verdicts", "source_block_ids 必须是字符串数组")
        if not isinstance(entry.get("note"), str):
            raise Note2ValidationError("/verdicts", "note 必须是字符串")
        verdicts.append(
            {
                "object_id": identifier,
                "status": status,
                "source_block_ids": source_ids,
                "note": entry["note"],
            }
        )
    if seen != allowed_ids:
        raise Note2ValidationError("/verdicts", f"核对结果遗漏 {len(allowed_ids - seen)} 个对象")
    return verdicts


def verify_document(
    document: Note2Document,
    snapshot: InputSnapshot,
    *,
    client: AiClient | None = None,
    progress: Any = None,
) -> VerificationReport:
    """执行两步核对；程序层失败抛错，模型层结论随结果返回。"""
    errors = check_sources(document, snapshot)
    report = VerificationReport(checked_objects=len(_iter_records(document)), errors=errors)
    if errors:
        raise AiNote2Error(
            "生成内容无法在冻结 PDF 文本中准确定位：" + "；".join(errors[:5]),
            code="validation_error",
            hint_key="source_verification_failed",
        )
    if client is None:
        return report

    prompt = format_template(
        load_template("verification"),
        objects=wrap_untrusted(_object_summary(document), "note-objects"),
        blocks=wrap_untrusted(snapshot.projection, "document"),
    )
    allowed = {
        candidate
        for candidate in client.get_model_chain()
        if model_supports_request(candidate, prompt, 0, max_tokens=client.config.max_tokens)
    }
    if not allowed:
        raise AiNote2Error(
            "核对请求超出模型预算；不静默截断输入",
            code="validation_error",
            hint_key="input_too_large",
            parkable=True,
        )
    if progress:
        progress("verify", f"第二步：核对 {report.checked_objects} 个对象的原文依据")
    raw = client.chat(prompt, figures=[], allowed_models=allowed, progress=progress)
    allowed_ids = {
        record[next(key for key in record if key.endswith("_id") and key != "target_id")]
        for _prefix, _table, record in _iter_records(document)
    }
    report.verdicts = _parse_verdicts(raw, allowed_ids)
    report.model_used = True
    return report


def attach_report(document: dict[str, Any], report: VerificationReport) -> dict[str, Any]:
    """把核对结论附到运行清单；不改动三链对象本身。"""
    validated = validate_document(document)
    return {
        "document": validated.to_dict(),
        "verification": report.to_dict(),
    }
