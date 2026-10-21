"""依据条目元数据与正文样本判定文献类型及对应模板。"""

from __future__ import annotations

from collections.abc import Callable

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.constants import MAX_ABSTRACT_CHARS, MAX_TITLE_CHARS
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes.generation import _chat, extract_json_object, wrap_untrusted
from zotero_cli.ai_notes.templates import load_template
from zotero_cli.zotero.models import Item

CLASSIFY_CHARS = 4000
BOOK_ITEM_TYPES = {"book", "bookSection"}
# 每个分类结果对应的模板；其他结果回退到 research_article。
TEMPLATE_BY_TYPE = {
    "research_article": "research-article",
    "review_article": "review-article",
    "book": "book",
}


def _cap_metadata(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def build_classify_prompt(item: Item, sample_text: str) -> str:
    """构造分类 prompt，并用分隔标记包裹不可信的 Zotero/PDF 文本。"""
    template = load_template("classify-item")
    title = _cap_metadata(item.title or "", MAX_TITLE_CHARS)
    abstract = _cap_metadata(item.abstract or "", MAX_ABSTRACT_CHARS)
    return (
        template
        + "\n\n## 待判定的条目\n\n"
        + "标题：\n"
        + wrap_untrusted(title, "title")
        + "\n\n摘要：\n"
        + wrap_untrusted(abstract, "abstract")
        + "\n\n正文开头：\n"
        + wrap_untrusted(sample_text, "document")
        + "\n"
    )


def _classify(
    item: Item,
    sample_text: str,
    ai_client: AiClient,
    progress: Callable[[str, str], None] | None,
) -> str:
    if item.item_type in BOOK_ITEM_TYPES:
        return "book"
    if progress:
        progress("classify", f"item_type={item.item_type}")
    prompt = build_classify_prompt(item, sample_text)
    answer = _chat(ai_client, prompt, progress=progress)
    try:
        data = extract_json_object(answer)
        return str(data.get("paper_type", "")).strip() or "uncertain"
    except NoteAnalysisError:
        return "uncertain"
