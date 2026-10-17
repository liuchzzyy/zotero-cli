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
from pathlib import Path

from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.writer import ZoteroWriter

SCIENTIST_HOME = Path("/Users/liuchzzyy/.hermes/profiles/scientist")
STATE_FILE = Path(__file__).resolve().parent / "backfill_titles_state.json"
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
                    ["hermes", "chat", "-Q", "--ignore-rules", "-t", "browser", "--max-turns", "1",
                     "--source", "tool", "--in", str(Path(__file__).resolve().parent),
                     "--query-file", str(query)],
                    capture_output=True, text=True, timeout=240,
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
    return "\n".join([*(extra.rstrip().splitlines() if extra.strip() else []),
                      f"translated-title: {title_zh.strip()}"])


def update_one(api, writer, row: dict[str, str], title_zh: str) -> str:
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
    writer.update_item(key, {"extra": new_extra})
    verify = api.item(key)
    verified = verify.get("data", {})
    if verify.get("key") != key or verified.get("title") != row["title"] or verified.get("extra") != new_extra:
        raise BackfillError(f"Zotero 写入后回读失败：{key}")
    if row.get("doi") and verified.get("DOI") != row["doi"]:
        raise BackfillError(f"Zotero 写入后 DOI 不一致：{key}")
    return "updated"


def stage_translations(items: list[dict], path: Path, *, batch_size: int = 5) -> list[dict[str, str]]:
    """Stage every missing translation before any remote mutation; resume safely."""
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, list) or not all(isinstance(r, dict) for r in state):
            raise BackfillError(f"翻译暂存文件无效：{path}")
        expected = {r["key"]: r["title"] for r in items}
        if {r.get("key"): r.get("title") for r in state} != expected:
            raise BackfillError("Zotero 库存与翻译暂存文件不一致，拒绝覆盖")
        return state
    translations = {}
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        translations.update(translate_batch(batch))
        print(f"翻译进度 {len(translations)}/{len(items)}", flush=True)
    if len(translations) != len(items):
        raise BackfillError("翻译结果不完整")
    state = [{**row, "title_zh": translations[row["key"]], "status": "pending"} for row in items]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return state


def run_backfill(api, writer, path: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Inventory, stage, update, verify and checkpoint each item."""
    items = api.everything(api.top(limit=100))
    missing = select_missing(items)
    if dry_run:
        by_type = {}
        for item in items:
            typ = item.get("data", {}).get("itemType", "")
            if item.get("key") in {r["key"] for r in missing}:
                by_type[typ] = by_type.get(typ, 0) + 1
        print(f"缺少翻译且有标题：{len(missing)}；按类型：{by_type}")
        return {"missing": len(missing), "updated": 0, "already_translated": 0}
    state = stage_translations(missing, path)
    counts = {"missing": len(state), "updated": 0, "already_translated": 0}
    for row in state:
        if row["status"] in {"updated", "already_translated"}:
            continue
        result = update_one(api, writer, row, row["title_zh"])
        row["status"] = result
        counts[result] += 1
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        print(f"写入进度：{counts['updated']} 已更新，{counts['already_translated']} 已存在", flush=True)
    return counts


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="为 Zotero 全库缺少中文翻译的标题补写 translated-title")
    parser.add_argument("--apply", action="store_true", help="实际写入；默认只统计")
    args = parser.parse_args(argv)
    config = load_config()
    library_id, api_key = resolve_write_credentials(config, library_type="user", group_id=None)
    if not library_id or not api_key:
        raise BackfillError("Zotero API 凭据缺失")
    writer = ZoteroWriter(library_id, api_key)
    result = run_backfill(writer._zot, writer, STATE_FILE, dry_run=not args.apply)
    print(f"结果：{result}")


if __name__ == "__main__":
    try:
        main()
    except (BackfillError, OSError, ValueError, KeyError) as exc:
        print(f"全库标题翻译中断：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
