"""`zot ai-note`：解析条目 PDF 并生成 AI 分析笔记。"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer

from zotero_cli.ai_notes.errors import AiNoteExecutionError, NoteAnalysisError
from zotero_cli.ai_notes.execution import execute_ai_note, store_execution_result
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error

_HINTS = {
    "ai_configuration_missing": "在 .zot/config.toml 配置 [ai_notes] 的 api_key/base_url/model",
    "write_credentials_missing": "设置 ZOT_LIBRARY_ID / ZOT_API_KEY 或运行 zot config init",
    "pending_mutation": "Reconcile the item's notes before proceeding; do not bypass this with a new key",
    "reservation_failed": "Check the idempotency state directory and database",
    "cache_persist_failed": "Reconcile this item before retrying; the pending reservation still blocks blind retries",
}


def ai_note(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 父条目键。"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="只输出分类结果与将发给 AI 的输入，不调用 AI 分析、不写 note（无需 AI 配置）。"
    ),
    force: bool = typer.Option(False, "--force", help="已打 ai/noted 也重跑。"),
    no_tag: bool = typer.Option(False, "--no-tag", help="写 note 但不打 ai/noted tag。"),
    no_short_note: bool = typer.Option(False, "--no-short-note", help="不生成关键词简记，只生成 note。"),
    short_note_only: bool = typer.Option(False, "--short-note-only", help="只根据已有 AI note 回填 Extra short-note。"),
    progress_lines: bool = typer.Option(False, "--progress-lines", help="按行输出进度，适合日志。"),
    idempotency_key: str | None = typer.Option(
        None, "--idempotency-key", help="重放已确认成功；进行中或未知结果必须先对账。"
    ),
) -> None:
    """用 MinerU 和 AI 分析条目 PDF，生成 HTML note 并通过 Web API 写回。"""
    json_out = ctx.obj.get("json", False)

    def progress(event: str, message: str) -> None:
        if json_out:
            return
        line = f"  [{event}] {message}"
        if progress_lines or not _stderr_is_tty():
            typer.echo(line, err=True)
        else:
            sys.stderr.write(f"\r{line}\r")
            sys.stderr.flush()

    try:
        execution = execute_ai_note(
            key=key,
            profile=ctx.obj.get("profile"),
            library_type=ctx.obj.get("library_type", "user"),
            group_id=ctx.obj.get("group_id"),
            dry_run=dry_run,
            force=force,
            no_tag=no_tag,
            no_short_note=no_short_note,
            short_note_only=short_note_only,
            idempotency_key=idempotency_key,
            progress=progress,
        )
        if execution.replayed:
            assert execution.cached_envelope is not None
            envelope = execution.cached_envelope
        else:
            envelope = envelope_ok(execution.data)
            store_execution_result(execution, envelope)
    except (AiNoteExecutionError, NoteAnalysisError) as exc:
        committed = bool(getattr(exc, "committed", False))
        emit_error(
            exc.code,
            str(exc),
            output_json=json_out,
            retryable=exc.retryable and not committed,
            hint=_HINTS.get(getattr(exc, "hint_key", ""), ""),
            context="ai-note",
            committed=committed,
        )

    if not json_out and not progress_lines and _stderr_is_tty():
        sys.stderr.write(f"\r{' ' * 60}\r")
        sys.stderr.flush()

    if json_out:
        typer.echo(json.dumps(envelope, indent=2, ensure_ascii=False))
    elif execution.replayed:
        typer.echo(f"条目 {key} 已有成功结果（cached）：{execution.data.get('status', '?')}")
    else:
        _print_human(execution.data)


def _stderr_is_tty() -> bool:
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def _print_human(result: dict[str, Any]) -> None:
    status = result.get("status")
    key = result.get("item_key", "")
    if status == "already_analyzed":
        typer.echo(f"条目 {key} 已分析过（tag ai/noted）。用 --force 重跑。")
    elif status == "uncertain":
        typer.echo(f"条目 {key} 类型无法判定，已打 tag ai/uncertain 并跳过。")
    elif status == "dry_run":
        typer.echo(f"[dry-run] 条目 {key}")
        typer.echo(f"  类型：{result.get('paper_type')}")
        typer.echo(f"  模板：{result.get('template')}")
        typer.echo(f"  PDF：{result.get('pdf_count')} 个，正文 {result.get('chars')} 字符")
        if result.get("visual_counts_are_estimates"):
            typer.echo(
                f"  视觉预检估算：正文 {result.get('main_images')}，SI {result.get('si_images')}/{result.get('si_candidates')} 张（已为文字证据包预留；实际可能不同）"
            )
        typer.echo("  --- 将发给 AI 的输入预览 ---")
        typer.echo(result.get("prompt_preview", ""))
    elif status == "ok":
        typer.echo(
            f"已生成 note {result.get('note_key')}：类型 {result.get('paper_type')}，"
            f"{result.get('sections')} 个 section"
        )
        if "main_images_sent" in result:
            typer.echo(
                f"  实际附图：正文 {result['main_images_sent']}，SI {result['si_images_sent']}/{result['si_candidates']} 张"
            )
        _print_short_note(result)
    elif status == "short_note_only":
        typer.echo(f"已基于 note {result.get('note_key')} 生成简记")
        _print_short_note(result)
    else:
        typer.echo(f"完成：{result}")


def _print_short_note(result: dict) -> None:
    status = result.get("short_note", "skipped")
    if status == "ok":
        typer.echo("  简记（short-note）：已写入 Extra，tag ai/keywords")
    elif status == "failed":
        typer.echo(f"  简记（short-note）：失败，tag ai/no_keywords（{result.get('short_note_error', '')}）")
    else:
        typer.echo("  简记（short-note）：跳过")
