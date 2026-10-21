"""zot ai-analyze: parse an item's PDFs and generate an AI analysis note."""

from __future__ import annotations

import json
import sqlite3
import sys

import click

from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_ai_note_config,
    load_config,
    resolve_library_id,
    resolve_write_credentials,
)
from zotero_cli.core.notes.client import AiClient
from zotero_cli.core.notes.errors import NoteAnalysisError
from zotero_cli.core.notes.pipeline import analyze_item
from zotero_cli.core.zotero.idempotency import (
    PendingMutationError,
    begin_mutation,
    release_rejected_mutation,
    request_scope,
    store_cached,
)
from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.zotero.reader import ZoteroReader


@click.command("ai_analyze")
@click.argument("key")
@click.option(
    "--dry-run",
    is_flag=True,
    help="只输出分类结果与将发给 AI 的输入，不调用 AI 分析、不写 note（无需 AI 配置）",
)
@click.option("--force", is_flag=True, help="已打 ai/noted 也重跑")
@click.option("--no-tag", is_flag=True, help="写 note 但不打 ai/noted tag")
@click.option("--no-short-note", is_flag=True, help="不生成关键词（简记 short-note），只生成 note")
@click.option(
    "--short-note-only",
    is_flag=True,
    help="只根据已有 AI note 生成关键词并写入 Extra short-note（回填模式，不重新生成 note）",
)
@click.option("--progress-lines", is_flag=True, help="按行输出进度（适合日志）")
@click.option(
    "--idempotency-key",
    default=None,
    help="Replay confirmed success; in-flight or unknown outcomes require reconciliation",
)
@click.pass_context
def ai_analyze_cmd(
    ctx: click.Context,
    key: str,
    dry_run: bool,
    force: bool,
    no_tag: bool,
    no_short_note: bool,
    short_note_only: bool,
    progress_lines: bool,
    idempotency_key: str | None,
) -> None:
    """用 MinerU + AI 分析一个 Zotero 条目及其 PDF，生成 HTML note 并写回。

    \b
    Examples:
      zot ai_analyze ABC123 --dry-run               Preview without AI config
      zot ai_analyze ABC123                         Analyze and write a note
      zot ai_analyze ABC123 --idempotency-key k1    Retry-safe write
    """
    json_out = ctx.obj.get("json", False)

    ai_cfg = load_ai_note_config()
    ai_configured = bool(ai_cfg.api_key and ai_cfg.base_url and ai_cfg.model)
    if not dry_run and not ai_configured:
        emit_error(
            "configuration_error",
            "AI note 配置不完整",
            output_json=json_out,
            hint="在 .zot/config.toml 配置 [ai_notes] 的 api_key/base_url/model",
            context="ai_analyze",
        )

    cfg = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(cfg))

    writer_library_id, writer_api_key = resolve_write_credentials(
        cfg,
        library_type=ctx.obj.get("library_type", "user"),
        group_id=ctx.obj.get("group_id"),
    )
    if not dry_run and (not writer_library_id or not writer_api_key):
        reader.close()
        emit_error(
            "auth_missing",
            "写入凭证未配置",
            output_json=json_out,
            hint="设置 ZOT_LIBRARY_ID / ZOT_API_KEY 或运行 zot config init",
            context="ai_analyze",
        )

    writer = None
    if writer_library_id and writer_api_key:
        writer = ZoteroWriter(
            library_id=writer_library_id,
            api_key=writer_api_key,
            library_type=ctx.obj.get("library_type", "user"),
        )
    ai_client = AiClient(ai_cfg) if ai_configured else None

    cache_scope: str | None = None
    idem_key = idempotency_key or ""
    if not dry_run and idempotency_key:
        cache_scope = request_scope(
            "ai_analyze",
            ctx.obj.get("library_type", "user"),
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
            cached = begin_mutation(cache_scope, idem_key)
        except PendingMutationError as exc:
            reader.close()
            emit_error(
                "conflict",
                str(exc),
                output_json=json_out,
                retryable=False,
                hint="Reconcile the item's notes before proceeding; do not bypass this with a new key",
                context="ai_analyze",
            )
        except (OSError, sqlite3.Error):
            reader.close()
            emit_error(
                "configuration_error",
                "Could not durably reserve the analysis; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="ai_analyze",
            )
        if cached is not None:
            reader.close()
            if json_out:
                click.echo(json.dumps(cached, indent=2, ensure_ascii=False))
            else:
                click.echo(f"条目 {key} 已有成功结果（cached）：{cached.get('data', {}).get('status', '?')}")
            return

    def progress(event: str, message: str) -> None:
        if json_out:
            return
        line = f"  [{event}] {message}"
        if progress_lines or not _stderr_is_tty():
            click.echo(line, err=True)
        else:
            sys.stderr.write(f"\r{line}\r")
            sys.stderr.flush()

    try:
        result = analyze_item(
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
            max_extracted_chars=ai_cfg.max_extracted_chars,
        )
    except NoteAnalysisError as exc:
        committed = bool(getattr(exc, "committed", False))
        if cache_scope is not None and not committed:
            try:
                release_rejected_mutation(cache_scope, idem_key)
            except (OSError, sqlite3.Error):
                pass  # Preserve the reservation if its durable release failed.
        emit_error(
            exc.code,
            str(exc),
            output_json=json_out,
            retryable=exc.retryable and not committed,
            context="ai_analyze",
        )
    finally:
        reader.close()

    if not json_out and not progress_lines and _stderr_is_tty():
        sys.stderr.write(f"\r{' ' * 60}\r")
        sys.stderr.flush()

    env = envelope_ok(result)
    if cache_scope is not None:
        try:
            store_cached(cache_scope, idem_key, env)
        except (OSError, sqlite3.Error):
            emit_error(
                "configuration_error",
                "分析已完成，但重试结果无法保存",
                output_json=json_out,
                retryable=False,
                hint="Reconcile this item before retrying; the pending reservation still blocks blind retries",
                context="ai_analyze",
            )
    if json_out:
        click.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        _print_human(result)


def _stderr_is_tty() -> bool:
    try:
        return bool(sys.stderr.isatty())
    except (AttributeError, ValueError):
        return False


def _print_human(result: dict) -> None:
    status = result.get("status")
    key = result.get("item_key", "")
    if status == "already_analyzed":
        click.echo(f"条目 {key} 已分析过（tag ai/noted）。用 --force 重跑。")
    elif status == "uncertain":
        click.echo(f"条目 {key} 类型无法判定，已打 tag ai/uncertain 并跳过。")
    elif status == "dry_run":
        click.echo(f"[dry-run] 条目 {key}")
        click.echo(f"  类型：{result.get('paper_type')}")
        click.echo(f"  模板：{result.get('template')}")
        click.echo(f"  PDF：{result.get('pdf_count')} 个，正文 {result.get('chars')} 字符")
        click.echo("  --- 将发给 AI 的输入预览 ---")
        click.echo(result.get("prompt_preview", ""))
    elif status == "ok":
        click.echo(
            f"已生成 note {result.get('note_key')}：类型 {result.get('paper_type')}，"
            f"{result.get('sections')} 个 section"
        )
        _print_short_note(result)
    elif status == "short_note_only":
        click.echo(f"已基于 note {result.get('note_key')} 生成简记")
        _print_short_note(result)
    else:
        click.echo(f"完成：{result}")


def _print_short_note(result: dict) -> None:
    status = result.get("short_note", "skipped")
    if status == "ok":
        click.echo("  简记（short-note）：已写入 Extra，tag ai/keywords")
    elif status == "failed":
        click.echo(f"  简记（short-note）：失败，tag ai/no_keywords（{result.get('short_note_error', '')}）")
    else:
        click.echo("  简记（short-note）：跳过")
