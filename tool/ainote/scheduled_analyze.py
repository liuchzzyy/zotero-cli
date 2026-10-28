"""Guarded incremental AI Notes for scheduled collection runs.

Inventory and tags are refreshed through the Web API; PDFs remain local inputs.
No --force, deletions, membership changes or implicit retry of uncertain writes.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from batch_analyze import load_state, save_state

from zotero_cli.ai_notes.constants import ANALYZED_TAG, KEYWORDS_TAG, NO_KEYWORDS_TAG, NOTE_TITLE_PREFIX
from zotero_cli.pdf_rag.metadata import clean_html

DONE_TAGS = {"ai/noted", "ai/uncertain"}
DONE_STATUSES = {"ok", "already_analyzed", "uncertain"}
# A non-committed failure that repeats is not transient: the input itself cannot
# yield a note (structured text over `max_extracted_chars`, or more main figures
# than the request budget allows). Park it after this many attempts so a nightly
# run stops re-attempting it and stops reporting the same failure forever.
MAX_ATTEMPTS = 3
# 缺 ai/keywords 的已完成条目只补关键词；同一最多尝试该次数。
KEYWORD_BACKFILL_ATTEMPTS = 2
PARKED_STATUS = "unanalyzable"
HALTED_STATUSES = DONE_STATUSES | {PARKED_STATUS}


def _checkpoint(item: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """A checkpoint entry: every write records the same run context."""
    return {**fields, "updated_at": time.time(), "title": item.get("title", "")}


def verify_result(api: Any, key: str, before: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Read the exact target back before recording verified success."""
    item = api.item(key)["data"]
    tags = {entry["tag"] for entry in item.get("tags", [])}
    if result.get("status") == "uncertain":
        if "ai/uncertain" not in tags:
            raise RuntimeError("ai/uncertain tag missing on readback")
        return {"verified": True, "tags": sorted(tags)}
    if result.get("status") != "ok":
        raise RuntimeError(f"Unexpected analysis status: {result.get('status')}")
    notes = api.everything(api.children(key, limit=100))
    note = next((n["data"] for n in notes if n["key"] == result.get("note_key")), None)
    if not note or note.get("parentItem") != key or note.get("itemType") != "note":
        raise RuntimeError("Expected child note not found")
    html = note.get("note", "")
    sections = re.split(r"<h2\b[^>]*>.*?</h2>", html, flags=re.I | re.S)
    if NOTE_TITLE_PREFIX not in clean_html(html) or len(sections) < 2:
        raise RuntimeError("AI note title or section headings missing")
    if any(not clean_html(body).strip() for body in sections[1:]):
        raise RuntimeError("AI note has an empty section")
    if "ai/noted" not in tags:
        raise RuntimeError("Analysis tag missing")
    extra = item.get("extra", "")
    if result.get("short_note") == "ok":
        if "ai/keywords" not in tags:
            raise RuntimeError("Keyword tag missing")
        short = [s for s in extra.splitlines() if s.lower().startswith("short-note:")]
        if len(short) != 1 or "疑问：" not in short[0]:
            raise RuntimeError("short-note missing, duplicated or incomplete")
    elif result.get("short_note") == "failed":
        if "ai/no_keywords" not in tags:
            short = [s for s in extra.splitlines() if s.lower().startswith("short-note:")]
            if len(short) != 1 or "疑问：" not in short[0]:
                raise RuntimeError("Keyword failure not marked on readback")
    else:
        raise RuntimeError("Unexpected short-note status")
    previous = [s for s in before.get("extra", "").splitlines() if not s.lower().startswith("short-note:")]
    if any(s not in extra.splitlines() for s in previous):
        raise RuntimeError("Pre-existing Extra content changed")
    if any(item.get(field) != before.get(field) for field in ("title", "DOI", "collections")):
        raise RuntimeError("Item identity or collection membership changed")
    return {"verified": True, "headings": len(sections) - 1, "tags": sorted(tags), "note_key": note["key"]}


