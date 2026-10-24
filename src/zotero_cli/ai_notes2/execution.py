"""编排一次 ai-note2：先检查旧 AI 笔记，再冻结输入、生成、渲染与发布。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.generation import budget_summary, generate_document
from zotero_cli.ai_notes2.inputs import InputSnapshot, collect_inputs
from zotero_cli.ai_notes2.persistence import (
    STATUS_GENERATED,
    STATUS_VALIDATED,
    latest_run,
    publish_document,
    reconcile_run,
    reserve_run,
    scope_id,
    update_run,
)
from zotero_cli.ai_notes2.rendering import render_html, validate_before_render, write_artifacts
from zotero_cli.ai_notes2.verification import verify_document
from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_ai_note_config,
    load_config,
    resolve_library_id,
    resolve_write_credentials,
    state_dir,
)
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import ZoteroWriter

ProgressCallback = Callable[[str, str], None]


@dataclass
class Execution:
    """命令层呈现所需的结果；不包含凭据。"""

    data: dict[str, Any]
    replayed: bool = False
    ledger: dict[str, Any] = field(default_factory=dict)


def _snapshot_digest(snapshot: InputSnapshot) -> str:
    payload = {
        "note": snapshot.source_note.digest,
        "documents": snapshot.documents,
        "blocks": snapshot.source_blocks,
    }
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _document_title(snapshot: InputSnapshot) -> str:
    title = " ".join((snapshot.item.title or "").split())
    return "框架分析 - " + title if title else "框架分析"


def _resume_publication(
    *,
    config: Any,
    scope: str,
    key: str,
    library_type: str,
    group_id: str | None,
    progress: ProgressCallback | None,
) -> Execution:
    """对账恢复：确认已知 note 后只补未完成步骤，绝不重新生成或创建 note。"""
    latest = latest_run(scope)
    if latest is None or not latest.get("committed"):
        raise AiNote2Error("该条目没有可恢复的发布记录", code="not_found", hint_key="reconcile_missing")
    if latest["status"] == "verified":
        return Execution(
            data={
                "status": "already_verified",
                "item_key": key,
                "note_key": latest["note_key"],
                "run_number": latest["run_number"],
            }
        )
    writer_library_id, writer_api_key = resolve_write_credentials(config, library_type=library_type, group_id=group_id)
    if not writer_library_id or not writer_api_key:
        raise AiNote2Error("写入凭证未配置", code="auth_missing", hint_key="write_credentials_missing")
    if progress:
        progress("reconcile", f"核对运行 #{latest['run_number']} 的远端 note，只补未完成步骤")
    writer = ZoteroWriter(library_id=writer_library_id, api_key=writer_api_key, library_type=library_type)
    published = reconcile_run(
        writer,
        scope=scope,
        run_number=int(latest["run_number"]),
        item_key=key,
    )
    return Execution(
        data={
            "status": "ok",
            "item_key": key,
            "note_key": published.note_key,
            "run_number": int(latest["run_number"]),
            "tag": "ai/evidence",
            "tagged": published.tagged,
            "verified": published.verified,
            "needs_reconcile": published.needs_reconcile,
            "resumed": True,
        }
    )


def execute_ai_note2(
    *,
    key: str,
    profile: str | None,
    library_type: str,
    group_id: str | None,
    source_note: str | None = None,
    attachments: list[str] | None = None,
    dry_run: bool = False,
    local_only: bool = False,
    output_dir: str | None = None,
    force: bool = False,
    reconcile: bool = False,
    idempotency_key: str | None = None,
    progress: ProgressCallback | None = None,
) -> Execution:
    """执行单篇三链笔记；无可用旧 AI 笔记时正常跳过。"""
    config = load_config(profile=profile)
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, {"library_type": library_type, "group_id": group_id})
    scope = scope_id(library_type, str(library_id), key)

    if reconcile:
        return _resume_publication(
            config=config,
            scope=scope,
            key=key,
            library_type=library_type,
            group_id=group_id,
            progress=progress,
        )

    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(config))
    ai_config = load_ai_note_config()
    try:
        try:
            snapshot = collect_inputs(
                reader,
                key,
                library_type=library_type,
                group_id=group_id,
                source_note_key=source_note,
                attachment_keys=attachments or [],
                max_extracted_chars=ai_config.max_extracted_chars,
                progress=progress,
            )
        except AiNote2Error as exc:
            if exc.hint_key == "no_ai_note":
                # 正常跳过：不解析 PDF、不建模型客户端、不写 Zotero。
                return Execution(
                    data={
                        "status": "skipped",
                        "reason": "no_ai_note",
                        "item_key": key,
                        "message": "该条目没有可识别的旧 AI 分析笔记；未解析 PDF、未调用模型、未写入 Zotero",
                    }
                )
            raise
    finally:
        reader.close()

    scope = scope_id(library_type, str(library_id), key)
    digest = _snapshot_digest(snapshot)
    ai_configured = bool(ai_config.api_key and ai_config.base_url and ai_config.model)

    if dry_run:
        model = ai_config.model or "unknown"
        return Execution(
            data={
                "status": "dry_run",
                "item_key": key,
                "source_note_key": snapshot.source_note.key,
                "documents": [
                    {
                        "attachment_key": doc["attachment_key"],
                        "aliases": doc["attachment_aliases"],
                        "pdf_role": doc["pdf_role"],
                        "identity_status": doc["document_identity_status"],
                        "role_basis": doc["role_basis"],
                    }
                    for doc in snapshot.documents
                ],
                "blocks": len(snapshot.source_blocks),
                "characters": snapshot.character_count,
                "template": "evidence-chain",
                "steps": ["生成三链结构", "逐条核对原文可定位性"],
                "budget": budget_summary(snapshot, model, ai_config.max_tokens),
                "cache_note": "dry-run 不写 Zotero；缓存缺失时仍可能调用 MinerU 并写解析缓存",
            }
        )

    if not ai_configured:
        raise AiNote2Error("AI note 配置不完整", code="configuration_error", hint_key="ai_configuration_missing")

    if not local_only:
        writer_library_id, writer_api_key = resolve_write_credentials(
            config, library_type=library_type, group_id=group_id
        )
        if not writer_library_id or not writer_api_key:
            raise AiNote2Error("写入凭证未配置", code="auth_missing", hint_key="write_credentials_missing")

    reservation = reserve_run(
        scope,
        input_digest=digest,
        idempotency_key=None if local_only else idempotency_key,
        force=force,
    )
    if reservation.replayed is not None:
        return Execution(data=reservation.replayed, replayed=True)
    run_number = reservation.run_number

    try:
        client = AiClient(ai_config)
        generated = generate_document(client, snapshot, run_number=run_number, progress=progress)
        update_run(scope, run_number, status=STATUS_GENERATED, data={"model": generated.model})
        document = validate_before_render(generated.document)
        report = verify_document(document, snapshot, client=client, progress=progress)
        update_run(
            scope,
            run_number,
            status=STATUS_VALIDATED,
            data={"objects": len(document.to_dict()["object_registry"]), "verification": report.to_dict()["counts"]},
        )
    except Exception as exc:
        if isinstance(exc, AiNote2Error) and exc.hint_key == "ledger_failed":
            raise
        update_run(scope, run_number, status="failed", committed=False, data={"error": type(exc).__name__})
        if isinstance(exc, AiNote2Error):
            raise
        raise AiNote2Error(f"生成或核对失败：{type(exc).__name__}: {str(exc)[:200]}", code="runtime_error") from exc

    target_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir
        else state_dir() / "ai_note2" / str(library_id) / key / f"{run_number:04d}"
    )
    try:
        if output_dir and target_dir.exists() and any(target_dir.iterdir()):
            raise AiNote2Error(
                "输出目录已存在非本次文件，拒绝覆盖", code="validation_error", hint_key="output_dir_exists"
            )
    except AiNote2Error:
        update_run(scope, run_number, status="failed", committed=False, data={"error": "output_dir_exists"})
        raise
    manifest = {
        "schema_version": "ai-note2-run/1",
        "item_key": key,
        "run_number": run_number,
        "scope": scope,
        "source_note_key": snapshot.source_note.key,
        "source_note_digest": snapshot.source_note.digest,
        "input_digest": digest,
        "documents": snapshot.documents,
        "model": generated.model,
        "usage": generated.usage,
        "repair_used": generated.repair_used,
        "object_count": len(document.to_dict()["object_registry"]),
        "prompt_version": "ai-note2/1",
        "verification": report.to_dict(),
    }
    title = _document_title(snapshot)
    verification = report.to_dict()
    try:
        digests = write_artifacts(target_dir, document, title=title, manifest=manifest, verification=verification)
    except Exception as exc:
        update_run(scope, run_number, status="failed", committed=False, data={"error": type(exc).__name__})
        raise

    result: dict[str, Any] = {
        "status": "local_only" if local_only else "ok",
        "item_key": key,
        "run_number": run_number,
        "object_count": manifest["object_count"],
        "model": generated.model,
        "repair_used": generated.repair_used,
        "output_dir": str(target_dir),
        "files": digests,
        "source_note_key": snapshot.source_note.key,
        "verification": report.to_dict()["counts"],
        "unsupported_objects": [item["object_id"] for item in report.unsupported],
    }

    if local_only:
        update_run(scope, run_number, status="local_only", data={"result": result})
        return Execution(data=result)

    writer = ZoteroWriter(library_id=writer_library_id, api_key=writer_api_key, library_type=library_type)
    html_note = render_html(document, title=title, verification=verification)
    try:
        published = publish_document(writer, scope=scope, run_number=run_number, item_key=key, html_note=html_note)
    except AiNote2Error:
        raise
    result.update(
        status="ok",
        note_key=published.note_key,
        tag="ai/evidence",
        tagged=published.tagged,
        verified=published.verified,
        needs_reconcile=published.needs_reconcile,
    )
    update_run(scope, run_number, data={"result": result})
    return Execution(data=result, ledger={"scope": scope, "run_number": run_number})
