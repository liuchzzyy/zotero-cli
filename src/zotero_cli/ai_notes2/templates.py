"""解析并填充 ai-note2 的 prompt 模板。

模板与包一起分发，位于 `src/zotero_cli/ai_notes2/templates/`。与旧
`ai_notes/templates.py` 的差别仅是占位符集合不同：三链笔记需要
`source_note` 与 `blocks` 两类冻结输入，并附带紧凑的顶层字段提示。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from zotero_cli.ai_notes.generation import wrap_untrusted
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.models import RECORD_TABLES, SCHEMA_VERSION, note2_json_schema

_MISSING = "未知"

# 名称 -> 模板文件名；保留别名，便于以后按论文类型扩展。
_TEMPLATE_ALIASES: dict[str, str] = {
    "evidence-chain": "evidence-chain.md",
    "evidence_chain": "evidence-chain.md",
    "default": "evidence-chain.md",
}

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def templates_dir() -> Path:
    """模板随包分发，不读取用户可写目录。"""
    return Path(__file__).resolve().parent / "templates"


def template_path(name: str = "evidence-chain") -> Path:
    """解析模板路径；名称可省略后缀或使用别名。"""
    filename = _TEMPLATE_ALIASES.get(name, name)
    if not filename.endswith(".md"):
        filename += ".md"
    candidate = templates_dir() / filename
    if not candidate.exists():
        raise AiNote2Error(
            f"ai-note2 模板 '{name}' 不存在：{candidate}", code="configuration_error", hint_key="template_missing"
        )
    return candidate


def load_template(name: str = "evidence-chain") -> str:
    """读取模板原文；不做任何占位符替换。"""
    return template_path(name).read_text(encoding="utf-8")


def available_templates() -> list[str]:
    """列出可用的模板基名，供诊断与 schema 展示。"""
    return sorted(path.stem for path in templates_dir().glob("*.md"))


def schema_fields() -> str:
    """发送模型需生成的三链字段和类型提示；来源由程序填充。"""
    schema = note2_json_schema()
    defs = schema["$defs"]
    records = {
        prefix: {"required": defs[prefix]["required"], "properties": defs[prefix]["properties"]}
        for _chain, _table, _identity, prefix in RECORD_TABLES
    }
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "thought": schema["properties"]["thought"],
            "evidence": schema["properties"]["evidence"],
            "review": schema["properties"]["review"],
            "records": records,
            "shared_types": {name: definition for name, definition in defs.items() if name not in records},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def format_template(template: str, **values: Any) -> str:
    """一次性替换模板占位符，缺失值写 `未知`。

    只替换识别到的占位符，避免论文正文里出现的花括号被 `str.format` 解释。
    """
    resolved = {key: (str(value).strip() or _MISSING) for key, value in values.items()}
    return _PLACEHOLDER_RE.sub(lambda match: resolved.get(match.group(1), _MISSING), template)


def build_prompt(
    *,
    item_key: str,
    title: str,
    authors: str,
    journal: str,
    date: str,
    doi: str,
    source_note_key: str,
    source_note: str,
    blocks: str,
    template_name: str = "evidence-chain",
) -> str:
    """构造唯一一次分析请求的提示词，所有论文内容按不可信数据包裹。"""
    template = load_template(template_name)
    return format_template(
        template,
        item_key=item_key,
        title=wrap_untrusted(title, "title"),
        authors=wrap_untrusted(authors, "authors"),
        journal=wrap_untrusted(journal, "journal"),
        date=wrap_untrusted(date, "date"),
        doi=wrap_untrusted(doi, "doi"),
        source_note_key=source_note_key,
        schema_fields=schema_fields(),
        source_note=wrap_untrusted(source_note, "existing-ai-note"),
        blocks=wrap_untrusted(blocks, "document"),
    )
