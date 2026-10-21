"""pdf_rag 命令共用的 envelope、dry-run 与 pdf_rag 加载辅助函数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.pdf_rag.store import PdfRag, load_pdf_rag, pdf_rag_dir, pdf_rag_exists

_LIST_HINT = "Use 'zot pdf-rag list' to see available pdf_rags"


def output_json(ctx: typer.Context) -> bool:
    return bool(ctx.obj.get("json", False))


def emit_result(
    ctx: typer.Context,
    data: Any,
    *,
    human_text: str,
    meta: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> None:
    """只输出一个结果文档：JSON envelope 或人类可读文本。"""
    if output_json(ctx):
        typer.echo(json.dumps(envelope_ok(data, meta=meta, dry_run=dry_run), indent=2, ensure_ascii=False))
    else:
        typer.echo(human_text)


def emit_warning(message: str) -> None:
    """警告始终写入 stderr，使 JSON 模式下 stdout 只保留一个 envelope。"""
    typer.echo(f"Warning: {message}", err=True)


def emit_progress(ctx: typer.Context, message: str) -> None:
    """JSON 模式下进度写入 stderr，使 stdout 只保留一个 envelope。"""
    if output_json(ctx):
        typer.echo(message, err=True)
    else:
        typer.echo(message)


def pdf_rag_directory_or_exit(ctx: typer.Context, name: str, *, context: str, hint: str = "") -> Path:
    """解析 pdf_rag 目录；名称非法或不存在时输出带类型的错误。"""
    json_out = output_json(ctx)
    try:
        directory = pdf_rag_dir(name)
    except ValueError as exc:
        emit_error("validation_error", str(exc), output_json=json_out, context=context)
    except OSError as exc:
        emit_error(
            "validation_error",
            f"Invalid pdf_rag name: {exc}",
            output_json=json_out,
            hint="PdfRag names must be a single directory component under .zot/pdf_rag/",
            context=context,
        )
    if not directory.exists():
        emit_error("not_found", f"PdfRag '{name}' not found", output_json=json_out, hint=_LIST_HINT, context=context)
    return directory


def load_pdf_rag_or_exit(ctx: typer.Context, name: str, *, context: str, hint: str = "") -> PdfRag:
    """加载 pdf_rag，将非法、缺失或损坏的 manifest 转换为带类型的错误。"""
    json_out = output_json(ctx)
    pdf_rag_directory_or_exit(ctx, name, context=context, hint=hint or _LIST_HINT)
    try:
        return load_pdf_rag(name)
    except ValueError as exc:
        emit_error(
            "configuration_error",
            f"PdfRag manifest is invalid: {exc}",
            output_json=json_out,
            hint=f"Delete it with 'zot pdf-rag delete {name} --yes' and recreate it",
            context=context,
        )
    except OSError as exc:
        emit_error(
            "configuration_error",
            f"Cannot read pdf_rag manifest: {exc}",
            output_json=json_out,
            context=context,
        )


def load_pdf_rag_if_readable(name: str) -> PdfRag | None:
    """尽力而为地加载 manifest，供删除预览和损坏 pdf_rag 清理使用。"""
    try:
        return load_pdf_rag(name)
    except (OSError, ValueError):
        return None


def pdf_rag_exists_or_error(ctx: typer.Context, name: str, *, context: str, hint: str = "") -> bool:
    json_out = output_json(ctx)
    try:
        return pdf_rag_exists(name)
    except ValueError as exc:
        emit_error("validation_error", str(exc), output_json=json_out, context=context)
    except OSError as exc:
        emit_error(
            "validation_error",
            f"Invalid pdf_rag name: {exc}",
            output_json=json_out,
            hint="PdfRag names must be a single directory component under .zot/pdf_rag/",
            context=context,
        )
