"""AI note 分析流水线各模块共用的常量。"""

from __future__ import annotations

ANALYZED_TAG = "ai/noted"
NOT_ANALYZED_TAG = "ai/uncertain"
KEYWORDS_TAG = "ai/keywords"
NO_KEYWORDS_TAG = "ai/no_keywords"
NOTE_TITLE_PREFIX = "AI条目分析 - "

MIN_SECTIONS = 10

# *short-note* prompt 的硬上限（模板 + 被压缩的 AI note）。
# 分析 prompt 改用 `[ai_notes].max_extracted_chars` 限界
# （默认 250,000，0 表示不限），并受 `ai_notes/evidence.py`
# 中 800k 估算单位的单请求预算约束；两处限制相互独立。
MAX_PROMPT_CHARS = 220_000
PROMPT_RESERVE_CHARS = 2_000

# 逐字段上限，避免刻意构造或异常的 Zotero 元数据在（可截断的）全文
# 之前就耗尽 prompt 预算。
MAX_TITLE_CHARS = 2_000
MAX_ABSTRACT_CHARS = 20_000
MAX_AUTHORS_CHARS = 10_000
MAX_JOURNAL_CHARS = 1_000
MAX_DATE_CHARS = 100
MAX_DOI_CHARS = 500

# 模型输出不可用时追加到 prompt 末尾的重试指令。
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
