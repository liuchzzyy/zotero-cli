from __future__ import annotations

import json
import sys

import click

from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_ai_note_config,
    load_config,
    load_pdf_config,
    resolve_library_id,
    resolve_write_credentials,
)
from zotero_cli.core.ai_client import AiClient
from zotero_cli.core.note_analysis import NoteAnalysisError, analyze_item
from zotero_cli.core.reader import ZoteroReader
from zotero_cli.core.writer import ZoteroWriter
from zotero_cli.exit_codes import emit_error
from zotero_cli.formatter import envelope_ok


@click.command("ai_analyze")
@click.argument("key")
@click.option("--dry-run", is_flag=True, help="只输出分类结果与将发给 AI 的输入，不调用 AI 分析、不写 note")
@click.option("--force", is_flag=True, help="已打 ai/noted 也重跑")
@click.option("--extractor", default=None, help="PDF 文本抽取器，默认使用 [pdf].extractor（MinerU）")
@click.option("--no-tag", is_flag=True, help="写 note 但不打 ai/noted tag")
@click.option("--no-short-note", is_flag=True, help="不生成关键词（简记 short-note），只生成 note")
@click.option(
    "--short-note-only",
    is_flag=True,
    help="只根据已有 AI note 生成关键词并写入 Extra short-note（回填模式，不重新生成 note）",
)
@click.option("--progress-lines", is_flag=True, help="按行输出进度（适合日志）")
@click.pass_context
def ai_analyze_cmd(
    ctx: click.Context,
    key: str,
    dry_run: bool,
    force: bool,
    extractor: str | None,
    no_tag: bool,
    no_short_note: bool,
    short_note_only: bool,
    progress_lines: bool,
) -> None:
    """用 MinerU + AI 分析一个 Zotero 条目及其 PDF，生成 HTML note 并写回。"""
    json_out = ctx.obj.get("json", False)

    if extractor is None:
        extractor = load_pdf_config().extractor

    ai_cfg = load_ai_note_config()
    if not (ai_cfg.api_key and ai_cfg.base_url and ai_cfg.model):
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
    if not writer_library_id or not writer_api_key:
        reader.close()
        emit_error(
            "auth_missing",
            "写入凭证未配置",
            output_json=json_out,
            hint="设置 ZOT_LIBRARY_ID / ZOT_API_KEY 或运行 zot config init",
            context="ai_analyze",
        )

    writer = ZoteroWriter(
        library_id=writer_library_id,
        api_key=writer_api_key,
        library_type=ctx.obj.get("library_type", "user"),
    )
    ai_client = AiClient(ai_cfg)

    def progress(event: str, message: str) -> None:
        if json_out:
            return
        if progress_lines:
            click.echo(f"  [{event}] {message}", err=True)
        else:
            sys.stderr.write(f"\r  [{event}] {message}\r")
            sys.stderr.flush()

    try:
        result = analyze_item(
            reader,
            writer,
            ai_client,
            key,
            force=force,
            no_tag=no_tag,
            extractor=extractor,
            dry_run=dry_run,
            no_short_note=no_short_note,
            short_note_only=short_note_only,
            progress=progress,
        )
    except NoteAnalysisError as exc:
        reader.close()
        emit_error(exc.code, str(exc), output_json=json_out, retryable=exc.retryable, context="ai_analyze")
    finally:
        reader.close()

    if not json_out and not progress_lines:
        sys.stderr.write(f"\r{' ' * 60}\r")
        sys.stderr.flush()

    if json_out:
        click.echo(json.dumps(envelope_ok(result), indent=2, ensure_ascii=False))
    else:
        _print_human(result)


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
        click.echo(
            f"  简记（short-note）：失败，tag ai/no_keywords"
            f"（{result.get('short_note_error', '')}）"
        )
    else:
        click.echo("  简记（short-note）：跳过")
