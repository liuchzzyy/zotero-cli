"""Sync Chinese title and abstract for the items of one Zotero collection.

Target collection is given on the command line: a collection key (e.g.
COLLECTIONKEY) or a name (exact, or a unique substring).

Two fields are handled:
- ``abstractNote``: the English abstract is translated to Chinese. Always on.
- Extra line ``translated-title:``: the title is translated to Chinese. Only
  with ``--with-titles``, and an existing translated-title is never overwritten.

Workflow:
1. Inventory all top-level articles in the target collection.
2. For articles missing an abstract:
   - Check Crossref API (api.crossref.org/works/{doi}).
   - If not found, recover from local PDF cache (.zotero-ft-cache) or OpenAlex.
3. Filter out abstracts that are already Chinese; skip titles that already have
   a translated-title in Extra, or are already Chinese.
4. Translate abstracts and titles in batches using AiClient.
5. Normalise with zh_text (no super/subscripts, CJK-Latin half-width spacing).
6. Atomically checkpoint the state file after every batch.
7. Apply via Zotero Web API and verify each field by readback.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

# Import from the sibling tools directory and the repo src/ tree (not installed).
TOOLS_DIR = Path(__file__).resolve().parent
REPO_DIR = TOOLS_DIR.parent.parent
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(REPO_DIR / "src"))

import backfill_titles as backfill  # noqa: E402
import zh_text  # noqa: E402
from translate_abstracts import (  # noqa: E402
    PROMPT,
    AbstractTranslationError,
    clean_source_abstract,
    is_predominantly_chinese,
    parse_model_response,
    update_one_abstract,
    validate_translation,
)

from zotero_cli.ai_notes.client import AiClient  # noqa: E402
from zotero_cli.config import (  # noqa: E402
    load_ai_note_config,
    load_config,
    resolve_crossref_mailto,
    resolve_write_credentials,
    state_dir,
)
from zotero_cli.zotero.reader import ZoteroReader  # noqa: E402
from zotero_cli.zotero.writer import ZoteroWriter  # noqa: E402

PAPER2ZOTERO_STATE_SUBDIR = "paper2zotero"
MANUAL_ABSTRACTS_ENV = "ZOT_PAPER2ZOTERO_MANUAL_ABSTRACTS"

ABS_DONE = {"updated", "already_up_to_date"}
TITLE_DONE = {"updated", "already_translated"}


def default_state_path(collection_key: str) -> Path:
    """Checkpoint under the git-ignored repo state dir, never the tracked tools tree."""
    return Path(state_dir()) / PAPER2ZOTERO_STATE_SUBDIR / f"zh_state_{collection_key}.json"


def load_manual_abstract_overrides() -> dict[str, tuple[str, str]]:
    """Load optional local abstracts for publisher-blocked items.

    ``ZOT_PAPER2ZOTERO_MANUAL_ABSTRACTS`` points at a JSON object outside the
    repository mapping Zotero item keys to ``[abstract_text, source_label]``
    pairs (or ``{"abstract": ..., "source": ...}`` objects), so real library
    data is configured locally instead of being committed in the tool source.
    An unreadable file only warns and falls back to automated recovery.
    """
    raw_path = os.environ.get(MANUAL_ABSTRACTS_ENV, "").strip()
    if not raw_path:
        return {}
    path = Path(raw_path).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[warn] 无法读取手动摘要覆盖文件 {path}: {exc}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(f"[warn] 手动摘要覆盖文件格式无效（应为 JSON 对象）: {path}", file=sys.stderr)
        return {}
    overrides: dict[str, tuple[str, str]] = {}
    for key, value in data.items():
        if isinstance(value, (list, tuple)) and len(value) == 2 and all(isinstance(item, str) for item in value):
            overrides[str(key)] = (value[0], value[1])
        elif isinstance(value, dict) and isinstance(value.get("abstract"), str):
            overrides[str(key)] = (value["abstract"], str(value.get("source") or "manual"))
        else:
            print(f"[warn] 忽略无效的手动摘要覆盖项: {key}", file=sys.stderr)
    return overrides


def recover_missing_abstract(
    key: str,
    doi: str,
    title: str,
    reader: ZoteroReader,
    storage_dir: Path,
    mailto: str = "",
) -> tuple[str | None, str]:
    """Recover missing abstract from Crossref, local PDF cache, or OpenAlex.

    ``mailto`` only feeds the polite-pool User-Agent; callers pass the configured
    contact so no personal address is baked into the tool. Recovery failures are
    reported on stderr instead of being silently treated as "no abstract".
    """
    user_agent = f"ZoteroCli/2.0 (mailto:{mailto})" if mailto else "ZoteroCli/2.0"
    # 1. Try Crossref
    if doi:
        try:
            url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                raw_ab = data.get("message", {}).get("abstract", "")
                if raw_ab and raw_ab.strip():
                    clean = re.sub(r"<[^>]+>", " ", raw_ab)
                    clean = " ".join(clean.split())
                    if len(clean) > 80:
                        return clean, "crossref"
        except Exception as exc:
            print(f"[warn] Crossref 摘要恢复失败 {doi}: {exc}", file=sys.stderr)

    # 2. Try local PDF .zotero-ft-cache
    try:
        atts = reader.get_pdf_attachments(key)
        for a in atts:
            ft_path = storage_dir / a.key / ".zotero-ft-cache"
            if ft_path.exists():
                text = ft_path.read_text(errors="replace")
                patterns = [
                    r"\babstract\b\s*[:\.]?\s*(.*?)(?:\b(?:1\.\s+introduction|introduction|keywords|key\s+words)\b)",
                    r"\babstract\b\s*[:\.]?\s*(.*?)(?:\n\s*©|\n\s*1\.|\n\s*introduction|\n\s*the\s+increasing|\n\s*graphical\s+abstract|\n\s*highlights)",
                    r"\babstract\b\s*[:\.]?\s*(.*?)(?:\n\s*\n\s*[A-Z0-9])",
                ]
                for pat in patterns:
                    m = re.search(pat, text, re.I | re.S)
                    if m:
                        cand = " ".join(m.group(1).split())
                        if 120 < len(cand) < 3500 and "supporting information" not in cand.lower()[:50]:
                            return cand, f"pdf:{a.key}"
    except Exception as exc:
        print(f"[warn] 本地 PDF 缓存摘要恢复失败 {key}: {exc}", file=sys.stderr)

    # 3. Try OpenAlex
    if doi:
        try:
            url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}"
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=5) as r:
                d = json.loads(r.read())
                inv = d.get("abstract_inverted_index")
                if inv:
                    words: list[str | None] = [None] * (max(idx for indices in inv.values() for idx in indices) + 1)
                    for word, indices in inv.items():
                        for idx in indices:
                            words[idx] = word
                    ab = " ".join(w for w in words if w is not None)
                    if len(ab) > 100:
                        return ab, "openalex"
        except Exception as exc:
            print(f"[warn] OpenAlex 摘要恢复失败 {doi}: {exc}", file=sys.stderr)

    return None, "none"


def resolve_collection(api: Any, spec: str) -> tuple[str, str]:
    """Resolve a collection key or name to (collection_key, collection_name)."""
    collections = api.everything(api.collections())
    by_key: dict[str, str] = {}
    for c in collections:
        data = c.get("data", {}) or {}
        ckey = data.get("key") or c.get("key")
        if ckey:
            by_key[ckey] = data.get("name", "")
    if spec in by_key:
        return spec, by_key[spec]
    exact = [k for k, n in by_key.items() if n == spec]
    if len(exact) == 1:
        return exact[0], by_key[exact[0]]
    partial = [k for k, n in by_key.items() if spec.lower() in (n or "").lower()]
    if len(partial) == 1:
        return partial[0], by_key[partial[0]]
    listing = "\n".join(f"  {k}  {n}" for k, n in sorted(by_key.items(), key=lambda kv: kv[1] or ""))
    raise SystemExit(f"无法唯一定位集合 {spec!r}（匹配到 {len(partial)} 个）。可用集合：\n{listing}")


def inventory_collection(
    api: Any,
    reader: ZoteroReader,
    cfg: Any,
    collection_key: str,
    manual_overrides: dict[str, tuple[str, str]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Inventory the collection and return one candidate row per item needing work.

    A row is emitted when the abstract needs translating or, when titles are
    requested, the title has no translated-title in Extra.
    """
    print(f"正在从 Zotero Web API 拉取集合 {collection_key} 中的所有条目...")
    raw_items = api.everything(api.collection_items(collection_key))
    top_articles = [it for it in raw_items if it.get("data", {}).get("itemType") not in {"attachment", "note"}]
    print(f"获取到顶层文献条目 {len(top_articles)} 篇。")

    storage_dir = Path(cfg.data_dir) / "storage"
    overrides = load_manual_abstract_overrides() if manual_overrides is None else manual_overrides
    mailto = resolve_crossref_mailto(cfg)
    candidates: list[dict[str, Any]] = []
    stats = {
        "total": len(top_articles),
        "has_abstract": 0,
        "abstract_recovered": 0,
        "abstract_unrecoverable": 0,
        "abstract_already_chinese": 0,
        "abstract_needs_translation": 0,
        "title_present": 0,
        "title_already_chinese": 0,
        "title_needs_translation": 0,
    }

    for it in top_articles:
        data = it.get("data", {}) or {}
        key = it.get("key")
        title = data.get("title") or ""
        doi = data.get("DOI") or ""
        extra = data.get("extra") or ""
        raw_abstract = data.get("abstractNote") or ""

        row: dict[str, Any] = {"key": key, "title": title, "doi": doi, "source_abstract": raw_abstract}

        # A short Chinese abstract can be complete. Never recover/replace it
        # merely because the English publisher-stub heuristic uses 120 chars.
        already_chinese = is_predominantly_chinese(zh_text.normalize_abstract(raw_abstract))
        is_fake_abstract = not already_chinese and (
            not raw_abstract.strip()
            or len(raw_abstract.strip()) < 120
            or raw_abstract.strip().startswith("ACS Applied")
            or raw_abstract.strip().startswith("DOI:")
        )

        if not is_fake_abstract:
            stats["has_abstract"] += 1
            if already_chinese:
                stats["abstract_already_chinese"] += 1
            else:
                row["abstract"] = clean_source_abstract(raw_abstract)
                stats["abstract_needs_translation"] += 1
        else:
            recovered: str | None
            override = overrides.get(key)
            if override:
                recovered, src = override
            else:
                recovered, src = recover_missing_abstract(key, doi, title, reader, storage_dir, mailto)

            if recovered:
                stats["abstract_recovered"] += 1
                row["abstract"] = clean_source_abstract(recovered)
                row["abstract_source"] = src
                stats["abstract_needs_translation"] += 1
            else:
                stats["abstract_unrecoverable"] += 1

        if backfill.TRANSLATED_TITLE_RE.search(extra):
            stats["title_present"] += 1
        elif title.strip():
            if is_predominantly_chinese(title):
                stats["title_already_chinese"] += 1
            else:
                row["title_needs_zh"] = True
                stats["title_needs_translation"] += 1

        if "abstract" in row or row.get("title_needs_zh"):
            candidates.append(row)

    return candidates, stats


