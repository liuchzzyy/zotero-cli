"""编排一次 ai-note 运行：输入解析后的 PDF，输出渲染好的笔记与关键词。

流程是：解析条目及其 PDF 附件，经 MinerU 缓存解析，判定论文类型，为匹配
的模板构造 prompt（含正文插图），把 JSON sections 渲染为 HTML 笔记，并推导
Extra short-note 关键词。仅生成 short-note 的模式会从已有 AI note 回填这些
关键词，而不重新生成笔记。
"""

from __future__ import annotations

from collections.abc import Callable

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.classification import (
    BOOK_ITEM_TYPES,
    CLASSIFY_CHARS,
    TEMPLATE_BY_TYPE,
    _classify,
)
from zotero_cli.ai_notes.constants import (
    ANALYZED_TAG,
    NOT_ANALYZED_TAG,
    NOTE_TITLE_PREFIX,
)
from zotero_cli.ai_notes.documents import (
    _sections_to_text,
    collect_documents,
)
from zotero_cli.ai_notes.errors import EvidenceBudgetError, NoteAnalysisError
from zotero_cli.ai_notes.evidence import (
    build_evidence,
    model_supports_request,
    plan_with_coverage,
)
from zotero_cli.ai_notes.generation import _chat_with_retry, build_analysis_prompt
from zotero_cli.ai_notes.persistence import _writer_call, mark_short_note_failure, save_analysis_note, save_short_note
from zotero_cli.ai_notes.rendering import render_note
from zotero_cli.ai_notes.short_notes import _analyze_short_note_only, _generate_short_note
from zotero_cli.ai_notes.templates import (
    load_template,
)
from zotero_cli.ai_notes.text import join_spaced_digits, prepare
from zotero_cli.pdf_rag import clean_html
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import ZoteroWriter


def _require_ai_client(ai_client: AiClient | None) -> AiClient:
    if ai_client is None:
        raise NoteAnalysisError("AI note 配置不完整", code="configuration_error")
    return ai_client


def _plain_title(title: str) -> str:
    """把 Zotero 条目标题渲染为纯文本，用作笔记 h1 标题。"""
    return " ".join(clean_html(title or "").split())


