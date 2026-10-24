"""`zot ai-note2`：基于旧 AI 笔记与 MinerU 文本生成三链框架分析笔记。"""

from __future__ import annotations

import json
from typing import Any

import typer

from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.ai_notes2.execution import execute_ai_note2
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error

_HINTS = {
    "no_ai_note": "先运行 zot ai-note 生成 AI 分析笔记，再用 --source-note 指定",
    "source_note_invalid": "确认 note key 属于该父条目且不是本功能输出",
    "source_note_ambiguous": "用 --source-note 明确指定同父条目的旧 AI 分析笔记",
    "not_parent_item": "传入父条目 key，而不是 note 或 PDF 附件 key",
    "pdf_missing": "补齐或同步该条目的本地 PDF 附件后重试",
    "attachment_invalid": "--attachment 只能引用该父条目下的 PDF 附件",
    "parse_failed": "检查 MinerU 缓存与 ZOT_MINERU_TOKEN",
    "input_too_large": "缩小 --attachment 范围或提高 max_extracted_chars；不会静默截断",
    "ai_configuration_missing": "在 .zot/config.toml 配置 [ai_notes] 的 api_key/base_url/model",
    "write_credentials_missing": "设置 ZOT_LIBRARY_ID / ZOT_API_KEY，或使用 --local-only",
    "pending_mutation": "先核对远端 note 与 ai/evidence 标签；不要用新键绕过",
    "publish_unknown": "先回读该父条目的远端 notes；不要重复创建 note",
    "publish_mismatch": "保留已返回的 note key，人工核对远端内容",
    "publish_rejected": "服务端明确拒绝，未写入；修复原因后可重试",
    "tag_unknown": "note 已创建；恢复时只补 ai/evidence 标签，不重复创建 note",
    "ledger_failed": "远端可能已写入；先核对远端，再修复本地账本",
    "output_dir_exists": "使用新的 --output-dir，或删除本次之外的旧产物",
    "generation_invalid": "结构修复后仍无效；检查模型输出或缩小输入范围",
    "reservation_failed": "检查状态目录与账本数据库权限",
    "idempotency_conflict": "同一幂等键对应不同输入；使用新的键",
    "reconcile_missing": "该条目没有已创建 note 的记录；先正常运行 ai-note2",
}


def ai_note2(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 父条目键；不能是 note 或 PDF 附件。"),
    source_note: str | None = typer.Option(
        None, "--source-note", help="指定同父条目的旧 AI 分析笔记 key；未指定时仅在唯一一份可识别笔记时自动选用。"
    ),
    attachment: list[str] = typer.Option(
        None, "--attachment", help="限定 PDF 附件 key，可重复；未指定时清点该父条目的本地 PDF。"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="不调用分析模型、不写 Zotero；预览所选笔记、附件、输入范围与预算。缓存缺失时仍可能调用 MinerU 并写解析缓存。",
    ),
    local_only: bool = typer.Option(
        False, "--local-only", help="调用模型生成 JSON、Markdown、HTML 和 SVG，但不创建 writer、不改 Zotero。"
    ),
    output_dir: str | None = typer.Option(None, "--output-dir", help="本地输出目录；拒绝非空目录。"),
    force: bool = typer.Option(False, "--force", help="已有发布记录时新建一次运行并追加新 note；不覆盖或删除旧 note。"),
    reconcile: bool = typer.Option(
        False, "--reconcile", help="核对已创建的 note，只补未完成的标签/回读步骤；不重新生成或创建 note。"
    ),
    idempotency_key: str | None = typer.Option(
        None, "--idempotency-key", help="同一范围与输入的成功结果可重放；进行中或结果未知时拒绝。"
    ),
    progress_lines: bool = typer.Option(False, "--progress-lines", help="按行输出进度到 stderr。"),
) -> None:
    """先检查旧 AI 分析笔记，再单阶段生成三链笔记与鱼骨图，并发布到 Zotero。"""
    json_out = ctx.obj.get("json", False)

    def progress(event: str, message: str) -> None:
        if json_out:
            return
        line = f"  [{event}] {message}"
        if progress_lines:
            typer.echo(line, err=True)

    try:
        execution = execute_ai_note2(
            key=key,
            profile=ctx.obj.get("profile"),
            library_type=ctx.obj.get("library_type", "user"),
            group_id=ctx.obj.get("group_id"),
            source_note=source_note,
            attachments=list(attachment or []),
            dry_run=dry_run,
            local_only=local_only,
            output_dir=output_dir,
            force=force,
            reconcile=reconcile,
            idempotency_key=idempotency_key,
            progress=progress,
        )
        envelope = execution.cached_envelope or envelope_ok(execution.data)
    except NoteAnalysisError as exc:
        committed = bool(getattr(exc, "committed", False))
        emit_error(
            exc.code,
            str(exc),
            output_json=json_out,
            retryable=exc.retryable and not committed,
            hint=_HINTS.get(getattr(exc, "hint_key", ""), ""),
            context="ai-note2",
            committed=committed,
        )

    if json_out:
        typer.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
    else:
        _print_human(execution.data)


def _print_human(result: dict[str, Any]) -> None:
    status = result.get("status")
    key = result.get("item_key", "")
    if status == "skipped":
        typer.echo(f"条目 {key} 没有可识别的旧 AI 分析笔记，已跳过；未解析 PDF、未调用模型、未写入 Zotero。")
    elif status == "dry_run":
        typer.echo(f"[dry-run] 条目 {key}")
        typer.echo(f"  旧笔记：{result.get('source_note_key')}")
        for doc in result.get("documents", []):
            typer.echo(f"  PDF {doc['attachment_key']}：{doc['pdf_role']}（{doc['role_basis']}）")
        typer.echo(f"  来源块：{result.get('blocks')}，字符：{result.get('characters')}")
        budget = result.get("budget", {})
        typer.echo(f"  预算：payload {budget.get('projection_bytes')} 字节，可用={budget.get('within_budget')}")
        typer.echo(f"  说明：{result.get('cache_note')}")
    elif status in {"ok", "local_only"}:
        typer.echo(
            f"条目 {key} 运行 #{result.get('run_number')} 完成：{result.get('object_count')} 个对象，"
            f"模型 {result.get('model')}{'（含结构修复）' if result.get('repair_used') else ''}"
        )
        typer.echo(f"  本地产物：{result.get('output_dir')}")
        if status == "ok":
            typer.echo(f"  note：{result.get('note_key')}；标签 ai/evidence 已回读确认")
        else:
            typer.echo("  未写入 Zotero（--local-only）")
    else:
        typer.echo(f"完成：{result}")
