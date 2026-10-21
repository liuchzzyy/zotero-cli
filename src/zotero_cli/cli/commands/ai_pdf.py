"""`zot ai-pdf`：通过 MinerU 解析 Zotero PDF 并读取缓存结果。"""

from __future__ import annotations

import json

import typer

from zotero_cli.ai_pdf.content import read_pdf_content
from zotero_cli.ai_pdf.documents import PdfDocumentError, ZoteroPdfDocuments
from zotero_cli.ai_pdf.errors import MinerUError, MinerUTokenMissingError, PdfCacheReadError, PdfSectionNotFoundError
from zotero_cli.cli.renderers import format_pdf_text
from zotero_cli.config import get_data_dir, get_prefs_js_path, load_config, resolve_library_id
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error, emit_exception
from zotero_cli.zotero.access.reader import ZoteroReader

_NO_PDF_HINT = "Check item details with: zot zotero read KEY"
_USAGE_HINT = 'Find item keys with: zot zotero search "<query>"'


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
        config = load_config(profile=ctx.obj.get("profile"))
        data_dir = get_data_dir(config)
        db_path = data_dir / "zotero.sqlite"
        library_id = resolve_library_id(db_path, ctx.obj)
        reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(config))
        try:
            documents = ZoteroPdfDocuments(reader)
        except MinerUError as error:
            emit_error(
                "configuration_error",
                f"MinerU cache configuration error: {error}",
                output_json=json_out,
                hint="Check [pdf].mineru_model_version in .zot/config.toml",
                context="ai-pdf",
            )
        try:
            parsed_document = documents.parse(key, attachment_key)
        except PdfDocumentError as error:
            if error.kind == "item":
                emit_error("not_found", str(error), output_json=json_out, hint=_USAGE_HINT, context="ai-pdf")
            if error.kind == "pdf":
                emit_error("not_found", str(error), output_json=json_out, hint=_NO_PDF_HINT, context="ai-pdf")
            if error.kind == "selection":
                emit_error(
                    "validation_error",
                    str(error),
                    output_json=json_out,
                    hint=f"Run 'zot zotero pdf list {key}' to select an attachment",
                    context="ai-pdf",
                )
            emit_error(
                "not_found",
                str(error),
                output_json=json_out,
                hint="Check the Zotero storage directory" if error.kind == "file" else "",
                context="ai-pdf",
            )
        except PdfCacheReadError as error:
            emit_error(
                "internal_error",
                f"Could not read the MinerU cache: {error}",
                output_json=json_out,
                hint="The local MinerU cache appears corrupt; remove the affected package directory and retry",
                context="ai-pdf",
            )
        except MinerUTokenMissingError as error:
            emit_error(
                "configuration_error",
                str(error),
                output_json=json_out,
                hint="Set [pdf].mineru_token in .zot/config.toml",
                context="ai-pdf",
            )
        except MinerUError as error:
            emit_error(
                "network_error",
                str(error),
                output_json=json_out,
                retryable=True,
                hint="Check the MinerU token, API status, and network connection",
                context="ai-pdf",
            )
        document = parsed_document.document
        parsed = parsed_document.parsed

        meta = {
            "attachment_key": document.attachment.key,
            "fingerprint": parsed.fingerprint,
            "mineru_model_version": parsed.model_version,
            "cache_dir": str(parsed.root),
        }
        try:
            content = read_pdf_content(parsed, outline=outline, section=section, structured=structured)
            if structured:
                data = {"key": key, "content_list": content.content_list, **meta}
                if json_out:
                    typer.echo(json.dumps(envelope_ok(data), ensure_ascii=False, indent=2))
                else:
                    typer.echo(json.dumps(content.content_list, ensure_ascii=False, indent=2))
                return
            if section is not None:
                typer.echo(format_pdf_text(key, section=section, content=content.markdown, output_json=json_out))
                return
            if outline:
                if json_out:
                    typer.echo(
                        json.dumps(
                            envelope_ok({"key": key, "outline": content.outline, **meta}), ensure_ascii=False, indent=2
                        )
                    )
                elif not content.outline:
                    typer.echo("No headings found in document.")
                else:
                    typer.echo(format_pdf_text(key, outline=content.outline, output_json=False))
                return
            if json_out:
                typer.echo(
                    json.dumps(
                        envelope_ok({"key": key, "markdown": content.markdown, **meta}), ensure_ascii=False, indent=2
                    )
                )
            else:
                typer.echo(content.markdown)
        except PdfSectionNotFoundError as error:
            emit_error(
                "not_found",
                str(error),
                output_json=json_out,
                hint="Use --outline first to see available sections",
                context="ai-pdf",
            )
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
        # 兜底边界：集中分类（配置错误与运行时错误），并像其他命令一样
        # 遵循 --verbose 的回溯输出。
        emit_exception(exc, output_json=json_out, verbose=verbose, context="ai-pdf")
    finally:
        if reader is not None:
            reader.close()
