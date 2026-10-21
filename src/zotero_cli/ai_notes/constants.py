"""Constants shared by the AI note analysis pipeline modules."""

from __future__ import annotations

ANALYZED_TAG = "ai/noted"
NOT_ANALYZED_TAG = "ai/uncertain"
KEYWORDS_TAG = "ai/keywords"
NO_KEYWORDS_TAG = "ai/no_keywords"
NOTE_TITLE_PREFIX = "AI条目分析 - "

MIN_SECTIONS = 10

# Hard ceiling for the *short-note* prompt (template + the AI note it condenses).
# The analysis prompt is bounded instead by `[ai_notes].max_extracted_chars`
# (default 250,000, 0 = no limit) and by the 500k-token image budget in
# `ai_notes/structured_input.py`; the two limits are independent.
MAX_PROMPT_CHARS = 220_000
PROMPT_RESERVE_CHARS = 2_000

# Per-field caps keep specially crafted / pathological Zotero metadata from
# consuming the prompt budget before the (truncatable) full text is considered.
MAX_TITLE_CHARS = 2_000
MAX_ABSTRACT_CHARS = 20_000
MAX_AUTHORS_CHARS = 10_000
MAX_JOURNAL_CHARS = 1_000
MAX_DATE_CHARS = 100
MAX_DOI_CHARS = 500

# Retry instructions appended to a prompt when the model output is unusable.
PARSE_RETRY_SUFFIX = (
    "\n\n【输出格式要求（必须遵守）】"
    "上一次输出无法解析为 JSON。请重新输出，且只输出一个 json 代码块："
    '不要输出任何解释文字，顶层必须是 {"sections": [...]}，'
    "确保 JSON 完整闭合（花括号、引号全部配对），字符串内换行用 \\n 转义。"
)

SPARSE_RETRY_SUFFIX = (
    "\n\n【内容充实度要求（必须遵守）】"
    "上一次输出过于简略。请重新生成完整分析：每个章节内容充实具体，"
    "关键结论带证据锚点（页码/图号/表号/文献编号）与具体数值；"
    "笔记原子化每类至少 3 条并带 citations；优缺点至少 3 条优点、2 条缺点；"
    "研究启发至少 3 条；论证逻辑链路图用固定表格且至少 5 行。"
    "仍只输出一个 json 代码块。"
)