def analyze_item(
    reader: ZoteroReader,
    writer: ZoteroWriter | None,
    ai_client: AiClient | None,
    key: str,
    *,
    force: bool = False,
    no_tag: bool = False,
    dry_run: bool = False,
    no_short_note: bool = False,
    short_note_only: bool = False,
    progress: Callable[[str, str], None] | None = None,
    max_extracted_chars: int | None = None,
) -> dict:
    item = reader.get_item(key)
    if item is None:
        raise NoteAnalysisError(f"条目 '{key}' 不存在", code="not_found")

    if short_note_only:
        if writer is None and not dry_run:
            raise NoteAnalysisError("写入凭证未配置", code="auth_missing")
        return _analyze_short_note_only(reader, writer, ai_client, item, key, progress, dry_run=dry_run)

    # 试运行是只读预览：即使条目已分析过也必须预览，而不是短路返回 already_analyzed。
    if not dry_run and ANALYZED_TAG in item.tags and not force:
        return {"status": "already_analyzed", "item_key": key, "item_type": item.item_type}

    structured_docs = collect_documents(reader, key, progress)
    try:
        evidence = build_evidence(structured_docs)
    except ValueError as exc:
        raise NoteAnalysisError(str(exc), code="validation_error") from exc
    if not evidence.text.strip():
        raise NoteAnalysisError(f"条目 '{key}' 的 MinerU structured content 为空", code="runtime_error")

    # 在不向模型发送文本的前提下预览元数据/默认模板。
    if dry_run:
        paper_type = "book" if item.item_type in BOOK_ITEM_TYPES else "research_article"
    else:
        classify_sample = (evidence.main_text or evidence.text)[:CLASSIFY_CHARS]
        paper_type = _classify(item, classify_sample, _require_ai_client(ai_client), progress)
    if paper_type == "uncertain":
        if not dry_run and not no_tag:
            if writer is None:
                raise NoteAnalysisError("写入凭证未配置", code="auth_missing")
            _writer_call("写入 tag ai/uncertain", writer.add_tags, key, [NOT_ANALYZED_TAG])
        return {"status": "uncertain", "item_key": key, "paper_type": paper_type}
    if paper_type not in TEMPLATE_BY_TYPE:
        paper_type = "research_article"

    template_name = TEMPLATE_BY_TYPE[paper_type]
    template = load_template(template_name)
    if max_extracted_chars is None:
        max_chars = ai_client.config.max_extracted_chars if ai_client is not None else 0
    else:
        max_chars = max_extracted_chars

    cleaned = join_spaced_digits(evidence.text)
    if max_chars > 0 and len(cleaned) > max_chars:
        raise NoteAnalysisError(
            "required structured text exceeds configured max_extracted_chars; no evidence truncated",
            code="validation_error",
            parkable=True,
        )

    total_refs = evidence.main_count + evidence.si_count
    # 在任何 Zotero 变更之前，对整个带来源标注的 prompt 做预先校验。
    prompt = build_analysis_prompt(item, template, cleaned)
    model = (ai_client.config.model if ai_client is not None else "") or "group/daily"
    try:
        plan, prompt, omitted_numbers = plan_with_coverage(evidence, prompt, model)
    except ValueError as exc:
        raise NoteAnalysisError(
            str(exc), code="validation_error", parkable=isinstance(exc, EvidenceBudgetError)
        ) from exc
    figures = plan.images
    chars_used = len(cleaned)
    if progress:
        progress(
            "figures",
            f"正文 {evidence.main_count}/{evidence.main_count}，SI {len(plan.si_images)}/{evidence.si_count} 张随请求发送",
        )

    if dry_run:
        return {
            "status": "dry_run",
            "item_key": key,
            "item_type": item.item_type,
            "paper_type": paper_type,
            "template": template_name,
            "pdf_count": len(structured_docs),
            "chars": chars_used,
            "figures": len(figures),
            "figure_refs": total_refs,
            "main_images": evidence.main_count,
            "si_images": len(plan.si_images),
            "si_candidates": evidence.si_count,
            "omitted_images": len(omitted_numbers),
            "skipped_images": plan.skipped,
            "estimated_tokens_upper_bound": plan.estimated_tokens,
            "request_bytes": plan.request_bytes,
            "budget_model": plan.model,
            "prompt_preview": prompt[:1200],
        }

    client = _require_ai_client(ai_client)
    # 初始方案只覆盖主路由。备用模型必须能在已核实的限制下接收这同一份
    # 最终 prompt 与图片集合。
    allowed_models = {
        candidate for candidate in client.get_model_chain() if model_supports_request(candidate, prompt, len(figures))
    }
    if progress:
        progress("analyze", f"paper_type={paper_type}")
    if writer is None:
        raise NoteAnalysisError("写入凭证未配置", code="auth_missing")

    sections = _chat_with_retry(client, prompt, figures=figures, allowed_models=allowed_models, progress=progress)

    # 非法或为空的 JSON 列表也可能只渲染出生成的标题。
    if not sections or not clean_html(render_note(sections, title="")).strip():
        raise NoteAnalysisError(f"条目 '{key}' 的 AI 分析未能生成任何有效章节内容", code="runtime_error")

    note_title = NOTE_TITLE_PREFIX + prepare(_plain_title(item.title))
    html_note = render_note(sections, title=note_title)
    note_key = save_analysis_note(writer, key, html_note, no_tag=no_tag)

    short_note_status = "skipped"
    short_note_error = ""
    if not no_short_note:
        try:
            short_note = _generate_short_note(client, _sections_to_text(sections), progress)
            short_note_status, short_note_error = save_short_note(writer, key, short_note)
        except NoteAnalysisError as error:
            short_note_status = "failed"
            short_note_error = mark_short_note_failure(writer, key, error)

    return {
        "status": "ok",
        "item_key": key,
        "note_key": note_key,
        "paper_type": paper_type,
        "sections": len(sections),
        "short_note": short_note_status,
        "short_note_error": short_note_error,
    }
