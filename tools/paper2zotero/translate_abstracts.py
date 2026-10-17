"""Translate missing or English Zotero abstractNote fields to Chinese and update via Web API.

Workflow:
1. Inventory target collection or item keys via Zotero API / local reader.
2. Filter items: skip items without an abstract; skip items whose abstract is already Chinese
   (unless --force is passed).
3. Stage and translate English abstracts in batches using Hermes model or configured LLM runner.
4. Normalise translations via zh_text.normalize_abstract:
   - Flatten all super/subscripts (MnO2, Zn2+, mAh g^-1).
   - Enforce exactly one half-width space at every CJK<->Latin boundary.
   - Strip leading "Abstract / 摘要" labels.
5. Mutate via Zotero Web API and immediately read back from Web API to verify.
   Never write directly to local zotero.sqlite. Supports --dry-run and safe resume.
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

from zotero_cli.config import load_config, resolve_write_credentials
from zotero_cli.core.writer import ZoteroWriter

SCIENTIST_HOME = Path("/Users/liuchzzyy/.hermes/profiles/scientist")
TOOLS_DIR = Path(__file__).resolve().parent
DEFAULT_STATE_FILE = TOOLS_DIR / "translate_abstracts_state.json"
CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
SESSION_LINE = re.compile(r"\n\s*session_id: [^\n]+\s*$")
LEADING_ABSTRACT_RE = re.compile(r"^\s*(?:abstract|摘要)\s*[:：]?\s*", re.I)

PROMPT = """你是材料科学与电化学储能领域的专业科研论文翻译助手。
请将提供的科技论文摘要（abstract）准确、严谨地翻译为规范的简体中文。
要求：
1. 保持高度专业性与学术风格，准确翻译专业词汇（如 conversion, dissolution, percolation network, Jahn-Teller effect, cathode, overpotential 等）。
2. 保留英文缩写、元素符号、化学式、单位与数值（如 MnO2, Zn2+, 600 mAh g^-1, 1.9 V vs. Zn2+/Zn, DFT, LCTEM 等）。
3. 剔除原文中混入的开头 "Abstract" 标签或因元数据抓取导致的重复开头首句。
4. 输出严格的 JSON 数组，每个对象仅包含原样 key、原样 title 和翻译好的中文摘要 abstract_zh。不得更改 key，不得增删条目，不能调用任何外部工具。
输入 JSON：
"""


class AbstractTranslationError(RuntimeError):
    """An abstract could not be safely translated, validated, or verified."""


def is_predominantly_chinese(text: str) -> bool:
    """Return True if text already contains significant Chinese characters (>20% of non-whitespace)."""
    cleaned = "".join(text.split())
    if not cleaned:
        return False
    cjk_chars = len(CHINESE_RE.findall(cleaned))
    return cjk_chars / len(cleaned) > 0.2


def clean_source_abstract(text: str) -> str:
    """Strip leading 'Abstract' label, unescape whitespace, and deduplicate leading Crossref stutter."""
    stripped = LEADING_ABSTRACT_RE.sub("", text.strip())
    # Remove metadata repeat: "The foo bar. , The foo bar..."
    repeat_match = re.match(r"^([^.!?\n]{20,200}[.!?])\s*,\s*\1\s*", stripped)
    if repeat_match:
        stripped = stripped[repeat_match.end(1) + 1:].strip()
        if stripped.startswith(","):
            stripped = stripped[1:].strip()
    return stripped


def select_items_to_translate(
    items: list[dict[str, Any]],
    *,
    force: bool = False,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Filter candidate items for translation:

    - Skip items without an abstractNote.
    - Skip items already having a Chinese abstract (unless force=True).
    Returns (selected_list, stats_summary).
    """
    selected: list[dict[str, str]] = []
    stats = {
        "total": len(items),
        "no_abstract": 0,
        "already_chinese": 0,
        "needs_translation": 0,
    }

    for item in items:
        if not isinstance(item, dict):
            continue
        data = item.get("data")
        if not isinstance(data, dict):
            data = item

        item_type = str(data.get("itemType", ""))
        if item_type in {"attachment", "note"}:
            continue

        raw_abstract = data.get("abstractNote") or data.get("abstract") or ""
        key = str(item.get("key") or data.get("key") or "")
        title = str(data.get("title") or "")
        doi = str(data.get("DOI") or data.get("doi") or "")

        if not isinstance(raw_abstract, str) or not raw_abstract.strip() or not key:
            stats["no_abstract"] += 1
            continue

        if not force and is_predominantly_chinese(raw_abstract):
            stats["already_chinese"] += 1
            continue

        cleaned_abstract = clean_source_abstract(raw_abstract)
        selected.append({
            "key": key,
            "title": title,
            "doi": doi,
            "abstract": cleaned_abstract,
        })
        stats["needs_translation"] += 1

    return selected, stats


