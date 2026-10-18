"""zot pdf: parse a PDF through MinerU and read the cached Markdown/JSON output."""

from __future__ import annotations

import json
import re

import click

from zotero_cli.config import get_data_dir, get_prefs_js_path, load_config, load_pdf_config, resolve_library_id
from zotero_cli.core.mineru import MinerUError, MinerUParseCache
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error
from zotero_cli.output.renderers import format_pdf_text

_NO_PDF_HINT = "Check item details with: zot read KEY"
_USAGE_HINT = 'Find item keys with: zot search "<query>"'
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


@click.command("pdf")
@click.option("--outline", is_flag=True, help="List headings from the MinerU Markdown as a numbered outline")
@click.option("--section", type=int, default=None, help="Return the N-th Markdown section")
@click.option("--structured", is_flag=True, help="Return MinerU content_list JSON instead of Markdown")
@click.option("--list-attachments", is_flag=True, help="List PDF attachment keys without parsing")
@click.option("--attachment", "attachment_key", default=None, help="Select a PDF by attachment key")
@click.argument("key")
@click.pass_context
def pdf_cmd(
    ctx: click.Context,
    outline: bool,
    section: int | None,
    structured: bool,
    list_attachments: bool,
    attachment_key: str | None,
    key: str,
) -> None:
    """Parse a Zotero PDF with MinerU cloud API and read its cached result.

    The canonical package stores Markdown for AI notes and content_list JSON for
    RAG. Repeated calls reuse the same content-addressed package.
    """
    json_out = ctx.obj.get("json", False)
    if sum((outline, section is not None, structured)) > 1:
        emit_error(
            "validation_error",
            "Use only one of --outline, --section, or --structured",
            output_json=json_out,
            context="pdf",
        )

    reader: ZoteroReader | None = None
    try:
        cfg = load_config(profile=ctx.obj.get("profile"))
        data_dir = get_data_dir(cfg)
        db_path = data_dir / "zotero.sqlite"
        library_id = resolve_library_id(db_path, ctx.obj)
        reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(cfg))
        attachments = reader.get_pdf_attachments(key)
        if list_attachments:
            if not attachments:
                if reader.get_item(key) is None:
                    emit_error(
                        "not_found",
                        f"Item '{key}' not found",
                        output_json=json_out,
                        hint=_USAGE_HINT,
                        context="pdf",
                    )
                emit_error(
                    "not_found",
                    f"No PDF attachment found for '{key}'",
                    output_json=json_out,
                    hint=_NO_PDF_HINT,
                    context="pdf",
                )
            data = {
                "key": key,
                "attachments": [
                    {"key": att.key, "filename": att.filename, "available": bool(att.path and att.path.is_file())}
                    for att in attachments
                ],
            }
            if json_out:
                click.echo(json.dumps(envelope_ok(data), ensure_ascii=False, indent=2))
            else:
                click.echo("\n".join(f"{att.key}  {att.filename}" for att in attachments))
            return
        if not attachments:
            if reader.get_item(key) is None:
                emit_error(
                    "not_found",
                    f"Item '{key}' not found",
                    output_json=json_out,
                    hint=_USAGE_HINT,
                    context="pdf",
                )
            emit_error(
                "not_found",
                f"No PDF attachment found for '{key}'",
                output_json=json_out,
                hint=_NO_PDF_HINT,
                context="pdf",
            )
        if not attachment_key and len(attachments) > 1:
            emit_error(
                "validation_error",
                f"Multiple PDFs for '{key}'; select with --attachment KEY",
                output_json=json_out,
                hint=f"Run zot pdf {key} --list-attachments",
                context="pdf",
            )
        attachment = (
            next((att for att in attachments if att.key == attachment_key), None) if attachment_key else attachments[0]
        )
        if attachment is None:
            emit_error(
                "not_found",
                f"PDF attachment '{attachment_key}' not found for '{key}'",
                output_json=json_out,
                context="pdf",
            )
        pdf_path = attachment.path
        if not pdf_path or not pdf_path.exists():
            emit_error(
                "not_found",
                f"PDF file not found at {pdf_path or attachment.filename}",
                output_json=json_out,
                hint="Check the Zotero storage directory",
                context="pdf",
            )

        try:
            cache = MinerUParseCache()
        except MinerUError as exc:
            emit_error(
                "configuration_error",
                f"MinerU cache configuration error: {exc}",
                output_json=json_out,
                hint="Check [pdf].mineru_model_version in .zot/config.toml",
                context="pdf",
            )
        except OSError as exc:
            emit_error(
                "internal_error",
                f"Could not initialize the MinerU cache: {exc}",
                output_json=json_out,
                context="pdf",
            )

        try:
            cached, fingerprint = cache.lookup(pdf_path)
        except MinerUError as exc:
            emit_error(
                "internal_error",
                f"Could not read the MinerU cache: {exc}",
                output_json=json_out,
                hint=_CACHE_STATE_HINT,
                context="pdf",
            )
        except OSError as exc:
            emit_error(
                "internal_error",
                f"Could not read PDF attachment '{pdf_path}': {exc}",
                output_json=json_out,
                context="pdf",
            )

        if cached is None and not load_pdf_config().mineru_token:
            emit_error(
                "configuration_error",
                "MinerU API token is not configured",
                output_json=json_out,
                hint="Set [pdf].mineru_token in .zot/config.toml",
                context="pdf",
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
                    context="pdf",
                )
            except OSError as exc:
                emit_error(
                    "internal_error",
                    f"Could not store the MinerU parse package: {exc}",
                    output_json=json_out,
                    context="pdf",
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
                    click.echo(json.dumps(envelope_ok(data), ensure_ascii=False, indent=2))
                else:
                    click.echo(json.dumps(parsed.content_list, ensure_ascii=False, indent=2))
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
                        context="pdf",
                    )
                click.echo(format_pdf_text(key, section=section, content=content, output_json=json_out))
                return
            if outline:
                outline_data = [
                    {"number": number, "text": text, "level": level} for number, text, level in _parse_outline(markdown)
                ]
                if json_out:
                    click.echo(
                        json.dumps(
                            envelope_ok({"key": key, "outline": outline_data, **meta}),
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                elif not outline_data:
                    click.echo("No headings found in document.")
                else:
                    click.echo(format_pdf_text(key, outline=outline_data, output_json=False))
                return

            if json_out:
                click.echo(
                    json.dumps(envelope_ok({"key": key, "markdown": markdown, **meta}), ensure_ascii=False, indent=2)
                )
            else:
                click.echo(markdown)
        except SystemExit:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            emit_error(
                "internal_error",
                f"Cached MinerU package for '{key}' is unreadable: {exc}",
                output_json=json_out,
                hint=f"Delete the package at {parsed.root} and retry",
                context="pdf",
            )
        except Exception as exc:
            emit_error(
                "internal_error",
                f"Could not render MinerU output: {type(exc).__name__}: {exc}",
                output_json=json_out,
                context="pdf",
            )
    except SystemExit:
        raise
    except Exception as exc:
        # Last-resort boundary: the command must never leak a traceback.
        emit_error(
            "internal_error",
            f"Failed to read PDF '{key}': {type(exc).__name__}: {exc}",
            output_json=json_out,
            context="pdf",
        )
    finally:
        if reader is not None:
            reader.close()
