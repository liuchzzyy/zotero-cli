"""条目的读取、写入、检索、引文与本地打开命令。"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import typer

from zotero_cli.cli.app.group import TieredGroup
from zotero_cli.cli.renderers import format_duplicates, format_item_detail, format_items
from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_config,
    resolve_library_id,
    resolve_write_credentials,
)
from zotero_cli.output.envelope import envelope_error, envelope_ok, envelope_partial, envelope_partial_data
from zotero_cli.output.exit_codes import EXIT_CONFLICT, emit_error, exit_code_for
from zotero_cli.output.sanitize import sanitize_text
from zotero_cli.output.streaming import emit_progress, stream_items
from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.idempotency import (
    PendingMutationError,
    begin_mutation,
    release_rejected_mutation,
    request_scope,
    store_cached,
)
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.items import (
    UpdateInputError,
    _load_updates_from_jsonl,
    _parse_inline_fields,
    delete_items,
    import_items,
    resolve_doi,
    resolve_item_metadata,
    summarize_item_metadata,
    update_item_with_tags,
    update_items,
)
from zotero_cli.zotero.models import Item
from zotero_cli.zotero.pdf import AddFromPdfError, add_item_from_pdf

app = typer.Typer(cls=TieredGroup, help="读取和检索 Zotero 条目。", no_args_is_help=True, rich_markup_mode=None)


_SORT_FIELDS = {"dateAdded", "dateModified", "title", "creator"}


_DIRECTIONS = {"asc", "desc"}


def _create_reader(ctx: typer.Context) -> tuple[ZoteroReader, Any]:
    config = load_config(profile=ctx.obj.get("profile"))
    database_path = get_data_dir(config) / "zotero.sqlite"
    library_id = resolve_library_id(database_path, ctx.obj)
    return ZoteroReader(database_path, library_id=library_id), config


def _result_limit(limit: int | None, ctx: typer.Context, config: Any) -> int:
    return limit if limit is not None else ctx.obj.get("limit", config.default_limit)


def _validate_sort_options(sort: str | None, direction: str) -> None:
    if sort not in _SORT_FIELDS | {None}:
        raise typer.BadParameter("sort 必须是 dateAdded、dateModified、title 或 creator")
    if direction not in _DIRECTIONS:
        raise typer.BadParameter("direction 必须是 asc 或 desc")


def _render_search_result(
    *,
    result: Any,
    ctx: typer.Context,
    stream: bool,
    empty_message: str | None = None,
) -> None:
    json_output = ctx.obj.get("json", False)
    detail = ctx.obj.get("detail", "standard")
    fulltext_limited = getattr(result, "fulltext_limited", None)
    metadata = {"fulltext_limited": bool(fulltext_limited)} if fulltext_limited is not None else None
    if fulltext_limited and not json_output:
        typer.echo(
            "Note: full-text search was bounded; some indexed attachment text may not have been searched.", err=True
        )
    if stream:
        typer.echo(stream_items(result.items, detail=detail, total=result.total, meta=metadata))
        return
    if not result.items and empty_message is not None and not json_output:
        typer.echo(empty_message, err=True)
        return
    typer.echo(format_items(result.items, output_json=json_output, detail=detail, meta=metadata))


@app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(help="检索词。"),
    collection: str | None = typer.Option(None, help="按 Zotero 集合名称筛选。"),
    item_type: str | None = typer.Option(None, "--type", help="按条目类型筛选。"),
    sort: str | None = typer.Option(None, help="排序字段。"),
    direction: str = typer.Option("desc", help="排序方向：asc 或 desc。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
    stream: bool = typer.Option(False, "--stream", help="以 NDJSON 逐条输出。"),
) -> None:
    """按标题、作者、标签或全文检索条目。"""
    _validate_sort_options(sort, direction)
    reader, config = _create_reader(ctx)
    try:
        try:
            result = reader.search(
                query,
                collection=collection,
                item_type=item_type,
                sort=sort,
                direction=direction,
                limit=_result_limit(limit, ctx, config),
            )
        except ValueError as error:
            emit_error("validation_error", str(error), output_json=ctx.obj.get("json", False))
        _render_search_result(result=result, ctx=ctx, stream=stream, empty_message="No results found.")
    finally:
        reader.close()


@app.command(name="list")
def list_items(
    ctx: typer.Context,
    collection: str | None = typer.Option(None, help="按 Zotero 集合名称筛选。"),
    item_type: str | None = typer.Option(None, "--type", help="按条目类型筛选。"),
    sort: str | None = typer.Option(None, help="排序字段。"),
    direction: str = typer.Option("desc", help="排序方向：asc 或 desc。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
    stream: bool = typer.Option(False, "--stream", help="以 NDJSON 逐条输出。"),
) -> None:
    """列出 Zotero 库中的条目。"""
    _validate_sort_options(sort, direction)
    reader, config = _create_reader(ctx)
    try:
        try:
            result = reader.search(
                "",
                collection=collection,
                item_type=item_type,
                sort=sort,
                direction=direction,
                limit=_result_limit(limit, ctx, config),
            )
        except ValueError as error:
            emit_error("validation_error", str(error), output_json=ctx.obj.get("json", False))
        _render_search_result(result=result, ctx=ctx, stream=stream)
    finally:
        reader.close()


@app.command()
def stats(ctx: typer.Context) -> None:
    """显示条目、附件、笔记、集合和标签的统计信息。"""
    reader, _config = _create_reader(ctx)
    try:
        statistics = reader.get_stats()
        if ctx.obj.get("json", False):
            typer.echo(json.dumps(envelope_ok(statistics), indent=2, ensure_ascii=False))
            return
        typer.echo(f"Total items: {statistics['total_items']}")
        typer.echo(f"PDF attachments: {statistics['pdf_attachments']}")
        typer.echo(f"Notes: {statistics['notes']}")
        typer.echo("\nItems by type:")
        for name, count in statistics["by_type"].items():
            typer.echo(f"  {sanitize_text(name)}: {count}")
        typer.echo(f"\nCollections ({len(statistics['collections'])}):")
        for name, count in statistics["collections"].items():
            typer.echo(f"  {sanitize_text(name)}: {count} items")
        typer.echo(f"\nTop tags ({len(statistics['top_tags'])}):")
        for name, count in statistics["top_tags"].items():
            typer.echo(f"  {sanitize_text(name)}: {count}")
    finally:
        reader.close()


@app.command()
def recent(
    ctx: typer.Context,
    days: int = typer.Option(7, help="向前检索的天数。"),
    modified: bool = typer.Option(False, "--modified", help="按修改时间而不是添加时间排序。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
    stream: bool = typer.Option(False, "--stream", help="以 NDJSON 逐条输出。"),
) -> None:
    """显示最近添加或修改的条目。"""
    reader, config = _create_reader(ctx)
    try:
        result_limit = _result_limit(limit, ctx, config)
        sort_field = "dateModified" if modified else "dateAdded"
        since = datetime.now(UTC) - timedelta(days=days)
        fetch_limit = result_limit + 1 if result_limit >= 0 else result_limit
        items = reader.get_recent_items(
            since=since.strftime("%Y-%m-%d %H:%M:%S"),
            sort=sort_field,
            limit=fetch_limit,
        )
        has_more = len(items) > result_limit
        if has_more:
            items = items[:result_limit]
        if stream:
            total = len(items) + 1 if has_more else len(items)
            typer.echo(stream_items(items, detail=ctx.obj.get("detail", "standard"), total=total))
            return
        json_output = ctx.obj.get("json", False)
        if not items and not json_output:
            typer.echo(f"No items {'modified' if modified else 'added'} in the last {days} day(s).", err=True)
            return
        typer.echo(format_items(items, output_json=json_output, detail=ctx.obj.get("detail", "standard")))
    finally:
        reader.close()


@app.command()
def read(ctx: typer.Context, key: str = typer.Argument(help="Zotero 条目键。")) -> None:
    """查看条目元数据、摘要和笔记。"""
    reader, _config = _create_reader(ctx)
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=ctx.obj.get("json", False),
                hint="Run 'zot zotero search' to find valid item keys",
                context="zotero read",
            )
        notes = reader.get_notes(key)
        typer.echo(
            format_item_detail(
                item,
                notes,
                output_json=ctx.obj.get("json", False),
                detail=ctx.obj.get("detail", "standard"),
            )
        )
    finally:
        reader.close()


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

    from zotero_cli.zotero.access.idempotency import (
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
        extra_fields, resolve_warning = resolve_item_metadata(doi, resolver=resolve_doi)
        if extra_fields:
            resolved_summary = summarize_item_metadata(extra_fields)
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
    """调用领域层的 PDF 添加流程，并把它翻译成命令行输出与退出码。"""
    try:
        result = add_item_from_pdf(
            pdf_path,
            library_id=library_id,
            api_key=api_key,
            library_type=library_type,
            doi_override=doi_override,
            resolve=resolve,
            idempotency_key=idempotency_key,
            metadata_resolver=resolve_doi,
            writer_factory=ZoteroWriter,
        )
    except AddFromPdfError as error:
        emit_error(
            error.code,
            str(error),
            output_json=json_out,
            retryable=error.retryable,
            committed=error.committed,
            hint=error.hint,
            context="add",
        )

    if result.cached_envelope is not None:
        if json_out:
            typer.echo(json.dumps(result.cached_envelope, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Item added: {result.key} (cached).")
        return

    data = result.data_payload()
    if result.attachment_error is not None:
        data["attachment_error"] = str(result.attachment_error)
        data["attachment_error_code"] = result.attachment_error.code
        data["attachment_committed"] = result.attachment_error.committed
        env = envelope_partial_data(data, next=[f"zot zotero attach {result.key} --file {pdf_path}"])
        if json_out:
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Item created: {result.key} (DOI: {result.doi})")
            typer.echo(f"Attachment upload failed: {result.attachment_error}", err=True)
            if idempotency_key:
                typer.echo(
                    "The idempotency key stays reserved because the item already exists; retry the attachment "
                    f"with: zot zotero attach {result.key} --file {pdf_path}",
                    err=True,
                )
            else:
                typer.echo(f"Retry with: zot zotero attach {result.key} --file {pdf_path}", err=True)
        raise SystemExit(exit_code_for(result.attachment_error.code))

    env = envelope_ok(data)
    if result.cache_failed:
        typer.echo("Warning: item was added but the retry result could not be cached", err=True)
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        return
    typer.echo(f"Item created: {result.key} (DOI: {result.doi})")
    summary = result.resolved_summary
    if summary:
        meta = " · ".join(v for v in (summary.get("author"), summary.get("journal"), summary.get("date")) if v)
        typer.echo(f"  {summary.get('title', '')}", err=True)
        if meta:
            typer.echo(f"  {meta}", err=True)
    elif resolve and result.resolve_warning:
        if result.resolve_warning == "no_match":
            typer.echo("Crossref has no record for this DOI — item created without metadata.", err=True)
        else:
            typer.echo(f"Crossref lookup failed ({result.resolve_warning}); item created without metadata.", err=True)
    typer.echo(f"Attachment uploaded: {result.attachment_key}")
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

    def progress(done: int, total: int) -> None:
        emit_progress("progress", phase="batch_add", done=done, total=total)

    def report(message: str) -> None:
        if not json_out:
            typer.echo(message, err=True)

    succeeded, failed = import_items(
        writer, lines, resolve=resolve, metadata_resolver=resolve_doi, progress=progress, report=report
    )

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


@app.command(name="update")
def update_cmd(
    ctx: typer.Context,
    key: str | None = typer.Argument(None, help="条目键；批量更新时不传。"),
    title: str | None = typer.Option(None, help="新标题。"),
    date: str | None = typer.Option(None, help="新日期。"),
    field: list[str] = typer.Option([], help="以 key=value 格式设置字段，可重复。"),
    from_jsonl: Path | None = typer.Option(
        None, "--from-jsonl", exists=True, dir_okay=False, help="从 JSONL 文件批量更新。"
    ),
    add_tags: list[str] = typer.Option([], "--add-tag", help="成功更新后添加标签，可重复。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只预览，不执行更新。"),
    idempotency_key: str | None = typer.Option(None, help="单条写入的幂等键。"),
) -> None:
    """通过 Zotero Web API 更新条目元数据。"""
    config = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    try:
        fields = _parse_inline_fields(title=title, date=date, field=tuple(field))
    except UpdateInputError as error:
        emit_error(
            "validation_error",
            str(error),
            output_json=json_out,
            hint="Use key=value format",
            context="update",
        )
    has_inline_fields = bool(fields)

    if from_jsonl is not None:
        if key is not None:
            emit_error(
                "validation_error",
                "Do not provide ITEMKEY when using --from-jsonl",
                output_json=json_out,
                hint="Use either 'zot zotero update KEY ...' or 'zot zotero update --from-jsonl updates.jsonl'",
                context="update",
            )
        if has_inline_fields:
            emit_error(
                "validation_error",
                "Do not mix --from-jsonl with --title, --date, or --field",
                output_json=json_out,
                hint="Put all field updates inside the JSONL file",
                context="update",
            )
        if idempotency_key:
            emit_error(
                "validation_error",
                "--idempotency-key is not supported with --from-jsonl",
                output_json=json_out,
                hint="Retry batch updates by rerunning the validated JSONL file or use single-item update mode",
                context="update",
            )

        try:
            updates = _load_updates_from_jsonl(from_jsonl)
        except UpdateInputError as error:
            emit_error(
                "validation_error",
                str(error),
                output_json=json_out,
                hint="Provide a UTF-8 JSONL file with one update object per line",
                context="update",
            )
        if dry_run:
            data = {
                "would": {
                    "source": "jsonl",
                    "path": str(from_jsonl),
                    "count": len(updates),
                    "updates": updates,
                    "tags_to_add": list(add_tags),
                }
            }
            if json_out:
                typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
            else:
                typer.echo(f"[dry-run] Would update {len(updates)} item(s) from {from_jsonl}")
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
                context="update",
            )

        emit_progress("start", phase="batch_update", total=len(updates), source=str(from_jsonl))
        writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)

        def progress(done: int, total: int) -> None:
            emit_progress("progress", phase="batch_update", done=done, total=total)

        def report(message: str) -> None:
            if not json_out:
                typer.echo(message, err=True)

        succeeded, failed = update_items(writer, updates, add_tags=add_tags, progress=progress, report=report)

        emit_progress(
            "complete",
            phase="batch_update",
            done=len(updates),
            total=len(updates),
            succeeded=len(succeeded),
            failed=len(failed),
        )
        if json_out:
            env = envelope_partial(
                succeeded,
                failed,
                meta={
                    "total": len(updates),
                    "sync_required": bool(succeeded) or any(row["error"].get("committed") for row in failed),
                },
            )
            if not failed:
                env["ok"] = True
                env["data"] = {"succeeded": succeeded, "failed": [], "tags_added": list(add_tags)}
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"\nDone: {len(succeeded)} updated, {len(failed)} failed", err=True)
            if succeeded:
                typer.echo(SYNC_REMINDER, err=True)
        return

    if key is None:
        emit_error(
            "validation_error",
            "Missing item key",
            output_json=json_out,
            hint="Use 'zot zotero update KEY --title ...' or 'zot zotero update --from-jsonl updates.jsonl'",
            context="update",
        )

    if not fields:
        emit_error(
            "validation_error",
            "No fields to update",
            output_json=json_out,
            hint="Use --title, --date, --field key=value, or --from-jsonl",
            context="update",
        )

    if dry_run:
        data = {"would": {"key": key, "fields": fields, "field_count": len(fields), "tags_to_add": list(add_tags)}}
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
        else:
            typer.echo(f"[dry-run] Would update '{key}' with {len(fields)} field(s): {list(fields.keys())}")
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
            context="update",
        )

    # 写入前预留幂等键，防止响应丢失后盲目重试。
    from zotero_cli.zotero.access.idempotency import (
        PendingMutationError,
        begin_mutation,
        failure_definitely_rejected,
        release_rejected_mutation,
        request_scope,
        store_cached,
    )

    cache_scope = request_scope(
        "update", library_type, str(library_id), {"key": key, "fields": fields, "add_tags": sorted(add_tags)}
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
                hint="Reconcile the remote item before retrying; do not bypass this with a new key",
                context="update",
            )
        except (OSError, sqlite3.Error):
            emit_error(
                "configuration_error",
                "Could not durably reserve the update; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="update",
            )
        if cached is not None:
            typer.echo(json.dumps(cached, indent=2, ensure_ascii=False) if json_out else f"Updated '{key}' (cached).")
            return

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)
    try:
        update_item_with_tags(writer, key, fields, tags=add_tags)
    except ZoteroWriteError as error:
        rejected = False
        if idempotency_key:
            rejected = not error.committed and failure_definitely_rejected(
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
            context="update",
            hint=(
                "The write outcome is unknown. Reconcile the item before retrying; do not reuse this key blindly"
                if error.committed or (idempotency_key and not rejected)
                else f"Failed to update '{key}'"
            ),
        )

    env = envelope_ok(
        {"key": key, "fields": fields, "tags_added": list(add_tags), "sync_required": True},
        next=[f"zot zotero read {key}"],
    )
    if idempotency_key:
        try:
            store_cached(cache_scope, idempotency_key, env)
        except (OSError, sqlite3.Error) as exc:
            typer.echo(f"Warning: item was updated but the retry result could not be cached: {exc}", err=True)
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Updated {len(fields)} field(s) for '{key}'.")
        typer.echo(SYNC_REMINDER, err=True)


PERMANENT_DELETE_WARNING = (
    "Permanently deleted remotely. Zotero's Web API DELETE is irreversible: the item is not moved to "
    "the Zotero trash, and 'zot zotero trash restore' cannot recover it."
)


@app.command(name="delete")
def delete_cmd(
    ctx: typer.Context,
    keys: list[str] = typer.Argument(..., help="要永久删除的条目键。"),
    yes: bool = typer.Option(False, "--yes", help="明确确认永久删除。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览永久删除，不发起写入。"),
    idempotency_key: str | None = typer.Option(None, help="防止结果未知时重复删除的幂等键。"),
) -> None:
    """通过 Web API 永久删除条目，不会移入回收站，也无法恢复。"""
    config = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    if dry_run:
        data = {"would_delete": list(keys), "count": len(keys), "permanent": True}
        if json_out:
            typer.echo(json.dumps(envelope_ok(data, dry_run=True), indent=2, ensure_ascii=False))
        else:
            for key in keys:
                typer.echo(f"[dry-run] Would permanently delete item '{key}' (irreversible)")
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
            context="delete",
        )
    # 必须明确传入 --yes；非交互模式不能代替永久删除确认。
    if not yes:
        if ctx.obj.get("no_interaction", False) or not sys.stdin.isatty():
            emit_error(
                "confirmation_required",
                f"Permanently deleting {len(keys)} item(s) requires --yes on non-interactive stdin",
                output_json=json_out,
                retryable=False,
                hint="Pass --yes to confirm permanent deletion; --dry-run only previews",
                context="delete",
            )
        label = ", ".join(keys)
        if not typer.confirm(f"Permanently delete {len(keys)} item(s): {label}? This cannot be undone."):
            if json_out:
                typer.echo(json.dumps(envelope_ok({"cancelled": True, "permanent": True}), indent=2))
            else:
                typer.echo("Cancelled.", err=True)
            return

    cache_scope = request_scope("delete", library_type, str(library_id), sorted(keys))
    if idempotency_key:
        try:
            cached = begin_mutation(cache_scope, idempotency_key)
        except PendingMutationError as exc:
            emit_error(
                "conflict",
                str(exc),
                output_json=json_out,
                retryable=False,
                hint="Reconcile the remote items before proceeding; do not bypass this with a new key",
                context="delete",
            )
        except (OSError, sqlite3.Error):
            emit_error(
                "configuration_error",
                "Could not durably reserve the permanent delete; no write attempted",
                output_json=json_out,
                retryable=False,
                hint="Check the idempotency state directory and database",
                context="delete",
            )
        if cached is not None:
            if json_out:
                typer.echo(json.dumps(cached, indent=2, ensure_ascii=False))
            else:
                typer.echo(f"Deleted {len(keys)} item(s) permanently (cached).")
            return

    writer = ZoteroWriter(library_id=library_id, api_key=api_key, library_type=library_type)

    def report(row: dict[str, object], is_error: bool) -> None:
        if json_out:
            return
        if is_error:
            typer.echo(f"Error: permanent delete failed for '{row['key']}': {row['message']}", err=True)
        else:
            typer.echo(f"Item '{row['key']}' permanently deleted.")

    result = delete_items(writer, keys, idempotency_key=idempotency_key, report=report)
    succeeded = result.deleted
    failed = result.failed
    rejected_count = result.rejected_count
    retry_blocked = bool(
        idempotency_key and failed and not (rejected_count == len(failed) and all(not f["committed"] for f in failed))
    )
    if idempotency_key and failed and rejected_count == len(failed) and all(not f["committed"] for f in failed):
        try:
            release_rejected_mutation(cache_scope, idempotency_key)
        except (OSError, sqlite3.Error):
            pass

    if not failed:
        env = envelope_ok(
            {"permanently_deleted": succeeded, "permanent": True, "sync_required": True},
        )
        if idempotency_key:
            try:
                store_cached(cache_scope, idempotency_key, env)
            except (OSError, sqlite3.Error) as exc:
                typer.echo(f"Warning: delete result could not be cached: {exc}", err=True)
        if json_out:
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(PERMANENT_DELETE_WARNING, err=True)
            typer.echo(SYNC_REMINDER, err=True)
        return

    first = failed[0]
    exit_code = exit_code_for(first["code"])
    if succeeded:
        env = envelope_partial_data(
            {
                "permanently_deleted": succeeded,
                "failed": failed,
                "permanent": True,
                "sync_required": True,
            },
        )
        if json_out:
            typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Permanently deleted {len(succeeded)} item(s); {len(failed)} failed.", err=True)
        raise SystemExit(exit_code)
    committed_extra = {"committed": True} if first["committed"] else {}
    env = envelope_error(
        first["code"],
        f"Permanent delete failed for {len(failed)} item(s): {first['message']}",
        retryable=False if retry_blocked else bool(first["retryable"]),
        **committed_extra,
        failed=failed,
        hint=(
            "The outcome is unknown for at least one item; reconcile through the Web API before retrying"
            if retry_blocked or any(f["committed"] for f in failed)
            else "Check the item keys and API credentials"
        ),
        context="delete",
    )
    if json_out:
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    raise SystemExit(exit_code)


def _copy_to_clipboard(text: str) -> bool:
    """将文本复制到系统剪贴板。成功时返回 True。"""
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True)
        elif sys.platform == "win32":
            subprocess.run(["clip"], input=text.encode(), check=True)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _get_year(item: Item) -> str:
    """从日期字符串中提取 4 位年份。"""
    if not item.date:
        return "n.d."
    for part in item.date.replace("/", "-").split("-"):
        if len(part) == 4 and part.isdigit():
            return part
    return item.date


def _get_authors_apa(item: Item) -> str:
    """按 APA 样式格式化作者。"""
    authors = [c for c in item.creators if c.creator_type == "author"]
    if not authors:
        return ""
    if len(authors) == 1:
        a = authors[0]
        first_initials = ". ".join(n[0] for n in a.first_name.split() if n) + "." if a.first_name else ""
        return f"{a.last_name}, {first_initials}" if first_initials else a.last_name
    if len(authors) == 2:
        parts = []
        for a in authors:
            first_initials = ". ".join(n[0] for n in a.first_name.split() if n) + "." if a.first_name else ""
            parts.append(f"{a.last_name}, {first_initials}" if first_initials else a.last_name)
        return f"{parts[0]} & {parts[1]}"
    # APA 第 7 版：最多列出 20 位作者；超过 20 位时列出前 19 位和最后一位。
    parts = []
    display = authors if len(authors) <= 20 else authors[:19]
    for a in display:
        first_initials = ". ".join(n[0] for n in a.first_name.split() if n) + "." if a.first_name else ""
        parts.append(f"{a.last_name}, {first_initials}" if first_initials else a.last_name)
    if len(authors) > 20:
        last = authors[-1]
        first_initials = ". ".join(n[0] for n in last.first_name.split() if n) + "." if last.first_name else ""
        last_str = f"{last.last_name}, {first_initials}" if first_initials else last.last_name
        return ", ".join(parts) + ", ... " + last_str
    return ", ".join(parts[:-1]) + ", & " + parts[-1]


def _get_authors_vancouver(item: Item) -> str:
    """按 Vancouver 样式格式化作者。"""
    authors = [c for c in item.creators if c.creator_type == "author"]
    if not authors:
        return ""
    parts = []
    display = authors[:6]
    for a in display:
        initials = "".join(n[0].upper() for n in a.first_name.split() if n) if a.first_name else ""
        parts.append(f"{a.last_name} {initials}" if initials else a.last_name)
    result = ", ".join(parts)
    if len(authors) > 6:
        result += ", et al"
    return result


def _format_apa(item: Item) -> str:
    """按 APA 第 7 版样式格式化引文。"""
    authors = _get_authors_apa(item)
    year = _get_year(item)
    title = item.title or ""
    journal = item.extra.get("publicationTitle", "")
    volume = item.extra.get("volume", "")
    issue = item.extra.get("issue", "")
    pages = item.extra.get("pages", "")
    doi = item.doi

    parts = []
    if authors:
        parts.append(f"{authors} ({year}).")
    else:
        parts.append(f"({year}).")
    parts.append(f"{title}.")
    if journal:
        vol_str = f", {volume}" if volume else ""
        issue_str = f"({issue})" if issue else ""
        pages_str = f", {pages}" if pages else ""
        parts.append(f"{journal}{vol_str}{issue_str}{pages_str}.")
    if doi:
        parts.append(f"https://doi.org/{doi}")

    return " ".join(parts)


def _format_nature(item: Item) -> str:
    """按 Nature 样式格式化引文。"""
    authors = [c for c in item.creators if c.creator_type == "author"]
    if not authors:
        author_str = ""
    else:
        parts = []
        for a in authors:
            initials = " ".join(f"{n[0]}." for n in a.first_name.split() if n) if a.first_name else ""
            parts.append(f"{a.last_name}, {initials}".strip(", ") if initials else a.last_name)
        if len(parts) > 5:
            author_str = ", ".join(parts[:5]) + " et al."
        else:
            author_str = " & ".join([", ".join(parts[:-1]), parts[-1]]) if len(parts) > 1 else parts[0]

    title = item.title or ""
    journal = item.extra.get("publicationTitle", item.extra.get("journalAbbreviation", ""))
    volume = item.extra.get("volume", "")
    pages = item.extra.get("pages", "")
    year = _get_year(item)
    doi = item.doi

    result = f"{author_str} {title}." if author_str else f"{title}."
    if journal:
        result += f" {journal}"
        if volume:
            result += f" **{volume}**"
        if pages:
            result += f", {pages}"
        result += f" ({year})."
    else:
        result += f" ({year})."
    if doi:
        result += f" https://doi.org/{doi}"

    return result


def _format_vancouver(item: Item) -> str:
    """按 Vancouver 样式格式化引文。"""
    authors = _get_authors_vancouver(item)
    title = item.title or ""
    journal = item.extra.get("journalAbbreviation", item.extra.get("publicationTitle", ""))
    year = _get_year(item)
    volume = item.extra.get("volume", "")
    issue = item.extra.get("issue", "")
    pages = item.extra.get("pages", "")
    doi = item.doi

    result = f"{authors}. {title}." if authors else f"{title}."
    if journal:
        result += f" {journal}. {year}"
        if volume:
            result += f";{volume}"
        if issue:
            result += f"({issue})"
        if pages:
            result += f":{pages}"
        result += "."
    else:
        result += f" {year}."
    if doi:
        result += f" doi:{doi}"

    return result


STYLES = {
    "apa": _format_apa,
    "nature": _format_nature,
    "vancouver": _format_vancouver,
}


@app.command(name="cite")
def cite(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键。"),
    style: str = typer.Option("apa", help="引文样式。"),
    no_copy: bool = typer.Option(False, "--no-copy", help="仅打印，不复制到剪贴板。"),
) -> None:
    """格式化引文并复制到剪贴板。

    人类模式会打印引文并复制到系统剪贴板，除非传入 --no-copy。JSON 模式返回
    单个 envelope {citation, style, copied}，且绝不写入剪贴板（对代理程序调用方
    不产生隐藏副作用）。

    \b
    示例：
      zot zotero cite ABC123                    APA 样式（默认，会复制）
      zot zotero cite ABC123 --style nature     Nature 样式
      zot zotero cite ABC123 --style vancouver  Vancouver 样式
      zot zotero cite ABC123 --no-copy          仅打印，不复制
      zot --json zotero cite ABC123             仅输出 envelope，不写剪贴板
    """
    if style not in STYLES:
        raise typer.BadParameter("style 必须是 apa、nature 或 vancouver")
    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    json_out = ctx.obj.get("json", False)
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot zotero search' to find valid item keys",
                context="cite",
            )
        formatter = STYLES[style]
        citation = formatter(item)
        if json_out:
            # 机器模式：只报告引文，不改动剪贴板。
            typer.echo(
                json.dumps(
                    envelope_ok({"citation": citation, "style": style, "copied": False}),
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return
        typer.echo(sanitize_text(citation))
        if not no_copy:
            if _copy_to_clipboard(citation):
                typer.echo("(copied to clipboard)")
            else:
                typer.echo("(clipboard not available)")
    finally:
        reader.close()


@app.command(name="duplicates")
def duplicates(
    ctx: typer.Context,
    strategy: str = typer.Option("both", "--by", help="检测策略：doi、title 或 both。"),
    threshold: float = typer.Option(0.85, help="标题相似度阈值。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
) -> None:
    """查找库中疑似重复的条目。

    \b
    示例：
      zot zotero duplicates
      zot zotero duplicates --by doi
      zot zotero duplicates --by title --threshold 0.9
      zot --json zotero duplicates
    """
    if strategy not in {"doi", "title", "both"}:
        raise typer.BadParameter("by 必须是 doi、title 或 both")
    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        limit = limit if limit is not None else ctx.obj.get("limit", config.default_limit)
        groups = reader.find_duplicates(strategy=strategy, threshold=threshold, limit=limit)
        if not groups:
            if ctx.obj.get("json"):
                typer.echo(json.dumps(envelope_ok([], meta={"count": 0}), indent=2, ensure_ascii=False))
            else:
                typer.echo("No duplicates found.")
            return
        typer.echo(format_duplicates(groups, output_json=ctx.obj.get("json", False)))
    finally:
        reader.close()
    # 通过带类型的退出码表示"检测到重复项"，使代理程序/脚本可以据此分支执行：
    # `if zot zotero duplicates ...; then ...; else act_on_duplicates; fi`。
    ctx.exit(EXIT_CONFLICT)


@app.command(name="export")
def export(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键。"),
    fmt: str = typer.Option("bibtex", "--format", help="导出格式。"),
) -> None:
    """以 BibTeX、CSL-JSON、RIS 或原始 JSON 格式导出引文。

    \b
    示例：
      zot zotero export ABC123                    BibTeX（默认）
      zot zotero export ABC123 --format csl-json  CSL-JSON
      zot zotero export ABC123 --format ris       RIS
      zot zotero export ABC123 --format json      原始 JSON 元数据
    """
    if fmt not in {"bibtex", "csl-json", "ris", "json"}:
        raise typer.BadParameter("format 必须是 bibtex、csl-json、ris 或 json")
    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    json_out = ctx.obj.get("json", False)
    try:
        if fmt == "json":
            item = reader.get_item(key)
            if item is None:
                emit_error(
                    "not_found",
                    f"Item '{key}' not found",
                    output_json=json_out,
                    hint="Run 'zot zotero search' to find valid item keys",
                    context="export",
                )
            data = asdict(item)
            if json_out:
                typer.echo(json.dumps(envelope_ok(data, meta={"format": "json"}), indent=2, ensure_ascii=False))
            else:
                # 文本模式保留原始 JSON 负载，以便直接传递给其他程序。
                typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            result = reader.export_citation(key, fmt=fmt)
            if result is None:
                emit_error(
                    "not_found",
                    f"Item '{key}' not found",
                    output_json=json_out,
                    hint="Run 'zot zotero search' to find valid item keys",
                    context="export",
                )
            if json_out:
                typer.echo(
                    json.dumps(
                        envelope_ok({"format": fmt, "citation": result}, meta={"format": fmt}),
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            else:
                typer.echo(sanitize_text(result))
    finally:
        reader.close()


_HTTP_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


_ANY_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


_DOI_RE = re.compile(r"^doi:", re.IGNORECASE)


def _open_path(path: str) -> None:
    """用系统默认程序打开文件或 URL。

    每个平台只使用一条固定、纯参数的调用（不经 shell）：macOS 用 "open"，
    Linux 用 "xdg-open"，Windows 用原生 os.startfile API（其参数是文件名而非
    命令字符串，因此不涉及 shell 引号处理）。
    """
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=True)
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # Windows-only API
    else:
        subprocess.run(["xdg-open", path], check=True)


def _emit_and_open(target: str, *, kind: str, output_json: bool) -> None:
    """报告目标（envelope 或人类可读行）并启动默认应用。"""
    if output_json:
        typer.echo(json.dumps(envelope_ok({"target": target, "kind": kind, "gui": True}), indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Opening {sanitize_text(target)}")
    _open_path(target)


@app.command(name="open")
def open_item(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键或 http(s) URL。"),
    open_url: bool = typer.Option(False, "--url", help="在浏览器中打开条目或指定的 URL。"),
) -> None:
    """用默认应用打开 Zotero 条目的 PDF 或 URL。

    KEY 也可以是 http(s) URL，此时直接打开该地址
    （例如 zot zotero open --url https://example.com）。此命令会启动系统默认
    应用，即带有 GUI 副作用；JSON 模式返回单个 envelope {target, kind, gui}
    描述所打开的目标。

    \b
    示例：
      zot zotero open ABC123                    用默认阅读器打开 PDF
      zot zotero open ABC123 --url              在浏览器中打开 DOI/URL
      zot zotero open --url https://example.com 直接打开网页 URL
      zot --json zotero open ABC123             envelope + 打开（GUI 副作用）
    """
    json_out = ctx.obj.get("json", False)

    # 直接支持网页 URL：过去 "zot zotero open --url https://example.com" 会把
    # URL 当作条目键。此处仅接受 http(s)。
    if _HTTP_URL_RE.match(key):
        _emit_and_open(key, kind="url", output_json=json_out)
        return
    if _ANY_SCHEME_RE.match(key):
        emit_error(
            "validation_error",
            f"Unsupported URL scheme in '{key}'; only http(s) URLs are supported",
            output_json=json_out,
            hint="Use 'zot zotero open <item-key>' for library items",
            context="open",
        )
    if _DOI_RE.match(key):
        _emit_and_open(f"https://doi.org/{key[4:]}", kind="url", output_json=json_out)
        return

    config = load_config(profile=ctx.obj.get("profile"))
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(config))
    try:
        item = reader.get_item(key)
        if item is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot zotero search' to find valid item keys",
                context="open",
            )

        if open_url:
            target = item.url or item.doi
            if item.doi and not item.url:
                target = f"https://doi.org/{item.doi}"
            if not target:
                emit_error(
                    "not_found",
                    f"No URL or DOI for item '{key}'",
                    output_json=json_out,
                    context="open",
                )
            _emit_and_open(target, kind="url", output_json=json_out)
            return

        # 默认：打开 PDF
        att = reader.get_pdf_attachment(key)
        if att is None:
            emit_error(
                "not_found",
                f"No PDF attachment for '{key}'",
                output_json=json_out,
                hint="Use --url to open the item URL instead",
                context="open",
            )
        pdf_path = att.path
        if not pdf_path or not pdf_path.exists():
            emit_error(
                "not_found",
                f"PDF file not found at {pdf_path or att.filename}",
                output_json=json_out,
                context="open",
            )
        _emit_and_open(str(pdf_path), kind="pdf", output_json=json_out)
    finally:
        reader.close()


@app.command(name="relate")
def relate(
    ctx: typer.Context,
    key: str = typer.Argument(help="Zotero 条目键。"),
    limit: int | None = typer.Option(None, help="覆盖全局结果上限。"),
) -> None:
    """通过共享标签、集合或显式关联查找相关条目。

    \b
    示例：
      zot zotero relate ABC123
      zot --json zotero relate ABC123
    """
    config = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    data_dir = get_data_dir(config)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)
    try:
        if reader.get_item(key) is None:
            emit_error(
                "not_found",
                f"Item '{key}' not found",
                output_json=json_out,
                hint="Run 'zot zotero search' to find valid item keys",
                context="relate",
            )
        limit = limit if limit is not None else ctx.obj.get("limit", 20)
        items = reader.get_related_items(key, limit=limit)
        if not items:
            # 空结果是正常情况而非错误——以退出码 0 结束并给出友好提示。
            if json_out:
                typer.echo(json.dumps(envelope_ok([], meta={"count": 0}), indent=2, ensure_ascii=False))
            else:
                typer.echo(
                    f"No related items found for '{key}'. Items need shared tags or collections to find relations."
                )
            return
        detail = ctx.obj.get("detail", "standard")
        typer.echo(format_items(items, output_json=json_out, detail=detail))
    finally:
        reader.close()
