"""编排一次 AI 笔记执行，不依赖 CLI 输出层。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.analysis import analyze_item
from zotero_cli.ai_notes.errors import AiNoteExecutionError, NoteAnalysisError
from zotero_cli.ai_notes.models import AiNoteExecution
from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_ai_note_config,
    load_config,
    resolve_library_id,
    resolve_write_credentials,
)
from zotero_cli.zotero.access.idempotency import (
    PendingMutationError,
    begin_mutation,
    release_rejected_mutation,
    request_scope,
    store_cached,
)
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import ZoteroWriter

ProgressCallback = Callable[[str, str], None]


def execute_ai_note(
    *,
    key: str,
    profile: str | None,
    library_type: str,
    group_id: str | None,
    dry_run: bool = False,
    force: bool = False,
    no_tag: bool = False,
    no_short_note: bool = False,
    short_note_only: bool = False,
    idempotency_key: str | None = None,
    progress: ProgressCallback | None = None,
) -> AiNoteExecution:
    """执行 AI 笔记分析，并返回供任意调用方呈现的结构化结果。"""
    ai_config = load_ai_note_config()
    ai_configured = bool(ai_config.api_key and ai_config.base_url and ai_config.model)
    if not dry_run and not ai_configured:
        raise AiNoteExecutionError(
            "AI note 配置不完整",
            code="configuration_error",
            hint_key="ai_configuration_missing",
        )

    config = load_config(profile=profile)
    writer_library_id, writer_api_key = resolve_write_credentials(
        config,
        library_type=library_type,
        group_id=group_id,
    )
    if not dry_run and (not writer_library_id or not writer_api_key):
        raise AiNoteExecutionError(
            "写入凭证未配置",
            code="auth_missing",
            hint_key="write_credentials_missing",
        )

    cache_scope: str | None = None
    cache_key = idempotency_key or ""
    if not dry_run and idempotency_key:
        cache_scope = request_scope(
            "ai-note",
            library_type,
            str(writer_library_id),
            {
                "key": key,
                "force": force,
                "no_tag": no_tag,
                "no_short_note": no_short_note,
                "short_note_only": short_note_only,
            },
        )
        try:
            cached = begin_mutation(cache_scope, cache_key)
        except PendingMutationError as exc:
            raise AiNoteExecutionError(
                str(exc),
                code="conflict",
                hint_key="pending_mutation",
            ) from exc
        except (OSError, sqlite3.Error) as exc:
            raise AiNoteExecutionError(
                "Could not durably reserve the analysis; no write attempted",
                code="configuration_error",
                hint_key="reservation_failed",
            ) from exc
        if cached is not None:
            data = cached.get("data")
            if not isinstance(data, dict):
                raise AiNoteExecutionError("缓存的分析结果无效", code="runtime_error")
            return AiNoteExecution(data=data, replayed=True, cached_envelope=cached)

    reader: ZoteroReader | None = None
    try:
        data_dir = get_data_dir(config)
        db_path = data_dir / "zotero.sqlite"
        library_id = resolve_library_id(
            db_path,
            {"library_type": library_type, "group_id": group_id},
        )
        reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(config))
        writer = (
            ZoteroWriter(library_id=writer_library_id, api_key=writer_api_key, library_type=library_type)
            if not dry_run
            else None
        )
        ai_client = AiClient(ai_config) if ai_configured else None
        data = analyze_item(
            reader,
            writer,
            ai_client,
            key,
            force=force,
            no_tag=no_tag,
            dry_run=dry_run,
            no_short_note=no_short_note,
            short_note_only=short_note_only,
            progress=progress,
            max_extracted_chars=ai_config.max_extracted_chars,
        )
    except NoteAnalysisError as exc:
        committed = bool(getattr(exc, "committed", False))
        if cache_scope is not None and not committed:
            try:
                release_rejected_mutation(cache_scope, cache_key)
            except (OSError, sqlite3.Error):
                pass
        raise
    finally:
        if reader is not None:
            reader.close()

    return AiNoteExecution(data=data, cache_scope=cache_scope, idempotency_key=cache_key)


def store_execution_result(result: AiNoteExecution, envelope: dict[str, Any]) -> None:
    """持久化 CLI 构造的成功 envelope，并解除对应预占。"""
    if result.cache_scope is None:
        return
    try:
        store_cached(result.cache_scope, result.idempotency_key, envelope)
    except (OSError, sqlite3.Error) as exc:
        raise AiNoteExecutionError(
            "分析已完成，但重试结果无法保存",
            code="configuration_error",
            hint_key="cache_persist_failed",
        ) from exc