def write_state(path: Path, state: list[dict[str, Any]]) -> None:
    """Atomic state file write with a per-writer unique temp name.

    A fixed `path + ".tmp"` is shared by concurrent writers; unique names plus
    `replace` keep the checkpoint either the previous or a complete new value,
    and a failed write never leaves the temp file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def load_state_map(state_path: Path) -> dict[str, dict[str, Any]]:
    """Load a staged state file as {item key: row}; refuse to silently rebuild a corrupt one."""
    if not state_path.exists():
        return {}
    try:
        existing = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AbstractTranslationError(f"暂存状态文件无法读取，拒绝覆盖: {state_path}: {exc}") from exc
    if not isinstance(existing, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("key"), str) for row in existing
    ):
        raise AbstractTranslationError(f"暂存状态文件格式无效，拒绝覆盖: {state_path}")
    state_map = {row["key"]: row for row in existing}
    if len(state_map) != len(existing):
        raise AbstractTranslationError(f"暂存状态文件包含重复 key，拒绝覆盖: {state_path}")
    return state_map


def _ordered_state(candidates: list[dict[str, Any]], state_map: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [state_map[c["key"]] for c in candidates if c["key"] in state_map]


def _run_batches(
    targets: list[dict[str, Any]],
    handler: Any,
    *,
    batch_size: int,
    max_workers: int,
    label: str,
    on_done: Any,
) -> None:
    """Run ``handler`` over parallel batches and hand each result to ``on_done``."""
    batches = [targets[i : i + batch_size] for i in range(0, len(targets), batch_size)]
    total = len(batches)
    if not total:
        return
    print(f"{label}：{len(targets)} 篇，{total} 个批次，并发 {max_workers}。")
    lock = threading.Lock()
    done = 0

    def run(batch: list[dict[str, Any]]) -> None:
        nonlocal done
        result = handler(batch)
        with lock:
            on_done(batch, result)
            done += 1
            print(f"[{label} {done}/{total}] 批次完成 ({len(batch)} 篇): {[r['key'] for r in batch]}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(run, b) for b in batches]
        for f in concurrent.futures.as_completed(futures):
            f.result()


def translate_abstract_batch(batch: list[dict[str, Any]], ai_client: AiClient) -> dict[str, str]:
    """Translate one batch of abstracts, retrying up to 3 times."""
    keys = [r["key"] for r in batch]
    prompt = PROMPT + json.dumps(
        [{"key": r["key"], "title": r["title"], "abstract": r["abstract"]} for r in batch],
        ensure_ascii=False,
    )
    last: Exception | None = None
    for attempt in range(3):
        try:
            parsed = parse_model_response(ai_client.chat(prompt, temperature=0.2), batch)
            return {k: zh_text.normalize_abstract(parsed[k]) for k in keys}
        except Exception as e:
            last = e
            print(f"  摘要批次 {keys} 重试 ({attempt + 1}/3) 错误: {e}")
            time.sleep(2)
    raise AbstractTranslationError(f"摘要批次连续 3 次翻译失败: {keys}: {last}")


def translate_title_batch(batch: list[dict[str, Any]], ai_client: AiClient) -> dict[str, str]:
    """Translate one batch of titles, reusing the validated backfill prompt and parser."""
    query = [{"key": r["key"], "title": backfill._normalise_input_title(r["title"])} for r in batch]  # noqa: SLF001
    keys = [r["key"] for r in batch]
    prompt = backfill.PROMPT + json.dumps(query, ensure_ascii=False)
    last: Exception | None = None
    for attempt in range(3):
        try:
            parsed = backfill.parse_translations(ai_client.chat(prompt, temperature=0.2), query)
            return {k: zh_text.normalize(v) for k, v in parsed.items()}
        except Exception as e:
            last = e
            print(f"  标题批次 {keys} 重试 ({attempt + 1}/3) 错误: {e}")
            time.sleep(2)
    raise backfill.BackfillError(f"标题批次连续 3 次翻译失败: {keys}: {last}")


def stage_translations(
    candidates: list[dict[str, Any]],
    state_path: Path,
    ai_client: AiClient,
    *,
    batch_size: int = 6,
    max_workers: int = 6,
    with_titles: bool = False,
) -> list[dict[str, Any]]:
    """Translate abstracts and (optionally) titles, checkpointing after every batch.

    Existing translations in the state file are reused, so a rerun only fills gaps.
    """
    state_map = load_state_map(state_path)
    for cand in candidates:
        previous = state_map.get(cand["key"])
        matches = previous is not None and all(
            previous.get(field) == cand.get(field)
            for field in ("title", "doi", "abstract", "source_abstract", "abstract_source")
        )
        row = previous if previous is not None and matches else {**cand}
        row.setdefault("abstract_status", "pending")
        row.setdefault("title_status", "pending")
        if "abstract_zh" in row:
            row["abstract_zh"] = validate_translation(row["abstract_zh"], row["key"])
        state_map[cand["key"]] = row
    rows = _ordered_state(candidates, state_map)

    def checkpoint() -> None:
        write_state(state_path, _ordered_state(candidates, state_map))

    abs_targets = [r for r in rows if r.get("abstract") and not r.get("abstract_zh")]
    print(f"待翻译摘要：{len(abs_targets)} 篇（已暂存 {sum(1 for r in rows if r.get('abstract_zh'))} 篇）。")

    def on_abstracts(batch: list[dict[str, Any]], result: dict[str, str]) -> None:
        for r in batch:
            state_map[r["key"]]["abstract_zh"] = validate_translation(result[r["key"]], r["key"])
        checkpoint()

    _run_batches(
        abs_targets,
        lambda b: translate_abstract_batch(b, ai_client),
        batch_size=batch_size,
        max_workers=max_workers,
        label="摘要",
        on_done=on_abstracts,
    )

    if with_titles:
        title_targets = [r for r in rows if r.get("title_needs_zh") and not r.get("title_zh")]
        print(f"待翻译标题：{len(title_targets)} 篇（已暂存 {sum(1 for r in rows if r.get('title_zh'))} 篇）。")

        def on_titles(batch: list[dict[str, Any]], result: dict[str, str]) -> None:
            for r in batch:
                state_map[r["key"]]["title_zh"] = result[r["key"]]
            checkpoint()

        _run_batches(
            title_targets,
            lambda b: translate_title_batch(b, ai_client),
            batch_size=batch_size,
            max_workers=max_workers,
            label="标题",
            on_done=on_titles,
        )
    else:
        print("未启用 --with-titles，跳过中文标题。")

    checkpoint()
    return _ordered_state(candidates, state_map)


def apply_updates_from_state(
    api: Any,
    writer: ZoteroWriter,
    state_path: Path,
    *,
    limit: int | None = None,
    max_workers: int = 4,
    with_titles: bool = False,
) -> dict[str, int]:
    """Write the staged abstract (and optional title) to Zotero and verify each readback."""
    if not state_path.exists():
        raise AbstractTranslationError(f"暂存状态文件不存在: {state_path}")

    try:
        state: list[dict[str, Any]] = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AbstractTranslationError(f"暂存状态文件无法读取，拒绝覆盖: {state_path}: {exc}") from exc
    if not isinstance(state, list) or any(not isinstance(row, dict) or not row.get("key") for row in state):
        raise AbstractTranslationError(f"暂存状态文件格式无效，拒绝覆盖: {state_path}")
    counts = {
        "abstract_updated": 0,
        "abstract_already_up_to_date": 0,
        "abstract_failed": 0,
        "title_updated": 0,
        "title_already_translated": 0,
        "title_failed": 0,
    }

    def needs_abstract(row: dict[str, Any]) -> bool:
        return "abstract_zh" in row and row.get("abstract_status") not in ABS_DONE

    def needs_title(row: dict[str, Any]) -> bool:
        return with_titles and bool(row.get("title_zh")) and row.get("title_status") not in TITLE_DONE

    pending_indices = [i for i, row in enumerate(state) if needs_abstract(row) or needs_title(row)]
    if limit is not None:
        pending_indices = pending_indices[:limit]

    mode = "摘要+标题" if with_titles else "仅摘要"
    print(f"待向 Zotero Web API 写入并核验: {len(pending_indices)} 篇 ({mode}, 并发 {max_workers})...")
    if not pending_indices:
        return counts

    lock = threading.Lock()
    done_count = 0
    total = len(pending_indices)

    def apply_abstract(row: dict[str, Any]) -> None:
        target_zh = row["abstract_zh"]
        result = update_one_abstract(api, writer, row, target_zh)
        with lock:
            row["abstract_status"] = result
            counts[f"abstract_{result}"] += 1

    def apply_title(row: dict[str, Any]) -> None:
        result = backfill.update_one(api, writer, row, row["title_zh"])
        with lock:
            row["title_status"] = result
            counts[f"title_{result}"] += 1

    def update_single(idx: int) -> None:
        nonlocal done_count
        row = state[idx]
        key = row["key"]
        for field, handler in (("abstract", needs_abstract), ("title", needs_title)):
            if not handler(row):
                continue
            try:
                if field == "abstract":
                    apply_abstract(row)
                else:
                    apply_title(row)
            except Exception as e:
                with lock:
                    row[f"{field}_status"] = "failed"
                    counts[f"{field}_failed"] += 1
                    print(f"[ERROR] {key} 中文{'摘要' if field == 'abstract' else '标题'}写入失败: {e}")
        with lock:
            done_count += 1
            if done_count % 10 == 0 or done_count == total:
                print(f"[{done_count}/{total}] 已处理 {key}: {counts}")
                write_state(state_path, state)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(update_single, idx) for idx in pending_indices]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    write_state(state_path, state)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="为指定 Zotero 集合同步中文摘要（abstractNote），可选同步中文标题（Extra: translated-title）"
    )
    parser.add_argument("--collection", required=True, help="集合 key（如 COLLECTIONKEY）或集合名称")
    parser.add_argument(
        "--with-titles",
        action="store_true",
        help="同时翻译标题并写入 Extra 的 translated-title（已有该字段的条目跳过，不覆盖）",
    )
    parser.add_argument("--apply", action="store_true", help="执行实际写入 Web API 并回读验证")
    parser.add_argument("--batch-size", type=int, default=6, help="大模型翻译批次大小（默认 6）")
    parser.add_argument("--max-workers", type=int, default=6, help="翻译并发线程数（默认 6）")
    parser.add_argument("--limit-apply", type=int, default=None, help="单次写入 Web API 的条数限制")
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="暂存状态文件路径（默认 .zot/state/paper2zotero/zh_state_<key>.json）",
    )
    args = parser.parse_args()

    cfg = load_config()
    library_id, api_key = resolve_write_credentials(cfg, library_type="user")
    writer = ZoteroWriter(library_id, api_key)
    api = writer._zot
    reader = ZoteroReader(Path(cfg.data_dir) / "zotero.sqlite")
    ai_cfg = load_ai_note_config()
    ai_cfg.reasoning_effort = ""  # Abstract translation does not require max reasoning effort
    ai_client = AiClient(ai_cfg)

    collection_key, collection_name = resolve_collection(api, args.collection)
    state_path = args.state_file or default_state_path(collection_key)
    print(f"目标集合：{collection_name} ({collection_key})")
    print(f"暂存状态文件：{state_path}")
    print(f"中文标题：{'启用' if args.with_titles else '未启用'}")

    # 1. Inventory & Recover
    candidates, stats = inventory_collection(api, reader, cfg, collection_key=collection_key)
    print("集合统计情况：")
    for k, v in stats.items():
        print(f"  - {k}: {v}")

    # 2. Stage translations
    staged = stage_translations(
        candidates,
        state_path,
        ai_client,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
        with_titles=args.with_titles,
    )
    staged_abs = sum(1 for r in staged if r.get("abstract_zh"))
    staged_title = sum(1 for r in staged if r.get("title_zh"))
    print(f"翻译暂存完成：摘要 {staged_abs} 篇，中文标题 {staged_title} 篇。")

    # 3. Apply updates if requested
    if args.apply:
        res = apply_updates_from_state(
            api,
            writer,
            state_path,
            limit=args.limit_apply,
            max_workers=args.max_workers,
            with_titles=args.with_titles,
        )
        print(f"Web API 写入与回读验证完成：{res}")
    else:
        print("未指定 --apply，处于预览与暂存模式。实际写入请加 --apply。")


if __name__ == "__main__":
    try:
        main()
    except (AbstractTranslationError, backfill.BackfillError, OSError, ValueError, KeyError) as exc:
        print(f"元数据同步中断: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
