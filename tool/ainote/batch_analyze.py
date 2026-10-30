"""Batch AI note analysis for a Zotero collection or items.

Workflow:
1. Inventory target collection or items via ZoteroReader.
2. Check local PDF availability and MinerU parse cache.
3. Call analyze_item sequentially with proper error handling and logging.
4. Record per-item status in a persistent JSON state file for safe resume.
5. Provide summary of processed, skipped, and failed items.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from zotero_cli.ai_notes.access.client import AiClient
from zotero_cli.ai_notes.analysis import analyze_item
from zotero_cli.ai_notes.constants import ANALYZED_TAG, KEYWORDS_TAG, NOT_ANALYZED_TAG
from zotero_cli.ai_notes.errors import NoteAnalysisError
from zotero_cli.config import (
    get_data_dir,
    get_prefs_js_path,
    load_ai_note_config,
    load_config,
    resolve_library_id,
    resolve_write_credentials,
)
from zotero_cli.config import (
    state_dir as default_state_dir,
)
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.access.writer import ZoteroWriter
from zotero_cli.zotero.models import Collection, Item

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_ainote")
_DONE_STATUSES = {"ok", "already_analyzed", "uncertain"}
# 缺 ai/keywords 的条目只补关键词；同一最多尝试该次数，避免反复失败。
KEYWORD_BACKFILL_ATTEMPTS = 2


def resolve_collection_key(reader: ZoteroReader, name_or_key: str) -> str | None:
    """Resolve a stable key or a unique case-insensitive collection name."""
    collections: list[Collection] = []

    def collect(nodes: list[Collection]) -> None:
        for node in nodes:
            collections.append(node)
            collect(node.children)

    collect(reader.get_collections())
    for collection in collections:
        if collection.key == name_or_key:
            return str(collection.key)
    matches = [
        str(collection.key) for collection in collections if collection.name.casefold() == name_or_key.casefold()
    ]
    if len(matches) > 1:
        raise ValueError(f"Collection name is ambiguous; use a key: {', '.join(matches)}")
    return str(matches[0]) if matches else None


def get_collection_items_recursive(reader: ZoteroReader, root_key: str) -> list[Item]:
    collections = reader.get_collections()

    def _find_node(cols: list[Collection], key: str) -> Collection | None:
        for c in cols:
            if c.key == key:
                return c
            found = _find_node(c.children, key)
            if found:
                return found
        return None

    node = _find_node(collections, root_key)
    target_keys = [root_key]
    if node:

        def _collect(n: Collection) -> None:
            for ch in n.children:
                target_keys.append(ch.key)
                _collect(ch)

        _collect(node)

    seen = set()
    items = []
    for k in target_keys:
        for it in reader.get_collection_items(k):
            if it.key not in seen:
                seen.add(it.key)
                items.append(it)
    return items


def load_state(state_file: Path) -> dict[str, Any]:
    """Load a checkpoint without silently discarding duplicate-write protection."""
    if not state_file.exists():
        return {}
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot read checkpoint {state_file}: {exc}") from exc
    if not isinstance(state, dict) or any(not isinstance(value, dict) for value in state.values()):
        raise ValueError(f"Invalid checkpoint item mapping: {state_file}")
    return state


def save_state(state_file: Path, state: dict[str, Any]) -> None:
    """Persist the checkpoint atomically with a per-writer unique temp file.

    A fixed `<state>.tmp` is shared by concurrent runs of the same collection:
    they interleave JSON into one temp file and rename the mixture into place.
    Unique names plus `replace` keep the checkpoint either the previous or a
    complete new value; a failed write removes its temp file.
    """
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = state_file.with_name(f"{state_file.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        temp_file.replace(state_file)
    except BaseException:
        temp_file.unlink(missing_ok=True)
        raise


def run_batch(
    collection_name_or_key: str,
    *,
    recursive: bool = True,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
    no_tag: bool = False,
    no_short_note: bool = False,
    state_path: Path | None = None,
) -> dict[str, Any]:
    cfg = load_config()
    ai_cfg = load_ai_note_config()
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_id = resolve_library_id(db_path, {})
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(cfg))

    try:
        col_key = resolve_collection_key(reader, collection_name_or_key)
        if not col_key:
            raise ValueError(f"Collection '{collection_name_or_key}' not found.")

        if recursive:
            items = get_collection_items_recursive(reader, col_key)
            logger.info(f"Collection '{collection_name_or_key}' ({col_key}) [recursive] has {len(items)} items.")
        else:
            items = reader.get_collection_items(col_key)
            logger.info(f"Collection '{collection_name_or_key}' ({col_key}) has {len(items)} items.")

        writer = None
        if not dry_run:
            w_id, w_key = resolve_write_credentials(cfg)
            if not w_id or not w_key:
                raise RuntimeError("Zotero Web API write credentials missing.")
            writer = ZoteroWriter(library_id=w_id, api_key=w_key)

        ai_client = AiClient(ai_cfg)

        if state_path is None:
            state_path = default_state_dir() / f"batch_ainote_{col_key}.json"
        state = load_state(state_path)

        def checkpoint() -> None:
            # A dry run must have zero side effects, so it keeps its preview
            # results in memory only; a later apply is not skipped by them.
            if not dry_run:
                save_state(state_path, state)

        processed_count = 0
        notes_created_count = 0
        uncertain_count = 0
        skipped_count = 0
        blocked_count = 0
        keyword_backfilled_count = 0
        keyword_failed_count = 0
        keyword_blocked_count = 0
        keyword_candidates_count = 0
        attempted_count = 0
        failed_count = 0
        failure_reasons: dict[str, int] = {}

        for idx, item in enumerate(items, 1):
            if limit is not None and attempted_count >= limit:
                logger.info(f"Reached processing limit of {limit} items.")
                break

            key = item.key
            title = (item.title or "").replace("\n", " ")
            prev_result = state.get(key, {})

            def _progress(event: str, msg: str, _key: str = key) -> None:
                logger.info(f"  [{_key}][{event}] {msg}")

            if not force and prev_result.get("status") in {"blocked", "keyword_in_progress"}:
                logger.warning(
                    f"[{idx}/{len(items)}] Blocked {key} (prior write outcome uncertain); reconcile remotely."
                )
                blocked_count += 1
                continue
            if (
                not force
                and prev_result.get("status") == "keyword_backfilled"
                and prev_result.get("short_note") == "ok"
            ):
                logger.info(f"[{idx}/{len(items)}] Skip {key} (keywords already backfilled): {title[:40]}")
                skipped_count += 1
                continue
            raw_item = reader.get_item(key)
            item_tags = raw_item.tags if raw_item is not None else []

            if not force and not no_short_note and ANALYZED_TAG in item_tags and KEYWORDS_TAG not in item_tags:
                # 已完成分析但缺关键词：只补 Extra 与标签，绝不重跑完整分析。
                keyword_attempts = int(prev_result.get("keyword_attempts", 0))
                if keyword_attempts >= KEYWORD_BACKFILL_ATTEMPTS:
                    logger.warning(f"[{idx}/{len(items)}] Keyword blocked {key} after {keyword_attempts} attempts")
                    state[key] = {
                        **prev_result,
                        "status": "keyword_blocked",
                        "title": title,
                        "updated_at": time.time(),
                    }
                    keyword_blocked_count += 1
                    checkpoint()
                    continue
                logger.info(f"[{idx}/{len(items)}] Backfill keywords {key}: {title[:40]}")
                attempted_count += 1
                if not dry_run:
                    state[key] = {
                        **prev_result,
                        "status": "keyword_in_progress",
                        "title": title,
                        "updated_at": time.time(),
                    }
                    checkpoint()
                try:
                    keyword_result = analyze_item(
                        reader=reader,
                        writer=writer,
                        ai_client=ai_client,
                        key=key,
                        short_note_only=True,
                        dry_run=dry_run,
                        progress=_progress,
                    )
                except NoteAnalysisError as exc:
                    attempts = keyword_attempts + 1
                    status = (
                        "blocked"
                        if exc.committed
                        else ("keyword_blocked" if attempts >= KEYWORD_BACKFILL_ATTEMPTS else "keyword_failed")
                    )
                    state[key] = {
                        "status": status,
                        "keyword_attempts": attempts,
                        "error": str(exc),
                        "code": exc.code,
                        "title": title,
                        "updated_at": time.time(),
                    }
                    if exc.committed:
                        blocked_count += 1
                    elif status == "keyword_blocked":
                        keyword_blocked_count += 1
                    else:
                        keyword_failed_count += 1
                    checkpoint()
                else:
                    short_note_status = keyword_result.get("short_note")
                    if dry_run:
                        keyword_candidates_count += 1
                    elif short_note_status == "ok":
                        state[key] = {
                            "status": "keyword_backfilled",
                            "short_note": short_note_status,
                            "keyword_attempts": keyword_attempts + 1,
                            "title": title,
                            "updated_at": time.time(),
                        }
                        keyword_backfilled_count += 1
                        checkpoint()
                    else:
                        attempts = keyword_attempts + 1
                        status = "keyword_blocked" if attempts >= KEYWORD_BACKFILL_ATTEMPTS else "keyword_failed"
                        state[key] = {
                            "status": status,
                            "short_note": short_note_status,
                            "keyword_attempts": attempts,
                            "error": keyword_result.get("short_note_error", "关键词回填未成功"),
                            "title": title,
                            "updated_at": time.time(),
                        }
                        if status == "keyword_blocked":
                            keyword_blocked_count += 1
                        else:
                            keyword_failed_count += 1
                        checkpoint()
                continue

            if not force and prev_result.get("status") in _DONE_STATUSES and not prev_result.get("dry_run"):
                logger.info(f"[{idx}/{len(items)}] Skip {key} (previously completed): {title[:40]}")
                skipped_count += 1
                continue

            if not force and {ANALYZED_TAG, NOT_ANALYZED_TAG}.intersection(item_tags):
                logger.info(f"[{idx}/{len(items)}] Skip {key} (already tagged {ANALYZED_TAG}): {title[:40]}")
                state[key] = {
                    "status": "already_analyzed" if ANALYZED_TAG in item_tags else "uncertain",
                    "title": title,
                    "updated_at": time.time(),
                }
                checkpoint()
                skipped_count += 1
                continue

            pdfs = reader.get_pdf_attachments(key)
            if not pdfs:
                logger.warning(f"[{idx}/{len(items)}] {key} has no PDF attachments, skipping.")
                state[key] = {"status": "error", "error": "no_pdf", "title": title}
                failed_count += 1
                failure_reasons["no_pdf"] = failure_reasons.get("no_pdf", 0) + 1
                checkpoint()
                continue

            mode_label = "dry-run " if dry_run else ""
            logger.info(f"[{idx}/{len(items)}] Processing {mode_label}{key} ({len(pdfs)} PDFs): {title[:50]}")

            attempted_count += 1
            try:
                res = analyze_item(
                    reader=reader,
                    writer=writer,
                    ai_client=ai_client,
                    key=key,
                    force=force,
                    no_tag=no_tag,
                    dry_run=dry_run,
                    no_short_note=no_short_note,
                    progress=_progress,
                )
                state[key] = {
                    "status": res.get("status"),
                    "dry_run": dry_run,
                    "paper_type": res.get("paper_type"),
                    "note_key": res.get("note_key"),
                    "sections": res.get("sections"),
                    "short_note": res.get("short_note"),
                    "title": title,
                    "updated_at": time.time(),
                }
                checkpoint()
                processed_count += 1
                if res.get("status") == "ok":
                    notes_created_count += 1
                elif res.get("status") == "uncertain":
                    uncertain_count += 1
                suffix = " (dry-run, not persisted)" if dry_run else ""
                logger.info(f"  [{key}] Finished with status: {res.get('status')}{suffix}")
            except NoteAnalysisError as exc:
                logger.error(f"  [{key}] Analysis error: {exc}")
                state[key] = {
                    "status": "blocked" if exc.committed else "failed",
                    "error": str(exc),
                    "code": exc.code,
                    "committed": exc.committed,
                    "title": title,
                }
                if exc.committed:
                    blocked_count += 1
                else:
                    failed_count += 1
                    reason = str(exc).split("（", 1)[0].split("；", 1)[0]
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                checkpoint()
            except Exception as exc:
                logger.error(f"  [{key}] Unexpected error: {exc}")
                state[key] = {"status": "failed", "error": str(exc), "code": "unexpected", "title": title}
                failed_count += 1
                reason = f"unexpected:{type(exc).__name__}"
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                checkpoint()

        return {
            "collection_key": col_key,
            "total": len(items),
            "processed": processed_count,
            "notes_created": notes_created_count,
            "uncertain": uncertain_count,
            "skipped": skipped_count,
            "blocked": blocked_count,
            "keyword_backfilled": keyword_backfilled_count,
            "keyword_failed": keyword_failed_count,
            "keyword_blocked": keyword_blocked_count,
            "keyword_candidates": keyword_candidates_count,
            "attempted": attempted_count,
            "failed": failed_count,
            "failure_reasons": failure_reasons,
            "dry_run": dry_run,
            "state_path": str(state_path),
        }
    finally:
        reader.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch AI note analysis for a Zotero collection")
    parser.add_argument("--collection", required=True, help="Collection name or key to process")
    parser.add_argument("--no-recursive", action="store_true", help="Do not include items from subcollections")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview analysis without Zotero writes or checkpoint changes (still parses PDFs via MinerU)",
    )
    mode.add_argument("--apply", action="store_true", help="Execute writeback to Zotero Web API")
    parser.add_argument("--force", action="store_true", help="Re-analyze items even if already tagged ai/noted")
    parser.add_argument("--limit", type=int, default=None, help="Limit analysis and keyword-backfill attempts")
    parser.add_argument("--no-tag", action="store_true", help="Do not apply ai/noted tag")
    parser.add_argument("--no-short-note", action="store_true", help="Do not generate Extra short-note")

    args = parser.parse_args()

    summary = run_batch(
        args.collection,
        recursive=not args.no_recursive,
        dry_run=args.dry_run,
        force=args.force,
        limit=args.limit,
        no_tag=args.no_tag,
        no_short_note=args.no_short_note,
    )
    logger.info(f"Batch run complete: {summary}")


if __name__ == "__main__":
    main()
