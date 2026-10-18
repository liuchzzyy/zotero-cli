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
import time
from pathlib import Path
from typing import Any

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
from zotero_cli.core.notes.client import AiClient
from zotero_cli.core.notes.constants import ANALYZED_TAG, NOT_ANALYZED_TAG
from zotero_cli.core.notes.errors import NoteAnalysisError
from zotero_cli.core.notes.pipeline import analyze_item
from zotero_cli.core.zotero.reader import ZoteroReader
from zotero_cli.core.zotero.writer import ZoteroWriter
from zotero_cli.models import Collection, Item

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_ainote")
_DONE_STATUSES = {"ok", "already_analyzed", "uncertain"}


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
            return collection.key
    matches = [collection.key for collection in collections if collection.name.casefold() == name_or_key.casefold()]
    if len(matches) > 1:
        raise ValueError(f"Collection name is ambiguous; use a key: {', '.join(matches)}")
    return matches[0] if matches else None


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
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = state_file.with_suffix(".tmp")
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    temp_file.replace(state_file)


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
        checkpoint_exists = state_path.exists()
        state = load_state(state_path)

        def checkpoint() -> None:
            if not dry_run or not checkpoint_exists:
                save_state(state_path, state)

        # Pre-populate known items from other state files if available
        item_keys = {item.key for item in items}
        for existing_file in state_path.parent.glob("batch_ainote_*.json"):
            if existing_file == state_path:
                continue
            try:
                existing_data = load_state(existing_file)
            except ValueError as exc:
                logger.warning("Ignoring sibling checkpoint: %s", exc)
                continue
            for k, v in existing_data.items():
                if k in item_keys and k not in state and v.get("status") in _DONE_STATUSES and not v.get("dry_run"):
                    state[k] = v

        processed_count = 0
        attempted_count = 0

        for idx, item in enumerate(items, 1):
            if limit is not None and attempted_count >= limit:
                logger.info(f"Reached processing limit of {limit} items.")
                break

            key = item.key
            title = (item.title or "").replace("\n", " ")
            prev_result = state.get(key, {})

            if not force and prev_result.get("status") in _DONE_STATUSES and not prev_result.get("dry_run"):
                logger.info(f"[{idx}/{len(items)}] Skip {key} (previously completed): {title[:40]}")
                continue

            raw_item = reader.get_item(key)
            item_tags = raw_item.tags if raw_item is not None else []
            if not force and {ANALYZED_TAG, NOT_ANALYZED_TAG}.intersection(item_tags):
                logger.info(f"[{idx}/{len(items)}] Skip {key} (already tagged {ANALYZED_TAG}): {title[:40]}")
                state[key] = {
                    "status": "already_analyzed" if ANALYZED_TAG in item_tags else "uncertain",
                    "title": title,
                    "updated_at": time.time(),
                }
                checkpoint()
                continue

            pdfs = reader.get_pdf_attachments(key)
            if not pdfs:
                logger.warning(f"[{idx}/{len(items)}] {key} has no PDF attachments, skipping.")
                state[key] = {"status": "error", "error": "no_pdf", "title": title}
                checkpoint()
                continue

            logger.info(f"[{idx}/{len(items)}] Processing {key} ({len(pdfs)} PDFs): {title[:50]}")

            def _progress(event: str, msg: str) -> None:
                logger.info(f"  [{key}][{event}] {msg}")

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
                logger.info(f"  [{key}] Finished with status: {res.get('status')}")
            except NoteAnalysisError as exc:
                logger.error(f"  [{key}] Analysis error: {exc}")
                state[key] = {"status": "failed", "error": str(exc), "code": exc.code, "title": title}
                checkpoint()
            except Exception as exc:
                logger.error(f"  [{key}] Unexpected error: {exc}")
                state[key] = {"status": "failed", "error": str(exc), "code": "unexpected", "title": title}
                checkpoint()

        return {
            "collection_key": col_key,
            "total": len(items),
            "processed": processed_count,
            "attempted": attempted_count,
            "state_path": str(state_path),
        }
    finally:
        reader.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch AI note analysis for a Zotero collection")
    parser.add_argument("--collection", default="AA", help="Collection name or key (default: AA)")
    parser.add_argument("--no-recursive", action="store_true", help="Do not include items from subcollections")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview classification and prompt without LLM write")
    mode.add_argument("--apply", action="store_true", help="Execute writeback to Zotero Web API")
    parser.add_argument("--force", action="store_true", help="Re-analyze items even if already tagged ai/noted")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to process")
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
