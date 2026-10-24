"""AI note 分析流水线各模块共用的常量。

数值策略不在本模块写死：名称在访问时才从统一运行策略
（``config/defaults.toml`` 加用户 ``config.toml``）解析，因此 TOML 覆盖会生效。
"""

from __future__ import annotations

from typing import Any

from zotero_cli.config.runtime import section

# 常量名 -> 生效配置键
_POLICY_KEYS = {
    "ANALYZED_TAG": "analyzed_tag",
    "NOT_ANALYZED_TAG": "not_analyzed_tag",
    "KEYWORDS_TAG": "keywords_tag",
    "NO_KEYWORDS_TAG": "no_keywords_tag",
    "NOTE_TITLE_PREFIX": "note_title_prefix",
    "MIN_SECTIONS": "min_sections",
    "MAX_PROMPT_CHARS": "max_prompt_chars",
    "PROMPT_RESERVE_CHARS": "prompt_reserve_chars",
    "MAX_TITLE_CHARS": "max_title_chars",
    "MAX_ABSTRACT_CHARS": "max_abstract_chars",
    "MAX_AUTHORS_CHARS": "max_authors_chars",
    "MAX_JOURNAL_CHARS": "max_journal_chars",
    "MAX_DATE_CHARS": "max_date_chars",
    "MAX_DOI_CHARS": "max_doi_chars",
    "CLASSIFY_CHARS": "classification_chars",
    "MAX_PACKET_CHARS": "max_packet_chars",
    "MAX_REFERENCES_CHARS": "max_references_chars",
    "SHORT_NOTE_MAX_CHARS": "short_note_max_chars",
    "SHORT_NOTE_MIN_SEGMENTS": "short_note_min_segments",
}


def __getattr__(name: str) -> Any:
    key = _POLICY_KEYS.get(name)
    if key is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return section("ai_notes")[key]


def __dir__() -> list[str]:
    return sorted([*globals(), *_POLICY_KEYS])


# 模型输出不可用时追加到 prompt 末尾的重试指令（协议文本，不是运行策略）。
PARSE_RETRY_SUFFIX = (
    "\n\n[输出格式要求（必须遵守）]"
    "上一次输出无法解析为 JSON。请重新输出，且只输出一个 json 代码块："
    '不要输出任何解释文字，顶层必须是 {"sections": [...]}，'
    "确保 JSON 完整闭合（花括号、引号全部配对），字符串内换行用 \\n 转义。"
)

SPARSE_RETRY_SUFFIX = (
    "\n\n[内容充实度要求（必须遵守）]"
    "上一次输出过于简略。请重新生成完整分析：每个章节内容充实具体，"
    "关键结论带证据锚点（页码/图号/表号/文献编号）与具体数值；"
    "笔记原子化每类至少 3 条并带 citations；优缺点至少 3 条优点、2 条缺点；"
    "研究启发至少 3 条；论证逻辑链路图用固定表格且至少 5 行。"
    "仍只输出一个 json 代码块。"
)