def verify_keyword_result(api: Any, key: str, before: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """回填后只核对标签与唯一 short-note 行，不重写笔记内容。"""
    item = api.item(key)["data"]
    tags = {entry["tag"] for entry in item.get("tags", [])}
    extra_lines = (item.get("extra") or "").splitlines()
    short = [line for line in extra_lines if line.lower().startswith("short-note:")]
    if result.get("short_note") == "ok":
        if KEYWORDS_TAG not in tags:
            raise RuntimeError("Keyword tag missing on readback")
        if len(short) != 1 or "疑问：" not in short[0]:
            raise RuntimeError("short-note missing, duplicated or incomplete")
    elif result.get("short_note") == "failed":
        if NO_KEYWORDS_TAG not in tags:
            raise RuntimeError("Keyword failure not marked on readback")
        if short:
            raise RuntimeError("short-note present despite keyword failure")
    else:
        raise RuntimeError("Unexpected short-note status")
    previous = [line for line in (before.get("extra") or "").splitlines() if not line.lower().startswith("short-note:")]
    if any(line not in extra_lines for line in previous):
        raise RuntimeError("Pre-existing Extra content changed")
    return {"verified": True, "tags": sorted(tags), "keyword_verified": True}


def run_collection(
    reader: Any,
    api: Any,
    analyze: Callable[..., dict[str, Any]],
    collection: str,
    state_path: Path,
    *,
    apply: bool = False,
    budget_seconds: float = 3000,
    retry_parked: bool = False,
    retry_blocked: bool = False,
) -> dict[str, Any]:
    """Process only fresh candidates; all network/model boundaries are injectable.

    Failed items are reported individually; callers distinguish a completed scan
    from pre-scan failures. The time budget defers remaining candidates to future
    scheduled runs rather than claiming every item was analyzed in one tick.
    """
    started = time.monotonic()
    local = {i.key: i for i in reader.get_collection_items(collection) if i.item_type not in {"note", "attachment"}}
    remote_list = api.everything(api.collection_items(collection, limit=100))
    remote_keys = {v["key"] for v in remote_list if v["data"]["itemType"] not in {"note", "attachment"}}
    keys = sorted(set(local) | remote_keys)
    state = load_state(state_path)
    if retry_parked and apply:
        # An operator who fixed the cause (raised the char cap, split the PDF)
        # clears the parking here; otherwise parked items are never re-attempted.
        # Gated on apply so an inventory dry run still never writes anything.
        state = {key: value for key, value in state.items() if value.get("status") != PARKED_STATUS}
        save_state(state_path, state)
    # Do not let yesterday's failed first key consume today's attempt budget
    # before every new candidate has had a chance. Still retry it afterwards.
    keys.sort(key=lambda key: state.get(key, {}).get("status") == "failed")
    summary: dict[str, Any] = {
        "total": len(keys),
        "processed": 0,
        "uncertain": 0,
        "candidates": [],
        "skipped": [],
        "failed": [],
        "blocked": [],
        "degraded": [],
        "keyword_backfilled": [],
        "keyword_failed": [],
        "keyword_blocked": [],
        "keyword_candidates": [],
        "dry_run": not apply,
        "repaired": 0,
        "salvaged": 0,
        "normalized_sources": 0,
        "dropped_sources": 0,
        "write_retries": 0,
    }
    for key in keys:
        try:
            before = api.item(key)["data"]
            if before.get("deleted") or collection not in before.get("collections", []):
                summary["skipped"].append({"key": key, "reason": "not_live_member"})
                continue
            all_tags = {entry["tag"] for entry in before.get("tags", [])}
            previous = state.get(key, {})
            if retry_blocked and apply and (previous.get("status") == "blocked" or previous.get("committed")):
                # 人工确认后仍须由下方远端子笔记回读挡住重复创建。
                previous = {}
            done_tags = sorted(all_tags & DONE_TAGS)
            if done_tags:
                # 已分析但缺关键词的条目只回填 Extra 与标签，绝不重跑完整分析。
                if ANALYZED_TAG not in all_tags or KEYWORDS_TAG in all_tags:
                    summary["skipped"].append({"key": key, "reason": "tagged", "tags": done_tags})
                    continue
                if previous.get("status") in {"blocked", "keyword_in_progress"} or previous.get("committed"):
                    summary["blocked"].append({"key": key, "error": "prior_keyword_write_outcome_uncertain"})
                    continue
                attempts = int(previous.get("keyword_attempts", 0))
                if attempts >= KEYWORD_BACKFILL_ATTEMPTS:
                    summary["keyword_blocked"].append({"key": key, "attempts": attempts})
                    continue
                if time.monotonic() - started > budget_seconds:
                    summary["skipped"].append({"key": key, "reason": "deferred_time_budget"})
                    continue
                if not apply:
                    summary["keyword_candidates"].append(key)
                    continue
                state[key] = _checkpoint(before, status="keyword_in_progress", keyword_attempts=attempts)
                save_state(state_path, state)
                try:
                    result = analyze(key, short_note_only=True)
                except Exception as exc:
                    committed = bool(getattr(exc, "committed", True))
                    next_attempts = attempts + 1
                    status = (
                        "blocked"
                        if committed
                        else ("keyword_blocked" if next_attempts >= KEYWORD_BACKFILL_ATTEMPTS else "keyword_failed")
                    )
                    state[key] = _checkpoint(
                        before,
                        status=status,
                        error=str(exc),
                        committed=committed,
                        keyword_attempts=next_attempts,
                    )
                    save_state(state_path, state)
                    entry = {"key": key, "error": f"{type(exc).__name__}: {exc}", "attempts": next_attempts}
                    summary[status if status in {"blocked", "keyword_blocked"} else "keyword_failed"].append(entry)
                    continue
                try:
                    verification = verify_keyword_result(api, key, before, result)
                except Exception as exc:
                    state[key] = _checkpoint(
                        before, status="blocked", error=str(exc), committed=True, keyword_attempts=attempts + 1
                    )
                    save_state(state_path, state)
                    summary["blocked"].append({"key": key, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                short_note_status = result.get("short_note")
                next_attempts = attempts + 1
                status = (
                    "keyword_backfilled"
                    if short_note_status == "ok"
                    else ("keyword_blocked" if next_attempts >= KEYWORD_BACKFILL_ATTEMPTS else "keyword_failed")
                )
                state[key] = _checkpoint(
                    before,
                    **{
                        **result,
                        **verification,
                        "status": status,
                        "keyword_attempts": next_attempts,
                        "dry_run": False,
                    },
                )
                save_state(state_path, state)
                if short_note_status == "ok":
                    summary["keyword_backfilled"].append({"key": key, "tags": verification["tags"]})
                else:
                    summary[status].append(
                        {
                            "key": key,
                            "error": result.get("short_note_error", "关键词回填未成功"),
                            "attempts": next_attempts,
                        }
                    )
                continue
            if previous.get("status") in HALTED_STATUSES and not previous.get("dry_run"):
                summary["skipped"].append({"key": key, "reason": f"checkpoint_{previous['status']}"})
                continue
            notes = api.everything(api.children(key, limit=100))
            existing = [
                n["key"]
                for n in notes
                if n["data"]["itemType"] == "note" and NOTE_TITLE_PREFIX in clean_html(n["data"].get("note", ""))
            ]
            if existing:
                summary["skipped"].append({"key": key, "reason": "existing_ai_note", "note_keys": existing})
                continue
            if previous.get("status") == "in_progress" or previous.get("committed"):
                summary["blocked"].append(
                    {"key": key, "error": "prior_write_outcome_uncertain; reconcile before retry"}
                )
                continue
            if key not in local:
                summary["skipped"].append({"key": key, "reason": "not_synced_locally"})
                continue
            pdfs = [a for a in reader.get_pdf_attachments(key) if a.path is not None and a.path.is_file()]
            if not pdfs:
                summary["skipped"].append({"key": key, "reason": "no_local_pdf"})
                continue
            if time.monotonic() - started > budget_seconds:
                summary["skipped"].append({"key": key, "reason": "deferred_time_budget"})
                continue
            summary["candidates"].append(key)
            if not apply:
                continue
            state[key] = _checkpoint(before, status="in_progress", attempts=previous.get("attempts", 0))
            save_state(state_path, state)
            try:
                result = analyze(key)
            except Exception as exc:
                committed = bool(getattr(exc, "committed", True))
                attempts = int(previous.get("attempts", 0)) + 1
                # A network/provider failure may recover next run. Only the
                # pipeline's explicit, deterministic input-budget errors park.
                parkable = not committed and bool(getattr(exc, "parkable", False))
                budget_attempts = int(previous.get("budget_attempts", 0)) + 1 if parkable else 0
                parked = budget_attempts >= MAX_ATTEMPTS
                state[key] = _checkpoint(
                    before,
                    status=PARKED_STATUS if parked else "failed",
                    error=str(exc),
                    committed=committed,
                    attempts=attempts,
                    budget_attempts=budget_attempts,
                )
                save_state(state_path, state)
                if parked:
                    summary["blocked"].append(
                        {
                            "key": key,
                            "attempts": attempts,
                            "budget_attempts": budget_attempts,
                            "error": str(exc),
                            "title": before.get("title", ""),
                        }
                    )
                    continue
                raise
            # If readback fails, keep in_progress. Never blindly append another note.
            verification = verify_result(api, key, before, result)
            # Merge, don't splat twice: both dicts carry `note_key`, and a second
            # ** would raise "multiple values for keyword argument".
            state[key] = _checkpoint(before, **{**result, **verification, "dry_run": False})
            save_state(state_path, state)
            for name in ("repaired", "salvaged", "normalized_sources", "dropped_sources", "write_retries"):
                summary[name] += (
                    int(bool(result.get(name))) if name in {"repaired", "salvaged"} else int(result.get(name, 0))
                )
            summary["uncertain" if result["status"] == "uncertain" else "processed"] += 1
            if result.get("short_note") == "failed":
                summary["degraded"].append(
                    {"key": key, "note_key": result["note_key"], "short_note_error": result.get("short_note_error", "")}
                )
        except Exception as exc:
            summary["failed"].append({"key": key, "error": f"{type(exc).__name__}: {exc}"})
    return summary


def batch_exit_code(result: dict[str, Any]) -> int:
    """Item failures are reported per key, not as a failed scheduler run."""
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", required=True, help="Stable collection key; direct members only")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true", help="Inventory only; no MinerU, LLM or Zotero writes")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--retry-parked",
        action="store_true",
        help=f"Clear '{PARKED_STATUS}' checkpoints so they are attempted again (apply mode only)",
    )
    parser.add_argument(
        "--retry-blocked", action="store_true", help="Retry blocked items after confirming no duplicate note exists"
    )
    args = parser.parse_args()
    # Imports/configuration are intentionally lazy: --help works offline.
    from pyzotero import zotero

    from zotero_cli.ai_notes.access.client import AiClient
    from zotero_cli.ai_notes.analysis import analyze_item
    from zotero_cli.config import (
        get_data_dir,
        get_prefs_js_path,
        load_ai_note_config,
        load_config,
        resolve_library_id,
        state_dir,
    )
    from zotero_cli.zotero.access.reader import ZoteroReader
    from zotero_cli.zotero.access.writer import ZoteroWriter

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / f"{args.collection}.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        cfg = load_config()
        if not cfg.library_id or not cfg.api_key:
            raise RuntimeError("Zotero Web API credentials missing")
        api = zotero.Zotero(str(cfg.library_id), "user", cfg.api_key)
        collection = api.collection(args.collection)["data"]
        db = get_data_dir(cfg) / "zotero.sqlite"
        reader = ZoteroReader(db, library_id=resolve_library_id(db, {}), prefs_js_path=get_prefs_js_path(cfg))
        ai_client = None
        writer = None

        def analyze(key: str, *, short_note_only: bool = False) -> dict[str, Any]:
            nonlocal ai_client, writer
            if ai_client is None:
                ai_client = AiClient(load_ai_note_config())
                writer = ZoteroWriter(str(cfg.library_id), cfg.api_key)
            return dict(
                analyze_item(
                    reader,
                    writer,
                    ai_client,
                    key,
                    force=False,
                    short_note_only=short_note_only,
                    progress=lambda event, message: logging.info("[%s][%s] %s", key, event, message),
                )
            )

        try:
            result = run_collection(
                reader,
                api,
                analyze,
                args.collection,
                state_dir() / f"batch_ainote_{args.collection}.json",
                apply=args.apply,
                retry_parked=args.retry_parked,
                retry_blocked=args.retry_blocked,
            )
        finally:
            reader.close()
        result.update(
            {
                "collection_key": args.collection,
                "collection_name": collection["name"],
                "time": datetime.now().astimezone().isoformat(),
            }
        )
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        save_state(args.output_dir / f"run-{stamp}.json", result)
        save_state(args.output_dir / "latest.json", result)
        if (
            args.dry_run
            or result["processed"]
            or result["uncertain"]
            or result["failed"]
            or result["blocked"]
            or result["keyword_backfilled"]
            or result["keyword_failed"]
            or result["keyword_blocked"]
        ):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return batch_exit_code(result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stderr)
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Zotero AI Note failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
