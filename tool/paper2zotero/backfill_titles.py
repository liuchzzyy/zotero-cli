"""Backfill missing Zotero translated-title fields using the scientist Hermes model.

Workflow: inventory the Zotero Web API -> stage/validate translations -> update
only missing Extra fields via the Zotero Web API -> read back each key.
Never writes the local Zotero SQLite database and never overwrites an existing
translated-title. Run under the zotero-cli venv; use --dry-run before --apply.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import zh_text

from zotero_cli.config import load_config, resolve_write_credentials, state_dir
from zotero_cli.config.runtime import section
from zotero_cli.zotero.access.writer import ZoteroWriter

_DEFAULT_SCIENTIST_HOME = Path.home() / ".hermes" / "profiles" / "scientist"
SCIENTIST_HOME = Path(os.environ.get("ZOT_PAPER2ZOTERO_HERMES_HOME") or _DEFAULT_SCIENTIST_HOME).expanduser()
PAPER2ZOTERO_STATE_SUBDIR = "paper2zotero"


def default_state_file() -> Path:
    """Checkpoint under the git-ignored repo state dir, never the tracked tools tree."""
    return Path(state_dir()) / PAPER2ZOTERO_STATE_SUBDIR / "backfill_titles_state.json"


TRANSLATED_TITLE_RE = re.compile(r"^\s*translated[- ]title\s*:", re.I | re.M)
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
SESSION_LINE = re.compile(r"\n\s*session_id: [^\n]+\s*$")
PROMPT = """你是材料与科研文献的标题翻译助手。仅依据所给 title 翻译为简体中文；保留化学式、价态及通用技术缩写。输出严格 JSON 数组，每项只含原样的 key、原样的 title 和 title_zh；不得更改标题或 key，不得增删条目，不能调用工具。若标题已经是中文，则保持原文。输入 JSON：\n"""


class BackfillError(RuntimeError):
    """An item could not be safely translated or verified."""


def select_missing(items: list[dict]) -> list[dict[str, str]]:
    """Select all titled top-level items without a translated-title in Extra."""
    chosen = []
    for item in items:
        data = item.get("data") or {}
        if data.get("itemType") in {"attachment", "note"}:
            continue
        title = data.get("title") or ""
        extra = data.get("extra") or ""
        if not isinstance(title, str) or not title.strip() or not isinstance(extra, str):
            continue
        if TRANSLATED_TITLE_RE.search(extra):
            continue
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise BackfillError("Zotero 条目缺少 key")
        chosen.append({"key": key, "title": title, "doi": data.get("DOI") or ""})
    return chosen


def parse_translations(raw: str, batch: list[dict[str, str]]) -> dict[str, str]:
    """Validate exact key/title echo, count, and nonempty Chinese output."""
    try:
        payload = json.loads(SESSION_LINE.sub("", raw.strip()))
    except (TypeError, ValueError) as exc:
        raise BackfillError("模型未返回有效 JSON") from exc
    expected = {row["key"]: row["title"] for row in batch}
    if not isinstance(payload, list) or len(payload) != len(batch):
        raise BackfillError("模型返回条数与输入不符")
    translated = {}
    for row in payload:
        if not isinstance(row, dict):
            raise BackfillError("模型返回条目格式无效")
        key, title, title_zh = row.get("key"), row.get("title"), row.get("title_zh")
        if key not in expected or key in translated or title != expected[key]:
            raise BackfillError(f"模型 key/title 与输入不一致：{key}")
        if not isinstance(title_zh, str) or not title_zh.strip() or not CHINESE_RE.search(title_zh):
            raise BackfillError(f"模型未返回中文标题：{key}")
        if "\n" in title_zh or "\r" in title_zh:
            raise BackfillError(f"模型标题含换行：{key}")
        translated[key] = title_zh.strip()
    return translated


def _normalise_input_title(title: str) -> str:
    """Collapse metadata whitespace/JATS tags before LLM input, not Zotero's original title."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", title)).strip()


