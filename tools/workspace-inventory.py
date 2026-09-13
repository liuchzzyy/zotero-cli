#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from zotero_cli.config import get_data_dir, get_prefs_js_path, load_config, load_vector_store_config
from zotero_cli.core.rag_index import RagIndex
from zotero_cli.core.reader import ZoteroReader
from zotero_cli.core.semantic_search import QdrantVectorStore, resolve_vector_store_path
from zotero_cli.core.workspace import Workspace, load_workspace, save_workspace, workspace_exists, workspace_index_path
from zotero_cli.models import Item


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inventory Zotero PDFs and workspace index state.")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scan-limit", type=int, default=100_000)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collection", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.scan_limit <= 0:
        raise SystemExit("--scan-limit must be greater than 0")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be greater than 0")

    cfg = load_config()
    db_path = get_data_dir(cfg) / "zotero.sqlite"
    reader = ZoteroReader(db_path, prefs_js_path=get_prefs_js_path(cfg))
    try:
        print(f"[inventory] reading local Zotero DB {db_path}", flush=True)
        collection_counts: dict[str, int] = {}
        if args.collection:
            item_by_key: dict[str, Item] = {}
            for collection in args.collection:
                result = reader.search("", collection=collection, limit=args.scan_limit)
                collection_counts[collection] = result.total
                for item in result.items:
                    item_by_key.setdefault(item.key, item)
            items = list(item_by_key.values())
        else:
            items = reader.search("", limit=args.scan_limit).items

        pdf_items: list[dict[str, object]] = []
        missing_pdf_files = 0
        for scan_index, item in enumerate(items, 1):
            attachments = reader.get_pdf_attachments(item.key)
            local_pdfs = [
                {"key": att.key, "filename": att.filename, "path": str(att.path)}
                for att in attachments
                if att.path is not None and att.path.exists()
            ]
            if local_pdfs:
                pdf_items.append(
                    {
                        "key": item.key,
                        "title": item.title,
                        "item_type": item.item_type,
                        "pdf_count": len(local_pdfs),
                        "pdfs": local_pdfs,
                    }
                )
            elif attachments:
                missing_pdf_files += 1
            if scan_index % args.progress_every == 0 or scan_index == len(items):
                print(
                    f"[inventory] scanned={scan_index}/{len(items)} local_pdf_items={len(pdf_items)} "
                    f"pdf_but_missing={missing_pdf_files}",
                    flush=True,
                )
    finally:
        reader.close()

    if workspace_exists(args.workspace):
        workspace = load_workspace(args.workspace)
        existing_keys = {entry.key for entry in workspace.items}
        workspace_created = False
    else:
        workspace = Workspace(
            name=args.workspace,
            created=utc_now(),
            description="Auto-maintained workspace containing Zotero items with local PDF attachments.",
        )
        existing_keys = set()
        workspace_created = True

    added = 0
    for row in pdf_items:
        key = str(row["key"])
        if key not in existing_keys:
            existing_keys.add(key)
            added += 1
            if not args.dry_run:
                workspace.add_item(key, str(row.get("title") or ""))
    if not args.dry_run:
        save_workspace(workspace)

    indexed_keys: set[str] = set()
    chunk_count = 0
    vector_count = 0
    index_path = workspace_index_path(args.workspace)
    if index_path.exists():
        rag_index = RagIndex(index_path)
        try:
            indexed_keys = rag_index.get_indexed_keys()
            chunk_count = len(rag_index.get_chunk_ids())
        finally:
            rag_index.close()
        vector_cfg = load_vector_store_config()
        vectors = QdrantVectorStore(resolve_vector_store_path(vector_cfg), f"ws_{args.workspace}")
        try:
            vector_count = vectors.count()
        finally:
            vectors.close()

    pdf_keys = {str(row["key"]) for row in pdf_items}
    pending_keys = sorted(pdf_keys - indexed_keys)
    payload = {
        "created_at": utc_now(),
        "workspace": args.workspace,
        "collections": args.collection,
        "collection_item_counts": collection_counts,
        "dry_run": args.dry_run,
        "db_path": str(db_path),
        "scanned_items": len(items),
        "local_pdf_items": len(pdf_items),
        "pdf_but_missing_local_file": missing_pdf_files,
        "workspace_created": workspace_created,
        "workspace_existing_items": len(existing_keys) - added,
        "workspace_added_items": added,
        "indexed_items": len(indexed_keys),
        "indexed_chunk_count": chunk_count,
        "chunks_with_embeddings": vector_count,
        "chunks_missing_embeddings": max(chunk_count - vector_count, 0),
        "pending_index_items": len(pending_keys),
        "pending_index_keys": pending_keys,
        "items": pdf_items,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[inventory-summary] local_pdf_items={len(pdf_items)} added_to_workspace={added} "
        f"indexed={len(indexed_keys)} pending_index={len(pending_keys)} chunks={chunk_count} "
        f"embedding_missing={payload['chunks_missing_embeddings']} output={args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
