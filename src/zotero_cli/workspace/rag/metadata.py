"""RAG 流水线的文本规范化与论文类型 metadata 辅助函数。"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any

# 用于识别补充材料文档的封面/页眉。它们只在文本顶部附近的*行首*进行匹配，
# 因此仅仅提到“Supporting Information”的普通正文不会触发分类。
_SUPPLEMENTARY_COVER_HINTS = (
    "electronic supplementary material",
    "electronic supplementary information",
    "supplementary material",
    "supplementary information",
    "supporting information",
    "supporting info",
    "supporting data",
    "supplementary data",
    "electronic annex",
    "si appendix",
)
_SUPPLEMENTARY_HINTS = _SUPPLEMENTARY_COVER_HINTS + ("figure s1", "table s1")

# 真实封面行上可能跟在封面短语之后的词（例如“Supporting Information
# *for* <title>”）。其他情况说明该行是正文。
_COVER_CONTINUATIONS = frozenset({"for", "of", "to", "esi"})

# 强主体论文线索：排版后的论文前置信息。弱线索是补充材料从母论文继承的
# 出版商标记——例如 `sciadv.aea1401_sm.pdf` 的 SI 封面带有论文 DOI——因此
# 只有在排除所有补充材料线索之后才检查它们。
_MAIN_PAPER_HINTS = (
    "cite this:",
    "broader context",
    "abstract",
    "introduction",
)
_WEAK_MAIN_PAPER_HINTS = (
    "received ",
    "accepted ",
    "doi:",
)

# 附件文件名是可靠的补充材料信号（“..._SI.pdf”、“supplementary-material.pdf”）；
# token 在非字母数字分隔处匹配，使“sodium”“spin”等普通词不会被误读为“si”。
_SUPPLEMENTARY_FILENAME_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:si|esi|supp|suppl|supplementary|supporting)(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)


def _supplementary_filename(filename: str) -> bool:
    return bool(filename) and bool(_SUPPLEMENTARY_FILENAME_RE.search(filename))


def _cover_line(line: str) -> bool:
    """当文本为 `Supporting Information`、`Supplementary Material for <title>` 等时返回 True。

    按前缀而非整行匹配，因为封面变体会追加所属论文标题或期刊名。短语之后必须跟连接词或左括号；
    像“Supporting Information is available free of charge”这类正文仍保持默认的 main 类型。
    """
    text = line.strip(" #\t\r*-_")
    lowered = text.lower()
    for phrase in _SUPPLEMENTARY_COVER_HINTS:
        if not lowered.startswith(phrase):
            continue
        rest = text[len(phrase) :].lstrip(" \t")
        if not rest.strip(" .,:;-–—"):
            return True
        if rest[0] in "(:[—–-":
            return True
        if rest.split()[0].strip(".,;:").lower() in _COVER_CONTINUATIONS:
            return True
    return False


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in text.lower().split():
        word = re.sub(r"[.,;:!?()\"'\[\]{}]+$", "", word)
        word = re.sub(r"^[.,;:!?()\"'\[\]{}]+", "", word)
        if word:
            tokens.append(word)
    return tokens


def build_metadata_chunk(title: str, authors: str, abstract: str | None, tags: list[str]) -> str:
    parts = [f"Title: {title}", f"Authors: {authors}"]
    if abstract:
        parts.append(f"Abstract: {abstract}")
    if tags:
        parts.append(f"Tags: {', '.join(tags)}")
    return "\n".join(parts)


def infer_pdf_kind(text: str, filename: str = "") -> str:
    head = f"{filename}\n{text[:2500]}".lower()
    extended = f"{filename}\n{text[:10000]}".lower()
    lead = f"{filename}\n{text[:500]}".lower()
    early = f"{filename}\n{text[:1200]}".lower()
    if _supplementary_filename(filename) and "cite this:" not in lead:
        return "supplementary"
    if "cite this:" not in lead and any(_cover_line(line) for line in lead.splitlines()):
        return "supplementary"
    if any(hint in head for hint in _MAIN_PAPER_HINTS):
        return "main"
    if "experimental section" in head:
        return "supplementary"
    # 上述行规则未能识别的 SI 封面，其优先级仍高于出版商标记：
    # `sciadv.aea1401_sm.pdf` 在第 247 个字符处带有论文 DOI。只有*早期*提及才
    # 算数——主体论文会在数千字符之后才引用自己的 SI（条目 DDM2563M：第 9073 个
    # 字符），这不得改变判定。
    if any(hint in early for hint in _SUPPLEMENTARY_HINTS):
        return "supplementary"
    if any(hint in head for hint in _WEAK_MAIN_PAPER_HINTS):
        return "main"
    if any(hint in extended for hint in _SUPPLEMENTARY_HINTS):
        return "supplementary"
    return "main"


# --- 插图/表格编号 ----------------------------------------------------------
# 无线索结构：把自己子图编为“Figure S1”、“Supplementary Fig. 3”或“Table S2”的
# 文档是补充材料；编为“Figure 1”/“Table 1”的则是论文。编号在各种出版商版式下
# 都保持不变，因此它胜过封面文本和文件名——但 MinerU 会把“S”放在两个位置
# （编号上或限定词上），两种都必须接受。
_CAPTION_LABEL_RE = re.compile(
    r"\b(?:(?P<qualifier>supplementary|supporting|esi)\s+)?"
    r"(?P<label>figure|fig\.?|scheme|table)\s*\.?\s*"
    r"(?P<number>s\s*\d+|\d+)",
    re.IGNORECASE,
)
_CAPTION_FIELDS = {"image": "image_caption", "chart": "chart_caption", "table": "table_caption"}

# 排版后的论文前置信息。这是唯一优先级高于题注编号的信号，因为把主体正文
# 降级为补充材料是代价高昂的错误：其插图随后会变成可选并被采样丢弃。
_FRONT_MATTER_ABSTRACT_CHARS = 6000
_FRONT_MATTER_INTRO_CHARS = 12_000


@dataclass(frozen=True)
class CaptionCounts:
    """单个文档如何为自己的插图和表格编号。"""

    figure_supplementary: int = 0
    figure_plain: int = 0
    table_supplementary: int = 0
    table_plain: int = 0

    @property
    def supplementary(self) -> int:
        return self.figure_supplementary + self.table_supplementary

    @property
    def plain(self) -> int:
        return self.figure_plain + self.table_plain

    @property
    def kind(self) -> str:
        """仅依据编号判定 `supplementary`、`main` 或 `unknown`。

        插图优先判定，表格仅用于打破平局：补充材料会为*所有*子图编号，因此插图使用普通编号的
        文档就是论文，即使它还带有零星的“Supplementary Table”题注。
        """
        for supplementary, plain in (
            (self.figure_supplementary, self.figure_plain),
            (self.table_supplementary, self.table_plain),
        ):
            if supplementary > plain:
                return "supplementary"
            if plain > supplementary:
                return "main"
        return "unknown"


@dataclass(frozen=True)
class PdfDocument:
    """类型分类器视角下的单个已解析附件。"""

    identifier: str
    filename: str
    markdown: str
    blocks: Any


def _iter_blocks(content: Any) -> Iterator[dict[str, Any]]:
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block
    elif isinstance(content, dict):
        for key in ("content_list", "blocks", "content"):
            if isinstance(content.get(key), list):
                yield from _iter_blocks(content[key])
                return


def _caption_text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(part) for part in value)
    return value if isinstance(value, str) else ""


def caption_counts(blocks: Any) -> CaptionCounts:
    """统计 S 编号与普通编号的插图、表格题注数量。"""
    figure_supplementary = figure_plain = table_supplementary = table_plain = 0
    for block in _iter_blocks(blocks):
        field = _CAPTION_FIELDS.get(str(block.get("type") or ""))
        if field is None:
            continue
        match = _CAPTION_LABEL_RE.search(_caption_text(block.get(field)))
        if match is None:
            continue
        numbered_s = bool(match.group("qualifier")) or match.group("number").lower().startswith("s")
        if match.group("label").lower().rstrip(".") == "table":
            if numbered_s:
                table_supplementary += 1
            else:
                table_plain += 1
        elif numbered_s:
            figure_supplementary += 1
        else:
            figure_plain += 1
    return CaptionCounts(figure_supplementary, figure_plain, table_supplementary, table_plain)


def _has_front_matter(markdown: str) -> bool:
    low = markdown.lower()
    return "abstract" in low[:_FRONT_MATTER_ABSTRACT_CHARS] and "introduction" in low[:_FRONT_MATTER_INTRO_CHARS]


def _kinds(documents: Sequence[PdfDocument], supplements: Sequence[str]) -> dict[str, str]:
    """指出补充材料集合；其余文档均为主体正文。"""
    return {doc.identifier: "supplementary" if doc.identifier in supplements else "main" for doc in documents}


def resolve_pdf_kinds(documents: Sequence[PdfDocument]) -> dict[str, str]:
    """将同一附件的条目放在一起分类，而不是逐个分类。

    单个附件按定义即为主体正文；有两个及以上时，题注为 S 编号的文档是补充材料，其余为主体正文。
    规则按顺序排列，使*保守*的结论优先：把文档保留为主体正文最多只多出图片，
    而把真实论文降级为补充材料会让其插图被采样丢弃。
    """
    if not documents:
        return {}
    if len(documents) == 1:
        return {documents[0].identifier: "main"}
    counts = {doc.identifier: caption_counts(doc.blocks) for doc in documents}
    front = {doc.identifier: _has_front_matter(doc.markdown) for doc in documents}

    def rest(main: str) -> list[str]:
        """除 `main` 之外的所有文档——当指明一篇论文时，即为补充材料集合。"""
        return [doc.identifier for doc in documents if doc.identifier != main]

    # 合并了论文与 SI 的 PDF 同时带有前置信息和 S 编号子图；以前置信息为准。
    numbered = {
        doc.identifier: (
            "main"
            if front[doc.identifier] and counts[doc.identifier].kind == "supplementary"
            else counts[doc.identifier].kind
        )
        for doc in documents
    }

    # 1. 恰好一个文档用 S 为其子图编号。
    s_numbered = [identifier for identifier, kind in numbered.items() if kind == "supplementary"]
    if len(s_numbered) == 1:
        return _kinds(documents, s_numbered)
    # 2. 没有文档的插图标有“S”，但恰好一个文档带有 S 编号的题注——Nature 风格的
    #    补充材料插图用普通编号，而表格编为“Supplementary Table 1”。
    any_s = [identifier for identifier, value in counts.items() if value.supplementary and not front[identifier]]
    if len(any_s) == 1:
        return _kinds(documents, any_s)
    # 3. 恰好一个普通编号的文档；与之并列的未编号文档就是补充材料。
    plain = [identifier for identifier, kind in numbered.items() if kind == "main"]
    if len(plain) == 1:
        return _kinds(documents, rest(plain[0]))
    # 4. 回退到 `infer_pdf_kind` 的封面/文件名启发式规则。
    textual = [
        doc.identifier
        for doc in documents
        if not front[doc.identifier] and infer_pdf_kind(doc.markdown, doc.filename) == "supplementary"
    ]
    if len(textual) == 1:
        return _kinds(documents, textual)
    # 5. 无确定结论：较长的文档是论文。
    return _kinds(documents, rest(max(documents, key=lambda doc: len(doc.markdown)).identifier))


def get_pdf_kind_from_source(source: str) -> str | None:
    if source.startswith("pdf:main:"):
        return "main"
    if source.startswith("pdf:supplementary:"):
        return "supplementary"
    return None


def filter_ranked_results_by_pdf_kind(
    results: list[tuple[int, float, dict]],
    pdf_kind: str | None,
) -> list[tuple[int, float, dict]]:
    if not pdf_kind or pdf_kind == "any":
        return results
    filtered: list[tuple[int, float, dict]] = []
    for cid, score, chunk in results:
        kind = get_pdf_kind_from_source(chunk.get("source", ""))
        if kind == pdf_kind:
            filtered.append((cid, score, chunk))
    return filtered


def _decode_numeric_entity(match: re.Match[str], *, base: int) -> str:
    raw = match.group(1)
    try:
        codepoint = int(raw, base)
    except ValueError:
        return match.group(0)
    # 拒绝 NUL、代理码点以及 Unicode 范围之外的值；格式错误的实体按字面保留，
    # 而不是让调用方崩溃。
    if codepoint <= 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
        return match.group(0)
    return chr(codepoint)


def clean_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<td[^>]*>(.*?)</td>", r"\1\t", text, flags=re.IGNORECASE)
    text = re.sub(r"<th[^>]*>(.*?)</th>", r"\1\t", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<tr[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</table>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<table[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&#(\d+);", lambda match: _decode_numeric_entity(match, base=10), text)
    text = re.sub(r"&#x([0-9a-fA-F]+);", lambda match: _decode_numeric_entity(match, base=16), text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&apos;", "'", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
