"""Orchestrate one ai_analyze run: parsed PDFs in, rendered note and keywords out.

The flow is: resolve the item and its PDF attachments, parse them through the
MinerU cache, classify the paper, build the prompt (with main-text figures) for
the matching template, render the JSON sections into an HTML note, and derive the
Extra short-note keywords. The short-note-only mode backfills those keywords from
an existing AI note without regenerating it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from zotero_cli.core.mineru import MinerUError, MinerUParseCache, MinerUParseResult
from zotero_cli.core.notes.classification import (
    BOOK_ITEM_TYPES,
    CLASSIFY_CHARS,
    TEMPLATE_BY_TYPE,
    _chat,
    _classify,
    extract_json_object,
)
from zotero_cli.core.notes.client import AiClient
from zotero_cli.core.notes.constants import (
    ANALYZED_TAG,
    KEYWORDS_TAG,
    MAX_FIGURES,
    MIN_SECTIONS,
    NO_KEYWORDS_TAG,
    NOT_ANALYZED_TAG,
    NOTE_TITLE_PREFIX,
    PARSE_RETRY_SUFFIX,
    SPARSE_RETRY_SUFFIX,
)
from zotero_cli.core.notes.documents import (
    _drop_duplicate_parts,
    _item_authors,
    _item_journal,
    _sections_to_text,
)
from zotero_cli.core.notes.errors import NoteAnalysisError
from zotero_cli.core.notes.figures import load_figures, mark_figures
from zotero_cli.core.notes.renderer import render_note
from zotero_cli.core.notes.short_note import _generate_short_note
from zotero_cli.core.notes.templates import format_template, load_template
from zotero_cli.core.notes.text import join_spaced_digits, prepare
from zotero_cli.core.rag import clean_html, infer_pdf_kind
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.models import Item


def _analyze_short_note_only(
    reader: ZoteroReader,
    writer: ZoteroWriter | None,
    ai_client: AiClient,
    item: Item,
    key: str,
    progress: Callable[[str, str], None] | None,
    *,
    dry_run: bool = False,
) -> dict:
    """Backfill mode: generate keywords from the existing AI note and write Extra short-note."""
    notes = reader.get_notes(key)
    if not notes:
        raise NoteAnalysisError(f"条目 '{key}' 没有可用的 AI 笔记", code="validation_error")
    # reader.get_notes 按创建顺序返回；含 "AI条目分析" 的笔记中选最新一条（我们的工作流总是后创建），
    # 避免旧版工作流的同名笔记（可能更长）抢走选择。
    ai_notes = [n for n in notes if "AI条目分析" in (n.content or "")]
    if ai_notes:
        chosen = ai_notes[-1]
    else:
        chosen = max(notes, key=lambda n: len(n.content or ""))
    note_text = clean_html((chosen.content or "").strip())
    if not note_text:
        raise NoteAnalysisError(f"条目 '{key}' 的 AI 笔记内容为空", code="validation_error")

    result: dict = {
        "status": "short_note_only",
        "item_key": key,
        "item_type": item.item_type,
        "note_key": chosen.key,
    }
    if dry_run:
        prompt = format_template(load_template("short-note"), fulltext=note_text)
        return {
            **result,
            "status": "dry_run",
            "template": "short-note",
            "chars": len(note_text),
            "prompt_preview": prompt[:1200],
        }
    if writer is None:
        raise NoteAnalysisError("写入凭证未配置", code="auth_missing")
    try:
        short_note = _generate_short_note(ai_client, note_text, progress)
        writer.update_short_note(key, short_note)
        writer.add_tags(key, [KEYWORDS_TAG])
        result["short_note"] = "ok"
    except Exception as exc:
        writer.add_tags(key, [NO_KEYWORDS_TAG])
        result["short_note"] = "failed"
        result["short_note_error"] = str(exc)[:160]
    return result


def analyze_item(
    reader: ZoteroReader,
    writer: ZoteroWriter | None,
    ai_client: AiClient,
    key: str,
    *,
    force: bool = False,
    no_tag: bool = False,
    dry_run: bool = False,
    no_short_note: bool = False,
    short_note_only: bool = False,
    progress: Callable[[str, str], None] | None = None,
) -> dict:
    item = reader.get_item(key)
    if item is None:
        raise NoteAnalysisError(f"条目 '{key}' 不存在", code="not_found")

    if short_note_only:
        if writer is None and not dry_run:
            raise NoteAnalysisError("写入凭证未配置", code="auth_missing")
        return _analyze_short_note_only(reader, writer, ai_client, item, key, progress, dry_run=dry_run)

    if ANALYZED_TAG in item.tags and not force:
        return {"status": "already_analyzed", "item_key": key, "item_type": item.item_type}

    attachments = reader.get_pdf_attachments(key)
    if not attachments:
        raise NoteAnalysisError(f"条目 '{key}' 没有本地 PDF 附件", code="validation_error")

    pdf_paths = [a.path for a in attachments if a.path is not None and a.path.exists()]
    if not pdf_paths:
        raise NoteAnalysisError(f"条目 '{key}' 的 PDF 附件文件不存在", code="validation_error")

    if progress:
        progress("mineru", f"解析/读取缓存：{len(pdf_paths)} PDF(s)")
    try:
        pdf_parses = MinerUParseCache().ensure_many(pdf_paths)
    except MinerUError as exc:
        message = str(exc)
        code = "configuration_error" if "token not configured" in message.lower() else "network_error"
        raise NoteAnalysisError(message, code=code, retryable=code == "network_error") from exc

    main_parts: list[str] = []
    supp_parts: list[str] = []
    main_figure_refs: list[tuple[int, Path]] = []
    supp_figure_refs: list[tuple[int, Path]] = []
    for att in attachments:
        if att.path is None or att.path not in pdf_parses:
            continue
        parsed = pdf_parses[att.path]
        if not isinstance(parsed, MinerUParseResult):
            continue
        text = parsed.markdown
        if not text.strip():
            continue
        # Figures are only meaningful where the text is actually sent, so mark
        # them in the same pass that classifies the document. Numbers stay
        # continuous across PDFs, so a marker is unique inside the prompt.
        text, refs = mark_figures(text, parsed.root / "raw", start=len(main_figure_refs) + len(supp_figure_refs) + 1)
        kind = infer_pdf_kind(parsed.markdown, att.filename or att.key)
        (supp_figure_refs if kind == "supplementary" else main_figure_refs).extend(refs)
        if kind == "supplementary":
            supp_parts.append(text)
        else:
            main_parts.append(text)

    main_parts = _drop_duplicate_parts(main_parts)
    main_text = "\n\n".join(main_parts)
    if not main_text.strip() and supp_parts:
        main_parts, supp_parts = supp_parts, []
        main_text = "\n\n".join(main_parts)

    if not main_text.strip():
        failures = [str(value) for value in pdf_parses.values() if isinstance(value, Exception)]
        detail = f"：{failures[0]}" if failures else ""
        raise NoteAnalysisError(f"条目 '{key}' 的 MinerU Markdown 为空{detail}", code="runtime_error")

    full_text = main_text
    if supp_parts:
        full_text += "\n\n## 支撑信息\n\n" + "\n\n".join(supp_parts)

    # Preview a metadata/default template without sending text to the model.
    if dry_run:
        paper_type = "book" if item.item_type in BOOK_ITEM_TYPES else "research_article"
    else:
        paper_type = _classify(item, main_text[:CLASSIFY_CHARS], ai_client, progress)
    if paper_type == "uncertain":
        if not dry_run and not no_tag:
            if writer is None:
                raise NoteAnalysisError("写入凭证未配置", code="auth_missing")
            writer.add_tags(key, [NOT_ANALYZED_TAG])
        return {"status": "uncertain", "item_key": key, "paper_type": paper_type}
    if paper_type not in TEMPLATE_BY_TYPE:
        paper_type = "research_article"

    cleaned = join_spaced_digits(clean_html(full_text))
    max_chars = ai_client.config.max_extracted_chars
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]

    template_name = TEMPLATE_BY_TYPE[paper_type]
    template = load_template(template_name)
    prompt = format_template(
        template,
        title=item.title,
        authors=_item_authors(item),
        journal=_item_journal(item),
        date=item.date or "",
        doi=item.doi or "",
        fulltext=cleaned,
    )

    # Main-text panels are the evidence being judged; only spend leftover slots
    # on supporting information.
    figures = load_figures(main_figure_refs, MAX_FIGURES)
    figures += load_figures(supp_figure_refs, MAX_FIGURES - len(figures))
    total_refs = len(main_figure_refs) + len(supp_figure_refs)
    if progress and total_refs:
        progress("figures", f"附图 {len(figures)}/{total_refs} 张随请求发送")

    if dry_run:
        return {
            "status": "dry_run",
            "item_key": key,
            "item_type": item.item_type,
            "paper_type": paper_type,
            "template": template_name,
            "pdf_count": len(pdf_paths),
            "chars": len(cleaned),
            "figures": len(figures),
            "figure_refs": total_refs,
            "prompt_preview": prompt[:1200],
        }

    if progress:
        progress("analyze", f"paper_type={paper_type}")
    if writer is None:
        raise NoteAnalysisError("写入凭证未配置", code="auth_missing")

    answer = _chat(ai_client, prompt, progress=progress, figures=figures)
    try:
        sections = extract_json_object(answer).get("sections", [])
    except NoteAnalysisError:
        if progress:
            progress("retry", "AI 输出无法解析为 JSON，追加格式要求重试一次")
        # A long answer gets truncated when the context is already full of
        # figures, so the retry carries half of them.
        retry_figures = figures[: max(1, len(figures) // 2)] if figures else figures
        retry_answer = _chat(ai_client, prompt + PARSE_RETRY_SUFFIX, progress=progress, figures=retry_figures)
        try:
            sections = extract_json_object(retry_answer).get("sections", [])
        except NoteAnalysisError as retry_error:
            snippet = retry_answer[:300].replace("\n", " ")
            raise NoteAnalysisError(
                f"AI 输出两次都无法解析为 JSON（响应开头：{snippet}...）",
                code="runtime_error",
            ) from retry_error
    if not isinstance(sections, list):
        sections = []
    if len(sections) < MIN_SECTIONS:
        if progress:
            progress("retry", f"输出过简略（{len(sections)} 个 block），追加内容充实度要求重试一次")
        try:
            retry_answer = _chat(ai_client, prompt + SPARSE_RETRY_SUFFIX, progress=progress, figures=figures)
            retried = extract_json_object(retry_answer).get("sections", [])
            if isinstance(retried, list) and len(retried) > len(sections):
                sections = retried
        except NoteAnalysisError:
            pass

    # An invalid/nonempty JSON list can still render only the generated title.
    if not sections or not clean_html(render_note(sections, title="")).strip():
        raise NoteAnalysisError(f"条目 '{key}' 的 AI 分析未能生成任何有效章节内容", code="runtime_error")

    note_title = NOTE_TITLE_PREFIX + prepare(item.title)
    html_note = render_note(sections, title=note_title)
    note_key = writer.add_note(key, html_note)
    if not no_tag:
        writer.add_tags(key, [ANALYZED_TAG])

    short_note_status = "skipped"
    short_note_error = ""
    if not no_short_note:
        try:
            short_note = _generate_short_note(ai_client, _sections_to_text(sections), progress)
            writer.update_short_note(key, short_note)
            writer.add_tags(key, [KEYWORDS_TAG])
            short_note_status = "ok"
        except Exception as exc:  # graceful degradation: the note is already written
            writer.add_tags(key, [NO_KEYWORDS_TAG])
            short_note_status = "failed"
            short_note_error = str(exc)[:160]

    return {
        "status": "ok",
        "item_key": key,
        "note_key": note_key,
        "paper_type": paper_type,
        "sections": len(sections),
        "short_note": short_note_status,
        "short_note_error": short_note_error,
    }
