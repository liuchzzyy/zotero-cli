"""通过 Semantic Scholar 检查并更新预印本的发表状态。"""

from __future__ import annotations

import json
from typing import Any

import click

from zotero_cli.config import (
    get_data_dir,
    load_config,
    resolve_library_id,
    resolve_semantic_scholar_api_key,
    resolve_write_credentials,
)
from zotero_cli.metadata.publication_status import (
    PreprintInfo,
    SemanticScholarClient,
    SemanticScholarError,
    extract_preprint_info,
)
from zotero_cli.output.envelope import envelope_ok, envelope_partial_data
from zotero_cli.output.exit_codes import EXIT_NETWORK, EXIT_RUNTIME, emit_error
from zotero_cli.output.sanitize import sanitize_text
from zotero_cli.zotero.reader import ZoteroReader
from zotero_cli.zotero.writer import SYNC_REMINDER, ZoteroWriter
from zotero_cli.zotero.writer_errors import ZoteroWriteError

_NETWORK_FAMILY_CODES = frozenset({"network_error", "rate_limited"})


def _write_failure_exit_code(failure_codes: list[str]) -> int:
    """当所有写入失败都属于网络/限流时返回退出码 5，否则返回运行时错误码（1）。"""
    if failure_codes and all(code in _NETWORK_FAMILY_CODES for code in failure_codes):
        return EXIT_NETWORK
    return EXIT_RUNTIME


