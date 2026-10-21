"""编排一次 ai-note 运行：输入解析后的 PDF，输出渲染好的笔记与关键词。

流程是：解析条目及其 PDF 附件，经 MinerU 缓存解析，判定论文类型，为匹配
的模板构造 prompt（含正文插图），把 JSON sections 渲染为 HTML 笔记，并推导
Extra short-note 关键词。仅生成 short-note 的模式会从已有 AI note 回填这些
关键词，而不重新生成笔记。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zotero_cli.ai_notes.classification import (
    BOOK_ITEM_TYPES,
    CLASSIFY_CHARS,
    TEMPLATE_BY_TYPE,
    _chat,
    _classify,
    extract_json_object,
    wrap_untrusted,
)
from zotero_cli.ai_notes.client import AiClient
from zotero_cli.ai_notes.constants import (
    ANALYZED_TAG,
    KEYWORDS_TAG,
    MAX_AUTHORS_CHARS,
    MAX_DATE_CHARS,
    MAX_DOI_CHARS,
    MAX_JOURNAL_CHARS,
    MAX_TITLE_CHARS,
    MIN_SECTIONS,
    NO_KEYWORDS_TAG,
    NOT_ANALYZED_TAG,
    NOTE_TITLE_PREFIX,
    PARSE_RETRY_SUFFIX,
    SPARSE_RETRY_SUFFIX,
)
from zotero_cli.ai_notes.documents import (
    _item_authors,
    _item_journal,
    _kept_duplicate_indices,
    _sections_to_text,
)
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes.renderer import render_note
from zotero_cli.ai_notes.short_note import _generate_short_note
from zotero_cli.ai_notes.structured_input import (
    EvidenceBudgetError,
    build_evidence,
    model_supports_request,
    plan_with_coverage,
)
from zotero_cli.ai_notes.templates import (
    fit_text_to_prompt,
    format_template,
    load_template,
)
from zotero_cli.ai_notes.text import join_spaced_digits, prepare
from zotero_cli.ai_pdf import MinerUError, MinerUParseCache, MinerUParseResult
from zotero_cli.workspace.rag import PdfDocument, clean_html, resolve_pdf_kinds
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import ZoteroWriter
from zotero_cli.zotero.models import Item

_MISSING_FIELD = "未知"

# 表示服务器明确拒绝该变更的 writer 错误码。对这类错误写入未提交，
# 因此可释放幂等预占；其他 writer 失败的结果均为未知。
_DEFINITE_REJECTION_CODES = frozenset(
    {"auth_invalid", "auth_missing", "validation_error", "not_found", "configuration_error", "confirmation_required"}
)


def _require_ai_client(ai_client: AiClient | None) -> AiClient:
    if ai_client is None:
        raise NoteAnalysisError("AI note 配置不完整", code="configuration_error")
    return ai_client


def _writer_call(action: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """执行 writer 变更，把原始失败转换为带类型的流水线错误。

    抛出的错误保留 writer 的错误码/可重试提示，并标记该变更为已提交，
    使结果可能已到达 Zotero 时不会释放幂等预占。
    """
    try:
        return callback(*args, **kwargs)
    except NoteAnalysisError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", "runtime_error"))
        retryable = bool(getattr(exc, "retryable", False))
        raise NoteAnalysisError(
            f"{action}失败：{type(exc).__name__}: {exc}",
            code=code,
            retryable=retryable,
            committed=code not in _DEFINITE_REJECTION_CODES,
        ) from exc


def _wrap_field(value: str, label: str, max_chars: int) -> str:
    text = (value or "").strip()
    if len(text) > max_chars:
        text = text[:max_chars]
    return wrap_untrusted(text or _MISSING_FIELD, label)


def _plain_title(title: str) -> str:
    """把 Zotero 条目标题渲染为纯文本，用作笔记 h1 标题。"""
    return " ".join(clean_html(title or "").split())


def _chat_with_retry(
    client: AiClient,
    prompt: str,
    *,
    figures: list[tuple[int, str]],
    allowed_models: set[str],
    progress: Callable[[str, str], None] | None,
) -> list[object]:
    """执行模型对话并解析 sections JSON，针对格式与内容稀疏各做一轮重试。"""
    answer = _chat(client, prompt, progress=progress, figures=figures, allowed_models=allowed_models)
    try:
        sections = extract_json_object(answer).get("sections", [])
    except NoteAnalysisError:
        if progress:
            progress("retry", "AI 输出无法解析为 JSON，追加格式要求重试一次")
        # 格式重试必须使用同一组图片。只有 AiClient 明确拒绝图像能力时，
        # 才允许以零图片重试。
        retry_answer = _chat(
            client, prompt + PARSE_RETRY_SUFFIX, progress=progress, figures=figures, allowed_models=allowed_models
        )
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
            retry_answer = _chat(
                client, prompt + SPARSE_RETRY_SUFFIX, progress=progress, figures=figures, allowed_models=allowed_models
            )
            retried = extract_json_object(retry_answer).get("sections", [])
            if isinstance(retried, list) and len(retried) > len(sections):
                sections = retried
        except NoteAnalysisError:
            pass
    return sections


def _analyze_short_note_only(
    reader: ZoteroReader,
    writer: ZoteroWriter | None,
    ai_client: AiClient | None,
    item: Item,
    key: str,
    progress: Callable[[str, str], None] | None,
    *,
    dry_run: bool = False,
) -> dict:
    """回填模式：从已有 AI note 生成关键词，并写入 Extra short-note。"""
    notes = reader.get_notes(key)
    if not notes:
        raise NoteAnalysisError(f"条目 '{key}' 没有可用的 AI 笔记", code="validation_error")
    # reader.get_notes 按创建顺序返回；标题前缀命中 AI 条目的笔记中选最新一条（我们的工作流总是后创建），
    # 避免旧版工作流的同名笔记（可能更长）抢走选择。
    ai_notes = [n for n in notes if NOTE_TITLE_PREFIX in (n.content or "")]
    if ai_notes:
        chosen = ai_notes[-1]
    else:
        chosen = max(notes, key=lambda n: len(n.content or ""))
    note_text = clean_html((chosen.content or "").strip())
    if not note_text:
        raise NoteAnalysisError(f"条目 '{key}' 的 AI 笔记内容为空", code="validation_error")

    template = load_template("short-note")
    source_text = fit_text_to_prompt(template, note_text)
    result: dict = {
        "status": "short_note_only",
        "item_key": key,
        "item_type": item.item_type,
        "note_key": chosen.key,
    }
    if dry_run:
        prompt = format_template(template, fulltext=wrap_untrusted(source_text, "ai-note"))
        return {
            **result,
            "status": "dry_run",
            "template": "short-note",
            "chars": len(source_text),
            "prompt_preview": prompt[:1200],
        }
    if writer is None:
        raise NoteAnalysisError("写入凭证未配置", code="auth_missing")
    committed = False
    try:
        short_note = _generate_short_note(_require_ai_client(ai_client), note_text, progress)
        _writer_call("写入 Extra short-note", writer.update_short_note, key, short_note)
        committed = True
        result["short_note"] = "ok"
        try:
            _writer_call("写入 tag ai/keywords", writer.add_tags, key, [KEYWORDS_TAG])
        except NoteAnalysisError as tag_error:
            raise NoteAnalysisError(
                f"short-note 已写入（item {key}），但 ai/keywords tag 写入失败：{tag_error}；"
                "请勿直接重跑，先手工补 tag",
                code=tag_error.code,
                retryable=False,
                committed=True,
            ) from tag_error
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, NoteAnalysisError)
            else NoteAnalysisError(
                f"生成关键词失败：{type(exc).__name__}: {exc}", code="runtime_error", committed=committed
            )
        )
        error.committed = error.committed or committed
        if error.committed:
            raise error from exc
        # 生成在任何写入之前失败：尽力打上 ai/no_keywords tag。
        result["short_note"] = "failed"
        result["short_note_error"] = str(error)[:160]
        try:
            _writer_call("写入 tag ai/no_keywords", writer.add_tags, key, [NO_KEYWORDS_TAG])
        except NoteAnalysisError as tag_error:
            result["short_note_error"] = f"{error!s}; tag 写入失败：{tag_error}"[:160]
    return result


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

    parsed_pairs: list[tuple[Any, MinerUParseResult]] = []
    for att in attachments:
        if att.path is None:
            continue
        parsed = pdf_parses.get(att.path)
        if isinstance(parsed, MinerUParseResult):
            parsed_pairs.append((att, parsed))
    # 图注编号一次性决定整个条目的正文（MS）与支撑信息（SI）：SI 封面
    # 常重复文章标题和文件名，因此只有附件之间的比较才可靠（见 `resolve_pdf_kinds`）。
    kinds = resolve_pdf_kinds(
        [
            PdfDocument(att.key, att.filename or att.key, parsed.markdown, parsed.content_list)
            for att, parsed in parsed_pairs
        ]
    )
    structured_docs: list[tuple[str, str, MinerUParseResult]] = [
        (att.key, kinds[att.key], parsed) for att, parsed in parsed_pairs
    ]
    structured_docs.sort(key=lambda doc: doc[1] != "main")
    # 在整个条目范围内而不是按类别去重重复附件：同一份 PDF 可能被附加两次，
    # 不能因为其中一份被归为支撑信息就两份都保留。保留者维持自身类别。
    kept_indices = _kept_duplicate_indices([doc[2].markdown for doc in structured_docs])
    structured_docs = [structured_docs[index] for index in kept_indices]
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
    wrapped_fulltext = wrap_untrusted(cleaned, "document")
    prompt = format_template(
        template,
        title=_wrap_field(item.title, "title", MAX_TITLE_CHARS),
        authors=_wrap_field(_item_authors(item), "authors", MAX_AUTHORS_CHARS),
        journal=_wrap_field(_item_journal(item), "journal", MAX_JOURNAL_CHARS),
        date=_wrap_field(item.date or "", "date", MAX_DATE_CHARS),
        doi=_wrap_field(item.doi or "", "doi", MAX_DOI_CHARS),
        fulltext=wrapped_fulltext,
    )
    model = (ai_client.config.model if ai_client is not None else "") or "deepseek-flash"
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
    note_key = _writer_call("写入 note", writer.add_note, key, html_note)
    if not no_tag:
        try:
            _writer_call("写入 tag ai/noted", writer.add_tags, key, [ANALYZED_TAG])
        except NoteAnalysisError as tag_error:
            raise NoteAnalysisError(
                f"note 已写入（key {note_key}），但 ai/noted tag 写入失败：{tag_error}；"
                "请勿直接重跑以免生成重复 note，先手工补 tag",
                code=tag_error.code,
                retryable=False,
                committed=True,
            ) from tag_error

    short_note_status = "skipped"
    short_note_error = ""
    if not no_short_note:
        try:
            short_note = _generate_short_note(client, _sections_to_text(sections), progress)
            _writer_call("写入 Extra short-note", writer.update_short_note, key, short_note)
            short_note_status = "ok"
            try:
                _writer_call("写入 tag ai/keywords", writer.add_tags, key, [KEYWORDS_TAG])
            except NoteAnalysisError as tag_error:
                short_note_status = "failed"
                short_note_error = f"short-note 已写入，但 ai/keywords tag 写入失败：{tag_error}"[:160]
        except NoteAnalysisError as exc:
            short_note_status = "failed"
            short_note_error = str(exc)[:160]
            try:
                _writer_call("写入 tag ai/no_keywords", writer.add_tags, key, [NO_KEYWORDS_TAG])
            except NoteAnalysisError as tag_error:
                short_note_error = f"{short_note_error}; tag 写入失败：{tag_error}"[:160]

    return {
        "status": "ok",
        "item_key": key,
        "note_key": note_key,
        "paper_type": paper_type,
        "sections": len(sections),
        "short_note": short_note_status,
        "short_note_error": short_note_error,
    }