def translate_batch(batch: list[dict[str, str]]) -> dict[str, str]:
    """Translate with an explicitly constrained toolset; retry malformed replies."""
    scratch = SCIENTIST_HOME / "cache/scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    query_rows = [{"key": row["key"], "title": _normalise_input_title(row["title"])} for row in batch]
    with tempfile.TemporaryDirectory(prefix="zotero-title-backfill-", dir=scratch) as directory:
        query = Path(directory) / "query.txt"
        query.write_text(PROMPT + json.dumps(query_rows, ensure_ascii=False), encoding="utf-8")
        error = None
        for _ in range(3):
            try:
                result = subprocess.run(
                    [
                        "hermes",
                        "chat",
                        "-Q",
                        "--ignore-rules",
                        "-t",
                        "browser",
                        "--max-turns",
                        "1",
                        "--source",
                        "tool",
                        "--in",
                        str(Path(__file__).resolve().parent),
                        "--query-file",
                        str(query),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=float(section("tools")["paper2zotero"]["title_timeout_seconds"]),
                    env={**os.environ, "HERMES_HOME": str(SCIENTIST_HOME)},
                )
                if result.returncode:
                    raise BackfillError(f"Hermes 退出码 {result.returncode}: {result.stderr.strip()}")
                return parse_translations(result.stdout, query_rows)
            except (OSError, subprocess.TimeoutExpired, BackfillError) as exc:
                error = exc
        raise BackfillError(f"模型连续三次未能提供有效译文：{error}")


def merge_extra(extra: str, title_zh: str) -> str:
    """Append only to an Extra field with no prior translation."""
    if TRANSLATED_TITLE_RE.search(extra):
        raise BackfillError("Extra 已存在 translated-title，拒绝覆盖")
    if not title_zh.strip() or not CHINESE_RE.search(title_zh) or "\n" in title_zh or "\r" in title_zh:
        raise BackfillError("中文标题无效")
    return "\n".join(
        [*(extra.rstrip().splitlines() if extra.strip() else []), f"translated-title: {zh_text.normalize(title_zh)}"]
    )


def _write_state(path: Path, state: list[dict]) -> None:
    """Persist the checkpoint atomically, with a temp name unique to this process.

    A fixed `path + ".tmp"` is shared by every writer of the same state file, so
    two concurrent runs would interleave into one temp file and rename the mixture
    into place. `replace` (rename(2)) makes the switch atomic, so the checkpoint is
    always either the previous one or a complete new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def update_one(api: Any, writer: ZoteroWriter, row: dict[str, str], title_zh: str) -> str:
    """Read latest item, preserve current Extra, update and verify exact target."""
    key = row["key"]
    current = api.item(key)
    data = current.get("data", {})
    if current.get("key") != key or data.get("title") != row["title"]:
        raise BackfillError(f"Zotero 条目 key/title 在准备更新时变化：{key}")
    if row.get("doi") and data.get("DOI") != row["doi"]:
        raise BackfillError(f"Zotero DOI 在准备更新时变化：{key}")
    extra = data.get("extra") or ""
    if TRANSLATED_TITLE_RE.search(extra):
        return "already_translated"
    new_extra = merge_extra(extra, title_zh)
    version = current.get("version")
    if isinstance(version, int):
        writer.update_item(key, {"extra": new_extra}, expected_version=version)
    else:
        writer.update_item(key, {"extra": new_extra})
    verify = api.item(key)
    verified = verify.get("data", {})
    if verify.get("key") != key or verified.get("title") != row["title"] or verified.get("extra") != new_extra:
        raise BackfillError(f"Zotero 写入后回读失败：{key}")
    if row.get("doi") and verified.get("DOI") != row["doi"]:
        raise BackfillError(f"Zotero 写入后 DOI 不一致：{key}")
    return "updated"


def stage_translations(items: list[dict], path: Path, *, batch_size: int | None = None) -> list[dict[str, str]]:
    """Stage every missing translation before any remote mutation; resume safely."""
    if batch_size is None:
        batch_size = int(section("tools")["paper2zotero"]["title_batch_size"])
    if batch_size < 1:
        raise BackfillError("batch_size 必须为正整数")
    saved: Any = []
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Fail closed: silently rebuilding would overwrite the staged plan and
            # lose which titles were already translated (repeat model cost).
            raise BackfillError(f"翻译暂存文件无法读取，拒绝覆盖：{path}: {exc}") from exc
    if not isinstance(saved, list) or not all(
        isinstance(row, dict) and isinstance(row.get("key"), str) for row in saved
    ):
        raise BackfillError(f"翻译暂存文件无效：{path}")
    state_map = {row["key"]: row for row in saved}
    if len(state_map) != len(saved):
        raise BackfillError("翻译暂存文件包含重复 key")
    state = []
    for item in items:
        previous = state_map.get(item["key"])
        if previous is not None and (
            previous.get("title") != item["title"] or (previous.get("doi") or "") != (item.get("doi") or "")
        ):
            raise BackfillError(f"Zotero 库存与翻译暂存文件不一致：{item['key']}")
        row = previous if previous is not None else {**item, "status": "pending"}
        state_map[item["key"]] = row
        state.append(row)
    pending = [row for row in state if not row.get("title_zh")]
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        translations = translate_batch(batch)
        if set(translations) != {row["key"] for row in batch}:
            raise BackfillError("翻译结果不完整")
        for row in batch:
            merge_extra("", translations[row["key"]])
            row["title_zh"] = translations[row["key"]]
        _write_state(path, list(state_map.values()))
        print(f"翻译进度 {sum(bool(row.get('title_zh')) for row in state)}/{len(state)}", flush=True)
    for row in state:
        merge_extra("", row["title_zh"])
    return state


def resolve_collection_items(api: Any, collection_name_or_key: str) -> list[dict]:
    """Find all items in a collection by name or key."""
    try:
        col = api.collection(collection_name_or_key)
        if col and "key" in col:
            return list(api.everything(api.collection_items(collection_name_or_key)))
    except Exception:
        pass

    all_collections = api.everything(api.collections())
    keyed = [collection for collection in all_collections if collection.get("key") == collection_name_or_key]
    matches = keyed or [
        collection
        for collection in all_collections
        if (collection.get("data") or {}).get("name", "").strip().casefold()
        == collection_name_or_key.strip().casefold()
    ]
    if not matches:
        raise BackfillError(f"未找到名为或 key 为 '{collection_name_or_key}' 的集合")
    if len(matches) != 1:
        keys = ", ".join(collection["key"] for collection in matches)
        raise BackfillError(f"多个集合同名，请使用 key：{keys}")
    return list(api.everything(api.collection_items(matches[0]["key"])))


def run_backfill(
    api: Any, writer: ZoteroWriter, path: Path, *, dry_run: bool = False, collection: str | None = None
) -> dict[str, int]:
    """Inventory, stage, update, verify and checkpoint each item."""
    if collection:
        items = resolve_collection_items(api, collection)
    else:
        items = api.everything(api.top(limit=100))
    missing = select_missing(items)
    if dry_run:
        by_type: dict[str, int] = {}
        missing_keys = {row["key"] for row in missing}
        for item in items:
            typ = item.get("data", {}).get("itemType", "")
            if item.get("key") in missing_keys:
                by_type[typ] = by_type.get(typ, 0) + 1
        print(f"缺少翻译且有标题：{len(missing)}；按类型：{by_type}")
        return {"missing": len(missing), "updated": 0, "already_translated": 0}
    state = stage_translations(missing, path)
    counts = {"missing": len(state), "updated": 0, "already_translated": 0}
    for row in state:
        # The fresh inventory says this title is missing; cached completion is not proof otherwise.
        result = update_one(api, writer, row, row["title_zh"])
        row["status"] = result
        counts[result] += 1
        _write_state(path, state)
        print(f"写入进度：{counts['updated']} 已更新，{counts['already_translated']} 已存在", flush=True)
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="为 Zotero 全库缺少中文翻译的标题补写 translated-title")
    parser.add_argument("--collection", help="目标集合名称或 key（例如 'Inbox' 或 'COLLECTIONKEY'）")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只统计")
    args = parser.parse_args(argv)
    config = load_config()
    library_id, api_key = resolve_write_credentials(config, library_type="user", group_id=None)
    if not library_id or not api_key:
        raise BackfillError("Zotero API 凭据缺失")
    writer = ZoteroWriter(library_id, api_key)
    result = run_backfill(writer._zot, writer, default_state_file(), dry_run=not args.apply, collection=args.collection)
    print(f"结果：{result}")


if __name__ == "__main__":
    try:
        main()
    except (BackfillError, OSError, ValueError, KeyError) as exc:
        print(f"全库标题翻译中断：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
