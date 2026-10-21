"""`zot ai-pdf`：通过 MinerU 解析 Zotero PDF 并读取缓存结果。"""

from __future__ import annotations

import json
import re

import typer

from zotero_cli.ai_pdf import MinerUError, MinerUParseCache
from zotero_cli.config import get_data_dir, get_prefs_js_path, load_config, load_pdf_config, resolve_library_id
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error, emit_exception
from zotero_cli.output.renderers import format_pdf_text
from zotero_cli.zotero.pdf import select_pdf_attachment
from zotero_cli.zotero.reader import ZoteroReader

_NO_PDF_HINT = "Check item details with: zot zotero read KEY"
_USAGE_HINT = 'Find item keys with: zot zotero search "<query>"'
_CACHE_STATE_HINT = "The local MinerU cache appears corrupt; remove the affected package directory and retry"


def _parse_outline(markdown: str) -> list[tuple[int, str, int]]:
    pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    return [
        (index, match.group(2).strip(), len(match.group(1)))
        for index, match in enumerate(pattern.finditer(markdown), 1)
    ]


def _extract_section(markdown: str, section_num: int) -> str:
    pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(pattern.finditer(markdown))
    if section_num < 1 or section_num > len(matches):
        return ""
    target = matches[section_num - 1]
    target_level = len(target.group(1))
    end = len(markdown)
    for match in matches[section_num:]:
        if len(match.group(1)) <= target_level:
            end = match.start()
            break
    return markdown[target.start() : end].strip()


def ai_pdf(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 父条目键。"),
    outline: bool = typer.Option(False, "--outline", help="列出 MinerU Markdown 标题编号。"),
    section: int | None = typer.Option(None, help="返回第 N 个 Markdown 区段。"),
    structured: bool = typer.Option(False, "--structured", help="返回 MinerU content_list JSON，而非 Markdown。"),
    attachment_key: str | None = typer.Option(None, "--attachment", help="指定 PDF 附件键。"),
) -> None:
    """通过 MinerU 云端 API 解析 Zotero PDF 并读取缓存结果。"""
    json_out = ctx.obj.get("json", False)
    verbose = bool(ctx.obj.get("verbose", False))
    if sum((outline, section is not None, structured)) > 1:
        emit_error(
            "validation_error",
            "Use only one of --outline, --section, or --structured",
            output_json=json_out,
            context="ai-pdf",
        )

    reader: ZoteroReader | None = None
    try:
        cfg = load_config(profile=ctx.obj.get("profile"))
        data_dir = get_data_dir(cfg)
        db_path = data_dir / "zotero.sqlite"
        library_id = resolve_library_id(db_path, ctx.obj)
        reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(cfg))
        attachments = reader.get_pdf_attachments(key)
        if not attachments:
            if reader.get_item(key) is None:
                emit_error(
                    "not_found",
                    f"Item '{key}' not found",
                    output_json=json_out,
                    hint=_USAGE_HINT,
                    context="ai-pdf",
                )
            emit_error(
                "not_found",
                f"No PDF attachment found for '{key}'",
                output_json=json_out,
                hint=_NO_PDF_HINT,
                context="ai-pdf",
            )
        try:
            attachment = select_pdf_attachment(attachments, attachment_key)
        except LookupError as error:
            emit_error("not_found", str(error), output_json=json_out, context="ai-pdf")
        except ValueError as error:
            emit_error(
                "validation_error",
                str(error),
                output_json=json_out,
                hint=f"Run 'zot zotero pdf list {key}' to select an attachment",
                context="ai-pdf",
            )
        pdf_path = attachment.path
        if not pdf_path or not pdf_path.exists():
            emit_error(
                "not_found",
                f"PDF file not found at {pdf_path or attachment.filename}",
                output_json=json_out,
                hint="Check the Zotero storage directory",
                context="ai-pdf",
            )

        try:
            cache = MinerUParseCache()
        except MinerUError as exc:
            emit_error(
                "configuration_error",
                f"MinerU cache configuration error: {exc}",
                output_json=json_out,
                hint="Check [pdf].mineru_model_version in .zot/config.toml",
                context="ai-pdf",
            )

        try:
            cached, fingerprint = cache.lookup(pdf_path)
        except MinerUError as exc:
            emit_error(
                "internal_error",
                f"Could not read the MinerU cache: {exc}",
                output_json=json_out,
                hint=_CACHE_STATE_HINT,
                context="ai-pdf",
            )

        if cached is None and not load_pdf_config().mineru_token:
            emit_error(
                "configuration_error",
                "MinerU API token is not configured",
                output_json=json_out,
                hint="Set [pdf].mineru_token in .zot/config.toml",
                context="ai-pdf",
            )
        if cached is not None:
            parsed = cached
        else:
            try:
                parsed = cache.ensure(pdf_path, fingerprint=fingerprint)
            except MinerUError as exc:
                emit_error(
                    "network_error",
                    str(exc),
                    output_json=json_out,
                    retryable=True,
                    hint="Check the MinerU token, API status, and network connection",
                    context="ai-pdf",
                )

        meta = {
            "attachment_key": attachment.key,
            "fingerprint": parsed.fingerprint,
            "mineru_model_version": parsed.model_version,
            "cache_dir": str(parsed.root),
        }
        try:
            if structured:
                data = {"key": key, "content_list": parsed.content_list, **meta}
                if json_out:
                    typer.echo(json.dumps(envelope_ok(data), ensure_ascii=False, indent=2))
                else:
                    typer.echo(json.dumps(parsed.content_list, ensure_ascii=False, indent=2))
                return

            markdown = parsed.markdown
            if section is not None:
                content = _extract_section(markdown, section)
                if not content:
                    emit_error(
                        "not_found",
                        f"Section {section} not found",
                        output_json=json_out,
                        hint="Use --outline first to see available sections",
                        context="ai-pdf",
                    )
                typer.echo(format_pdf_text(key, section=section, content=content, output_json=json_out))
                return
            if outline:
                outline_data = [
                    {"number": number, "text": text, "level": level} for number, text, level in _parse_outline(markdown)
                ]
                if json_out:
                    typer.echo(
                        json.dumps(
                            envelope_ok({"key": key, "outline": outline_data, **meta}),
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                elif not outline_data:
                    typer.echo("No headings found in document.")
                else:
                    typer.echo(format_pdf_text(key, outline=outline_data, output_json=False))
                return

            if json_out:
                typer.echo(
                    json.dumps(envelope_ok({"key": key, "markdown": markdown, **meta}), ensure_ascii=False, indent=2)
                )
            else:
                typer.echo(markdown)
        except SystemExit:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            emit_error(
                "internal_error",
                f"Cached MinerU package for '{key}' is unreadable: {exc}",
                output_json=json_out,
                hint=f"Delete the package at {parsed.root} and retry",
                context="ai-pdf",
            )
        except Exception as exc:
            emit_exception(exc, output_json=json_out, verbose=verbose, context="ai-pdf")
    except SystemExit:
        raise
    except Exception as exc:
        # Last-resort boundary: classify centrally (configuration vs runtime) and
        # honor --verbose tracebacks like every other command.
        emit_exception(exc, output_json=json_out, verbose=verbose, context="ai-pdf")
    finally:
        if reader is not None:
            reader.close()