def parse_model_response(raw_output: str, batch: list[dict[str, str]]) -> dict[str, str]:
    """Validate JSON array response from model, verifying key matching and non-empty Chinese output."""
    cleaned = SESSION_LINE.sub("", raw_output.strip())
    # Handle possible markdown fencing
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        payload = json.loads(cleaned)
    except (TypeError, ValueError) as exc:
        raise AbstractTranslationError(f"模型未返回有效 JSON: {exc} | 原始输出: {raw_output[:120]}") from exc

    if not isinstance(payload, list) or len(payload) != len(batch):
        raise AbstractTranslationError(f"模型返回条数 ({len(payload) if isinstance(payload, list) else 'invalid'}) 与输入 ({len(batch)}) 不符")

    expected_keys = {row["key"] for row in batch}
    translated = {}
    for row in payload:
        if not isinstance(row, dict):
            raise AbstractTranslationError("模型返回条目格式无效")
        key = row.get("key")
        abstract_zh = row.get("abstract_zh")
        if key not in expected_keys or key in translated:
            raise AbstractTranslationError(f"模型返回了未预期的或重复的 key: {key}")
        if not isinstance(abstract_zh, str) or not abstract_zh.strip() or not CHINESE_RE.search(abstract_zh):
            raise AbstractTranslationError(f"条目未返回有效中文摘要: {key}")
        translated[key] = abstract_zh.strip()

    return translated


