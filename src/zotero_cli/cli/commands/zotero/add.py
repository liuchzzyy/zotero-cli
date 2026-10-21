"""通过 DOI、URL、文件和 PDF 写入条目。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import typer

from zotero_cli.cli.commands.zotero.reading import app
from zotero_cli.cli.output.envelope import envelope_ok, envelope_partial, envelope_partial_data
from zotero_cli.cli.output.exit_codes import emit_error, exit_code_for
from zotero_cli.cli.output.streaming import emit_progress
from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.zotero.metadata import MetadataResolveError, resolve_doi
from zotero_cli.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.writer_errors import ZoteroWriteError


def _resolve_metadata(doi: str) -> tuple[dict | None, str | None]:
    """解析 DOI 元数据；查询失败时返回警告，不中断条目创建以避免重复写入。"""
    try:
        fields = resolve_doi(doi)
    except MetadataResolveError as error:
        return None, str(error)
    if fields is None:
        return None, "no_match"
    return fields, None


def _resolved_summary(fields: dict) -> dict:
    """提取适合展示的元数据摘要。"""
    summary: dict = {}
    if "title" in fields:
        summary["title"] = fields["title"]
    creators = fields.get("creators") or []
    if creators:
        first = creators[0]
        last = first.get("lastName") or first.get("name") or ""
        suffix = " et al." if len(creators) > 1 else ""
        summary["author"] = f"{last}{suffix}".strip()
    if "publicationTitle" in fields:
        summary["journal"] = fields["publicationTitle"]
    if "date" in fields:
        summary["date"] = fields["date"]
    return summary


@app.command(name="add")
def add_cmd(
    ctx: typer.Context,
    doi: str | None = typer.Option(None, help="DOI to add"),
    url: str | None = typer.Option(None, help="URL to add"),
    from_file: Path | None = typer.Option(None, "--from-file", exists=True, help="File with one DOI or URL per line"),
    pdf_file: Path | None = typer.Option(None, "--pdf", exists=True, help="PDF file to extract DOI from and attach"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview what would be added without calling the API"),
    idempotency_key: str | None = typer.Option(
        None, help="Key so retries are safe; same key returns the original result"
    ),
    no_resolve: bool = typer.Option(False, "--no-resolve", help="Skip Crossref DOI metadata lookup"),
) -> None:
    """通过 DOI、URL、批处理文件或 PDF 添加条目。"""
    json_out = ctx.obj.get("json", False)
    if idempotency_key is not None and from_file:
        emit_error(
            "validation_error",
            "--idempotency-key is not supported with --from-file",
            output_json=json_out,
            hint="Idempotent retries are supported for single --doi, --url, and --pdf inputs",
            context="add",
        )
    config = load_config(profile=ctx.obj.get("profile"))

    if dry_run:
        would: dict = {}
        if pdf_file:
            would = {"source": "pdf", "pdf": str(pdf_file), "doi_override": doi, "resolve_metadata": not no_resolve}
        elif from_file:
            would = {"source": "file", "path": str(from_file), "resolve_metadata": not no_resolve}
        elif doi:
            would = {"source": "doi", "doi": doi, "resolve_metadata": not no_resolve}
        elif url:
            would = {"source": "url", "url": url}
        else:
            emit_error(
                "validation_error",
                "Provide --doi, --url, --from-file, or --pdf",
                output_json=json_out,
                hint="Example: zot zotero add --doi '10.1038/...' --dry-run",
                context="add",
            )
        if json_out:
            typer.echo(json.dumps(envelope_ok({"would": would}, dry_run=True), indent=2, ensure_ascii=False))
        else:
            typer.echo(f"[dry-run] Would add: {would}")
        return

    library_type = ctx.obj.get("library_type", "user")
    group_id = ctx.obj.get("group_id")
    library_id, api_key = resolve_write_credentials(config, library_type=library_type, group_id=group_id)
    if not library_id or not api_key:
        emit_error(
            "auth_missing",
            "Write credentials not configured",
            output_json=json_out,
            hint="Run 'zot config init' to set up API credentials",
            context="add",
        )

    if pdf_file:
        _add_from_pdf(
            Path(pdf_file),
            doi,
            library_id,
            api_key,
            json_out,
            library_type=library_type,
            resolve=not no_resolve,
            idempotency_key=idempotency_key,
        )
        return

    if from_file:
        _add_from_file(
            Path(from_file), library_id, api_key, json_out, library_type=library_type, resolve=not no_resolve
        )
        return

    if not doi and not url:
        emit_error(
            "validation_error",
            "Provide --doi, --url, or --from-file",
            output_json=json_out,
            hint="Example: zot zotero add --doi '10.1038/...' or --from-file dois.txt",
            context="add",
        )

    from zotero_cli.zotero.idempotency import (
        PendingMutationError,
        begin_mutation,
        failure_definitely_rejected,
        release_rejected_mutation,
        request_scope,
        store_cached,
    )

    cache_scope = request_scope(
        "add", library_type, str(library_id), {"doi": doi, "url": url, "no_resolve": no_resolve}
    )
    if idempotency_key:
        try:
            cached = begin_mutation(cache_scope, idempotency_key)
        except PendingMutationError as exc:
            emit_error(
                "conflict",
                str(exc),
                output_json=json_out,
                retryable=False,
                hint="Reconcile the remote library before retrying; do not bypass this with a new key",
                context="add",
            )
        except (OSError, sqlite3.Error):
            emit_error(
                "configuration_error",
                "Could not durably reserve item creation; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="add",
            )
        if cached is not None:
            if json_out:
                typer.echo(json.dumps(cached, indent=2, ensure_ascii=False))
            else:
                typer.echo(f"Item added: {cached.get('data', {}).get('key', '?')} (cached).")
            return

    extra_fields: dict | None = None
    resolved_summary: dict | None = None
    resolve_warning: str | None = None
    if doi and not no_resolve:
        extra_fields, resolve_warning = _resolve_metadata(doi)
        if extra_fields:
            resolved_summary = _resolved_summary(extra_fields)
            if not json_out:
                bits = [f"Resolved via Crossref: {resolved_summary.get('title', '(no title)')}"]
                meta = " · ".join(
                    v
                    for v in (
                        resolved_summary.get("author"),
                        resolved_summary.get("journal"),
                        resolved_summary.get("date"),
                    )
                    if v
                )
                if meta:
                    bits.append(f"  {meta}")
                typer.echo("\n".join(bits), err=True)
        elif not json_out:
            if resolve_warning == "no_match":
                typer.echo("Crossref has no record for this DOI — creating a bare item (no metadata).", err=True)
            else:
                typer.echo(
                    f"Could not resolve metadata via Crossref ({resolve_warning}); creating bare item.",
                    err=True,
                )

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        key = writer.add_item(doi=doi, url=url, extra_fields=extra_fields)
    except ZoteroWriteError as error:
        rejected = False
        if idempotency_key:
            rejected = failure_definitely_rejected(
                response=writer._zot.request,
                code=error.code,
                retryable=error.retryable,
                committed=error.committed,
            )
            if rejected:
                try:
                    release_rejected_mutation(cache_scope, idempotency_key)
                except (OSError, sqlite3.Error):
                    pass
        emit_error(
            error.code,
            str(error),
            output_json=json_out,
            retryable=False if (error.committed or (idempotency_key and not rejected)) else error.retryable,
            committed=error.committed,
            hint=(
                "The write outcome is unknown. Reconcile the library before retrying; do not reuse this key blindly"
                if error.committed or (idempotency_key and not rejected)
                else "Check API credentials and network"
            ),
            context="add",
        )
    data: dict = {"key": key, "doi": doi, "url": url, "sync_required": True}
    if resolved_summary is not None:
        data["resolved"] = resolved_summary
    elif doi and not no_resolve:
        data["resolved"] = None
        data["resolve_warning"] = resolve_warning
    env = envelope_ok(
        data,
        next=[f"zot zotero attach {key} --file <path>"],
    )
    if idempotency_key:
        try:
            store_cached(cache_scope, idempotency_key, env)
        except (OSError, sqlite3.Error) as exc:
            typer.echo(f"Warning: item was added but the retry result could not be cached: {exc}", err=True)
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Item added: {key}")
        typer.echo(SYNC_REMINDER, err=True)


def _pdf_scope_payload(pdf_path: Path, doi_override: str | None, resolve: bool) -> dict:
    """以路径、大小、修改时间和覆盖参数标识 PDF 幂等请求。"""
    try:
        stat = pdf_path.stat()
        size = stat.st_size
        mtime_ns = stat.st_mtime_ns
    except OSError:
        size = None
        mtime_ns = None
    return {"pdf": str(pdf_path.resolve()), "size": size, "mtime_ns": mtime_ns, "doi": doi_override, "resolve": resolve}


def _add_from_pdf(
    pdf_path: Path,
    doi_override: str | None,
    library_id: str,
    api_key: str,
    json_out: bool,
    library_type: str = "user",
    resolve: bool = True,
    idempotency_key: str | None = None,
) -> None:
    """解析 PDF 后创建条目和上传附件；部分成功时保留幂等预留，防止盲重试。"""
    import sqlite3 as _sqlite3

    from zotero_cli.ai_pdf import MinerUError, MinerUParseCache, extract_doi_from_markdown
    from zotero_cli.config import load_pdf_config
    from zotero_cli.zotero.idempotency import (
        PendingMutationError,
        begin_mutation,
        failure_definitely_rejected,
        release_rejected_mutation,
        request_scope,
        store_cached,
    )

    cache_scope = request_scope(
        "add-pdf",
        library_type,
        str(library_id),
        _pdf_scope_payload(pdf_path, doi_override, resolve),
    )
    if idempotency_key:
        try:
            cached = begin_mutation(cache_scope, idempotency_key)
        except PendingMutationError as exc:
            emit_error(
                "conflict",
                str(exc),
                output_json=json_out,
                retryable=False,
                hint="Reconcile the remote library before retrying; do not bypass this with a new key",
                context="add",
            )
        except (OSError, _sqlite3.Error):
            emit_error(
                "configuration_error",
                "Could not durably reserve item creation; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="add",
            )
        if cached is not None:
            if json_out:
                typer.echo(json.dumps(cached, indent=2, ensure_ascii=False))
            else:
                typer.echo(f"Item added: {cached.get('data', {}).get('key', '?')} (cached).")
            return

    def _release() -> None:
        if not idempotency_key:
            return
        try:
            release_rejected_mutation(cache_scope, idempotency_key)
        except (OSError, _sqlite3.Error):
            pass

    doi = doi_override
    if not doi:
        cache = MinerUParseCache()
        if cache.get(pdf_path) is None and not load_pdf_config().mineru_token:
            _release()
            emit_error(
                "configuration_error",
                "MinerU API token is not configured",
                output_json=json_out,
                hint="Set [pdf].mineru_token in .zot/config.toml or pass --doi",
                context="add",
            )
        try:
            parsed = cache.ensure(pdf_path)
        except MinerUError as exc:
            _release()
            emit_error(
                "network_error",
                str(exc),
                output_json=json_out,
                retryable=True,
                context="add",
            )
        doi = extract_doi_from_markdown(parsed.markdown)
    if not doi:
        _release()
        emit_error(
            "validation_error",
            "No DOI found in PDF",
            output_json=json_out,
            hint="Use --doi to specify the DOI manually: zot zotero add --pdf paper.pdf --doi '10.1234/...'",
            context="add",
        )

    extra_fields: dict | None = None
    resolved_summary: dict | None = None
    resolve_warning: str | None = None
    if doi and resolve:
        extra_fields, resolve_warning = _resolve_metadata(doi)
        if extra_fields:
            resolved_summary = _resolved_summary(extra_fields)

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        key = writer.add_item(doi=doi, extra_fields=extra_fields)
    except ZoteroWriteError as error:
        rejected = False
        if idempotency_key:
            rejected = failure_definitely_rejected(
                response=writer._zot.request,
                code=error.code,
                retryable=error.retryable,
                committed=error.committed,
            )
            if rejected:
                _release()
        emit_error(
            error.code,
            str(error),
            output_json=json_out,
            retryable=False if (error.committed or (idempotency_key and not rejected)) else error.retryable,
            committed=error.committed,
            hint=(
                "The write outcome is unknown. Reconcile the library before retrying; do not reuse this key blindly"
                if error.committed or (idempotency_key and not rejected)
                else "Check API credentials and network"
            ),
            context="add",
        )

    att_key: str | None = None
    attach_error: ZoteroWriteError | None = None
    try:
        att_key = writer.upload_attachment(key, pdf_path)
    except ZoteroWriteError as error:
        attach_error = error

    data: dict = {"key": key, "doi": doi, "sync_required": True}
    if resolved_summary is not None:
        data["resolved"] = resolved_summary
    elif resolve:
        data["resolved"] = None
        data["resolve_warning"] = resolve_warning
    if att_key:
        data["attachment_key"] = att_key
    if attach_error is not None:
        data["attachment_error"] = str(attach_error)
        data["attachment_error_code"] = attach_error.code
        data["attachment_committed"] = attach_error.committed
        env = envelope_partial_data(data, next=[f"zot zotero attach {key} --file {pdf_path}"])
        if json_out:
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Item created: {key} (DOI: {doi})")
            typer.echo(f"Attachment upload failed: {attach_error}", err=True)
            if idempotency_key:
                typer.echo(
                    "The idempotency key stays reserved because the item already exists; retry the attachment "
                    f"with: zot zotero attach {key} --file {pdf_path}",
                    err=True,
                )
            else:
                typer.echo(f"Retry with: zot zotero attach {key} --file {pdf_path}", err=True)
        raise SystemExit(exit_code_for(attach_error.code))

    env = envelope_ok(data)
    if idempotency_key:
        try:
            store_cached(cache_scope, idempotency_key, env)
        except (OSError, _sqlite3.Error) as exc:
            typer.echo(f"Warning: item was added but the retry result could not be cached: {exc}", err=True)
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Item created: {key} (DOI: {doi})")
        if resolved_summary:
            meta = " · ".join(
                v
                for v in (resolved_summary.get("author"), resolved_summary.get("journal"), resolved_summary.get("date"))
                if v
            )
            typer.echo(f"  {resolved_summary.get('title', '')}", err=True)
            if meta:
                typer.echo(f"  {meta}", err=True)
        elif resolve and resolve_warning:
            if resolve_warning == "no_match":
                typer.echo("Crossref has no record for this DOI — item created without metadata.", err=True)
            else:
                typer.echo(f"Crossref lookup failed ({resolve_warning}); item created without metadata.", err=True)
        typer.echo(f"Attachment uploaded: {att_key}")
        typer.echo(SYNC_REMINDER, err=True)


def _add_from_file(
    file_path: Path,
    library_id: str,
    api_key: str,
    json_out: bool,
    library_type: str = "user",
    resolve: bool = True,
) -> None:
    """逐行读取 DOI 或 URL 并批量创建条目。"""
    lines = [line.strip() for line in file_path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        emit_error(
            "validation_error",
            "File is empty or has no valid entries",
            output_json=json_out,
            hint="One DOI or URL per line",
            context="add",
        )

    emit_progress("start", phase="batch_add", total=len(lines), source=str(file_path))
    if not json_out:
        typer.echo(f"Adding {len(lines)} items from {file_path}...", err=True)
    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    succeeded: list[dict] = []
    failed: list[dict] = []
    for i, entry in enumerate(lines, 1):
        emit_progress("progress", phase="batch_add", done=i - 1, total=len(lines))
        is_doi = not entry.startswith("http")
        try:
            if is_doi:
                extra_fields: dict | None = None
                resolved_summary: dict | None = None
                resolve_warning: str | None = None
                if resolve:
                    extra_fields, resolve_warning = _resolve_metadata(entry)
                    if extra_fields:
                        resolved_summary = _resolved_summary(extra_fields)
                key = writer.add_item(doi=entry, extra_fields=extra_fields)
                row: dict = {"entry": entry, "key": key}
                if resolved_summary is not None:
                    row["resolved"] = resolved_summary
                elif resolve:
                    row["resolved"] = None
                    row["resolve_warning"] = resolve_warning
                succeeded.append(row)
            else:
                key = writer.add_item(url=entry)
                succeeded.append({"entry": entry, "key": key})
            if not json_out:
                typer.echo(f"  [{i}/{len(lines)}] Added: {key} ({entry})", err=True)
        except ZoteroWriteError as error:
            failed.append(
                {
                    "entry": entry,
                    "error": {
                        "code": error.code,
                        "message": str(error),
                        "retryable": error.retryable,
                        "committed": error.committed,
                    },
                }
            )
            if not json_out:
                typer.echo(f"  [{i}/{len(lines)}] Failed: {entry} ({error})", err=True)

    emit_progress(
        "complete", phase="batch_add", done=len(lines), total=len(lines), succeeded=len(succeeded), failed=len(failed)
    )
    if json_out:
        if failed:
            env = envelope_partial(succeeded, failed, meta={"total": len(lines), "sync_required": bool(succeeded)})
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
            raise SystemExit(exit_code_for(failed[0]["error"]["code"]))
        env = envelope_ok(
            {"succeeded": succeeded, "failed": []},
            meta={"total": len(lines), "sync_required": bool(succeeded)},
        )
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        return

    typer.echo(f"\nDone: {len(succeeded)} added, {len(failed)} failed", err=True)
    if succeeded:
        typer.echo(SYNC_REMINDER, err=True)
    if failed:
        raise SystemExit(exit_code_for(failed[0]["error"]["code"]))