def update_status(
    ctx: click.Context,
    key: str | None,
    apply: bool,
    dry_run: bool,
    ss_api_key: str | None,
    collection: str | None,
    limit: int | None,
) -> None:
    """检查预印本（arXiv、bioRxiv、medRxiv）是否已正式发表。

    使用 Semantic Scholar API 查询发表状态。
    默认以预览模式运行（也可用 --dry-run 显式指定）；--apply 是唯一会把
    发现的元数据写回 Zotero 的方式。

    \b
    API key（可选，可提高速率上限）：
      --api-key KEY                                直接传入
      .zot/config.toml: semantic_scholar_api_key    在仓库本地的 zot 配置中设置
    申请地址：https://www.semanticscholar.org/product/api#api-key-form

    \b
    示例：
      zot metadata update-status                    # 检查所有预印本（预览）
      zot metadata update-status --dry-run          # 显式预览，绝不写入
      zot metadata update-status --apply            # 在 Zotero 中更新已发表条目
      zot metadata update-status ABC123             # 检查单个条目
      zot metadata update-status --collection "NLP" # 检查某分类下的条目
      zot metadata update-status --limit 10         # 最多检查 10 个条目
    """
    if apply and dry_run:
        emit_error(
            "validation_error",
            "--apply and --dry-run are mutually exclusive",
            output_json=ctx.obj.get("json", False),
            hint="Use --dry-run to preview; omit it with --apply to write",
            context="update-status",
        )
    cfg = load_config(profile=ctx.obj.get("profile"))
    json_out = ctx.obj.get("json", False)
    limit = limit if limit is not None else ctx.obj.get("limit", 50)

    api_key = resolve_semantic_scholar_api_key(cfg, explicit=ss_api_key)
    if not api_key:
        rate_msg = "No API key — rate limited to ~1 request/3s. Set semantic_scholar_api_key in .zot/config.toml."
        if not json_out:
            click.echo(rate_msg, err=True)

    # 从本地数据库读取条目
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, ctx.obj)
    reader = ZoteroReader(db_path, library_id=library_id)

    try:
        if key:
            item = reader.get_item(key)
            if not item:
                emit_error(
                    "not_found",
                    f"Item '{key}' not found.",
                    output_json=json_out,
                    hint="Run 'zot zotero search' to find valid item keys",
                    context="update-status",
                )
            items = [item]
        else:
            try:
                items = reader.get_arxiv_preprints(collection=collection, limit=limit)
            except ValueError as e:
                emit_error(
                    "not_found",
                    str(e),
                    output_json=json_out,
                    hint="Run 'zot zotero collection list' to find valid collection keys",
                    context="update-status",
                )
    finally:
        reader.close()

    if not items:
        if json_out:
            data = {"results": [], "published": 0, "checked": 0, "updated": 0, "sync_required": False}
            click.echo(
                json.dumps(envelope_ok(data, meta={"count": 0}, dry_run=not apply), indent=2, ensure_ascii=False)
            )
        else:
            click.echo("No preprints found.")
        return

    # 提取预印本标识（arXiv、bioRxiv、medRxiv）
    preprint_items: list[tuple[str, PreprintInfo, str]] = []  # (条目键, 信息, 标题)
    for item in items:
        info = extract_preprint_info(
            url=item.url,
            doi=item.doi,
            extra=item.extra.get("extra") if item.extra else None,
        )
        if info:
            preprint_items.append((item.key, info, item.title))

    if not preprint_items:
        if json_out:
            data = {"results": [], "published": 0, "checked": 0, "updated": 0, "sync_required": False}
            click.echo(
                json.dumps(envelope_ok(data, meta={"count": 0}, dry_run=not apply), indent=2, ensure_ascii=False)
            )
        else:
            click.echo("No items with preprint IDs found.")
        return

    # 按来源统计
    arxiv_count = sum(1 for _, info, _ in preprint_items if info.source == "arxiv")
    biorxiv_count = len(preprint_items) - arxiv_count

    if not json_out:
        parts = []
        if arxiv_count:
            parts.append(f"{arxiv_count} arXiv")
        if biorxiv_count:
            parts.append(f"{biorxiv_count} bioRxiv/medRxiv")
        click.echo(f"Checking {' + '.join(parts)} preprint(s)...")
        if not api_key:
            est_time = len(preprint_items) * 3
            click.echo(f"Estimated time: ~{est_time}s (use API key to speed up)")
        click.echo()

    # 查询 Semantic Scholar
    client = SemanticScholarClient(api_key=api_key or None)
    results: list[dict] = []
    published_count = 0

    try:
        for i, (item_key, info, title) in enumerate(preprint_items):
            if not json_out:
                label = f"[{i + 1}/{len(preprint_items)}]"
                safe_title = sanitize_text(title)
                short_title = safe_title[:60] + ("..." if len(safe_title) > 60 else "")
                click.echo(f"{label} {short_title}", nl=False)

            try:
                status = client.check_publication(info)
            except SemanticScholarError as exc:
                emit_error(
                    "rate_limited" if exc.rate_limited else "network_error",
                    str(exc),
                    output_json=json_out,
                    retryable=exc.retryable,
                    hint="Retry later or configure a semantic_scholar_api_key to raise rate limits",
                    context="update-status",
                )

            if status and status.is_published:
                published_count += 1
                result = {
                    "key": item_key,
                    "preprint_id": info.preprint_id,
                    "source": info.source,
                    "title": title,
                    "published": True,
                    "venue": status.venue,
                    "journal": status.journal_name,
                    "doi": status.doi,
                    "date": status.publication_date,
                }
                results.append(result)
                if not json_out:
                    venue = status.venue or status.journal_name or "Unknown venue"
                    click.echo(f" → Published in {sanitize_text(venue)}")
            else:
                result = {
                    "key": item_key,
                    "preprint_id": info.preprint_id,
                    "source": info.source,
                    "title": title,
                    "published": False,
                }
                results.append(result)
                if not json_out:
                    if status is None:
                        click.echo(" → Not found on Semantic Scholar")
                    else:
                        click.echo(" → Not yet published")
    finally:
        client.close()

    updated = 0
    update_failed: list[dict[str, Any]] = []

    if not json_out:
        click.echo()
        click.echo(f"Found {published_count}/{len(preprint_items)} published paper(s).")

    if apply and published_count > 0:
        # 通过 Zotero Web API 应用更新
        library_type = ctx.obj.get("library_type", "user")
        group_id = ctx.obj.get("group_id")
        zot_library_id, zot_api_key = resolve_write_credentials(cfg, library_type=library_type, group_id=group_id)

        if not zot_library_id or not zot_api_key:
            emit_error(
                "auth_missing",
                "Zotero write credentials not configured",
                output_json=json_out,
                hint="Run 'zot config init' to set up API credentials",
                context="update-status",
            )

        writer = ZoteroWriter(library_id=zot_library_id, api_key=zot_api_key, library_type=library_type)
        for r in results:
            if not r["published"]:
                continue
            fields: dict[str, str] = {}
            if r.get("doi"):
                fields["DOI"] = r["doi"]
            if r.get("venue"):
                fields["publicationTitle"] = r["venue"]
            elif r.get("journal"):
                fields["publicationTitle"] = r["journal"]
            if r.get("date"):
                fields["date"] = r["date"]
            if not fields:
                continue
            try:
                writer.update_item(r["key"], fields)
                r["updated"] = True
                updated += 1
                if not json_out:
                    click.echo(f"  Updated {r['key']}: {sanitize_text(r['title'])[:50]}...")
            except ZoteroWriteError as e:
                error = {"code": e.code, "message": str(e), "retryable": e.retryable}
                r["updated"] = False
                r["update_error"] = error
                update_failed.append({"key": r["key"], "error": error})
                if not json_out:
                    click.echo(f"  Failed {r['key']}: {e}", err=True)

    data = {
        "results": results,
        "published": published_count,
        "checked": len(preprint_items),
        "updated": updated,
        "update_failed": update_failed,
        "sync_required": updated > 0,
    }
    failure_codes = [str(failure["error"].get("code", "")) for failure in update_failed]
    if json_out:
        if apply and update_failed:
            # 部分成功：部分写入可能已失败；此时绝不能返回 ok=True。
            env = envelope_partial_data(data, meta={"count": len(results)})
        else:
            env = envelope_ok(data, meta={"count": len(results)}, dry_run=not apply)
        click.echo(json.dumps(env, indent=2, ensure_ascii=False))
        if apply and update_failed:
            raise SystemExit(_write_failure_exit_code(failure_codes))
        return

    if published_count == 0:
        return

    if not apply:
        click.echo("\nDry-run mode. Use --apply to update Zotero metadata.")
        return

    click.echo(f"\nUpdated {updated} item(s).")
    if updated > 0:
        click.echo(SYNC_REMINDER)
    if update_failed:
        click.echo(f"{len(update_failed)} item(s) failed to update.", err=True)
        raise SystemExit(_write_failure_exit_code(failure_codes))