def translate_batch(batch: list[dict[str, str]]) -> dict[str, str]:
    """Translate a batch of abstracts using hermes CLI; retries up to 3 times."""
    scratch = SCIENTIST_HOME / "cache/scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    query_rows = [
        {"key": row["key"], "title": row["title"], "abstract": row["abstract"]}
        for row in batch
    ]
    with tempfile.TemporaryDirectory(prefix="zotero-abstract-trans-", dir=scratch) as directory:
        query_file = Path(directory) / "query.txt"
        query_file.write_text(PROMPT + json.dumps(query_rows, ensure_ascii=False), encoding="utf-8")
        last_error = None
        for _ in range(3):
            try:
                result = subprocess.run(
                    [
                        "hermes", "chat", "-Q", "--ignore-rules", "-t", "browser", "--max-turns", "1",
                        "--source", "tool", "--in", str(TOOLS_DIR),
                        "--query-file", str(query_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env={**os.environ, "HERMES_HOME": str(SCIENTIST_HOME)},
                )
                if result.returncode != 0:
                    raise AbstractTranslationError(f"Hermes 退出码 {result.returncode}: {result.stderr.strip()}")
                return parse_model_response(result.stdout, batch)
            except (OSError, subprocess.TimeoutExpired, AbstractTranslationError) as exc:
                last_error = exc
        raise AbstractTranslationError(f"模型连续三次未能提供有效译文：{last_error}")


def _write_state(path: Path, state: list[dict[str, Any]]) -> None:
    """Persist checkpoint atomically using a unique temporary file."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def stage_translations(
    candidates: list[dict[str, str]],
    path: Path,
    *,
    batch_size: int = 3,
    custom_translator: Any = None,
) -> list[dict[str, Any]]:
    """Stage translations before making remote mutations, with resumption support."""
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(state, list) and all(isinstance(r, dict) for r in state):
                expected_keys = {r["key"] for r in candidates}
                if {r.get("key") for r in state} == expected_keys:
                    return state
        except Exception:
            pass  # regenerate if corrupt or mismatch

    translator = custom_translator or translate_batch
    translations: dict[str, str] = {}
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start:start + batch_size]
        translations.update(translator(batch))

    state = []
    for row in candidates:
        raw_zh = translations[row["key"]]
        normalised_zh = zh_text.normalize_abstract(raw_zh)
        state.append({
            **row,
            "abstract_zh": normalised_zh,
            "status": "pending",
        })

    _write_state(path, state)
    return state


def update_one_abstract(api: Any, writer: ZoteroWriter, row: dict[str, Any], abstract_zh: str) -> str:
    """Write normalised Chinese abstract to abstractNote and verify via Web API readback."""
    key = row["key"]
    item = api.item(key)
    data = item.get("data", {})
    if item.get("key") != key:
        raise AbstractTranslationError(f"条目 key 在准备更新时变化: {key}")

    current_abstract = data.get("abstractNote", "")
    if current_abstract == abstract_zh:
        return "already_up_to_date"

    writer.update_item(key, {"abstractNote": abstract_zh})

    # Verify via immediate Web API readback
    verify_item = api.item(key)
    verify_data = verify_item.get("data", {})
    if verify_data.get("abstractNote") != abstract_zh:
        raise AbstractTranslationError(f"Zotero abstractNote 写入后回读不一致: {key}")

    return "updated"


def run_abstract_translation(
    api: Any,
    writer: ZoteroWriter,
    candidates: list[dict[str, str]],
    path: Path,
    *,
    dry_run: bool = True,
    batch_size: int = 3,
    custom_translator: Any = None,
) -> dict[str, int]:
    """Execute the translation stage and atomic remote mutation."""
    if dry_run:
        return {
            "to_translate": len(candidates),
            "updated": 0,
            "already_up_to_date": 0,
        }

    state = stage_translations(candidates, path, batch_size=batch_size, custom_translator=custom_translator)
    counts = {"updated": 0, "already_up_to_date": 0}

    for row in state:
        if row.get("status") in {"updated", "already_up_to_date"}:
            counts[row["status"]] += 1
            continue

        status = update_one_abstract(api, writer, row, row["abstract_zh"])
        row["status"] = status
        counts[status] += 1
        _write_state(path, state)

    return counts


def resolve_collection_items(api: Any, collection_name_or_key: str) -> list[dict[str, Any]]:
    """Find all top-level items in a collection by name or key."""
    # First check if directly a collection key
    try:
        col = api.collection(collection_name_or_key)
        if col and "key" in col:
            return api.everything(api.collection_items(collection_name_or_key))
    except Exception:
        pass

    # Search all collections by name
    all_collections = api.everything(api.collections())
    target_key = None
    for c in all_collections:
        c_data = c.get("data", {})
        if c.get("key") == collection_name_or_key or c_data.get("name", "").strip().lower() == collection_name_or_key.strip().lower():
            target_key = c.get("key")
            break

    if not target_key:
        raise AbstractTranslationError(f"未找到名为或 key 为 '{collection_name_or_key}' 的集合")

    return api.everything(api.collection_items(target_key))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="翻译 Zotero 指定集合或条目的英文摘要并替换为规范中文摘要")
    parser.add_argument("--collection", help="目标集合名称或 key（例如 '00 | 收件箱' 或 'AG7NQ5UW'）")
    parser.add_argument("--key", action="append", default=[], help="指定更新的单个或多个条目 key（可重复使用）")
    parser.add_argument("--all", action="store_true", help="处理全库顶层条目（谨慎使用）")
    parser.add_argument("--apply", action="store_true", help="执行实际写入（默认仅 dry-run 统计预览）")
    parser.add_argument("--force", action="store_true", help="强制重新翻译已有中文的摘要")
    parser.add_argument("--batch-size", type=int, default=3, help="批量翻译条数（默认 3）")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="断点续跑暂存文件")
    args = parser.parse_args(argv)

    if not args.collection and not args.key and not args.all:
        parser.error("必须指定 --collection、--key 或 --all 之一作为目标范围。")

    config = load_config()
    library_id, api_key = resolve_write_credentials(config, library_type="user", group_id=None)
    if not library_id or not api_key:
        raise AbstractTranslationError("缺少 Zotero API 写入凭据 (library_id / api_key)")

    writer = ZoteroWriter(library_id, api_key)
    api = writer._zot

    # Gather target items
    raw_items: list[dict[str, Any]] = []
    if args.key:
        for k in args.key:
            raw_items.append(api.item(k))
    elif args.collection:
        raw_items = resolve_collection_items(api, args.collection)
    elif args.all:
        raw_items = api.everything(api.top(limit=100))

    candidates, stats = select_items_to_translate(raw_items, force=args.force)
    print(
        f"扫描结果：总条目 {stats['total']} 篇，"
        f"无摘要跳过 {stats['no_abstract']} 篇，"
        f"已有中文跳过 {stats['already_chinese']} 篇，"
        f"待翻译 {stats['needs_translation']} 篇。"
    )

    if not candidates:
        print("没有需要翻译的条目。")
        return

    result = run_abstract_translation(
        api,
        writer,
        candidates,
        args.state_file,
        dry_run=not args.apply,
        batch_size=args.batch_size,
    )
    mode_label = "【实际写入完成】" if args.apply else "【预览模式 (dry-run)】"
    print(f"{mode_label} 结果：{result}")


if __name__ == "__main__":
    try:
        main()
    except (AbstractTranslationError, OSError, ValueError, KeyError) as err:
        print(f"摘要翻译任务中断: {err}", file=sys.stderr)
        raise SystemExit(1) from err
