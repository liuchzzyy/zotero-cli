"""Sync Chinese title and abstract for the items of one Zotero collection.

Target collection is given on the command line: a collection key (e.g.
6MJBZYQX) or a name (exact, or a unique substring).

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

from zotero_cli.config import load_ai_note_config, load_config, resolve_write_credentials  # noqa: E402
from zotero_cli.core.notes.client import AiClient  # noqa: E402
from zotero_cli.core.zotero.reader import ZoteroReader  # noqa: E402
from zotero_cli.core.zotero.writer import ZoteroWriter  # noqa: E402

DEFAULT_STATE_FILE_TEMPLATE = TOOLS_DIR / "zh_state_{key}.json"

ABS_DONE = {"updated", "already_up_to_date"}
TITLE_DONE = {"updated", "already_translated"}

# Manual abstracts for items whose publisher page blocks automated retrieval,
# keyed by Zotero item key: {key: (abstract_text, source_label)}.
MANUAL_ABSTRACT_OVERRIDES: dict[str, tuple[str, str]] = {
    "ZK8TQ474": (
        "Layer-structured metal oxides are promising cathode materials for potassium-ion batteries (PIBs) "
        "due to their high specific capacity, low cost, and environmental friendliness. However, the redox activity "
        "and behaviors of different metal ions in such oxides remain inadequately understood. Herein, the redox behaviors "
        "of various transition metal dopants (Fe, Co, Ni, and Cu) in P3-type K0.5MnO2 cathodes for PIBs are systematically "
        "investigated. Although these doped elements are typically redox-active in sodium-ion battery cathodes, synchrotron "
        "X-ray absorption spectroscopy reveals their minimal valence changes during electrochemical cycling in PIBs, "
        "indicating unexpected redox-inert behavior. Density functional theory calculations elucidate the underlying "
        "mechanism: doping with Fe or Cu substantially suppresses their 3d-orbital conduction bands, while Co or Ni doping "
        "leads to larger band gaps compared to that of Mn. This electronic structure rationalizes the observed Mn-dominated "
        "redox activity. Electrochemical tests further show that Fe- and Co-doped samples deliver higher specific capacities "
        "than their Ni- and Cu-doped counterparts, consistent with reduced bandgaps in the Mn density of states. These findings "
        "provide fundamental insights into the unexpected redox-inert role of variable-valence dopants in KxMnO2 cathodes, "
        "offering guidance for designing high-performance PIB cathodes.",
        "acs_pubs",
    ),
    "46ZMA4F2": (
        "Spinel-type ZnMn2O4 cathodes for aqueous zinc-ion batteries (AZIBs) offer high theoretical capacity and elemental "
        "abundance, yet their electrochemical performance is fundamentally hindered by Mn dissolution-induced structural instability "
        "and sluggish Zn2+ diffusion kinetics. Herein, we propose a tunable cationic vacancy engineering strategy for ZnMn2O4 cathodes, "
        "enabling selective introduction of Zn2+ or Mn3+ vacancies via a facile chemical coprecipitation method. Comprehensive "
        "characterizations confirm that these cationic vacancies significantly enhance electronic conductivity and accelerate Zn2+ "
        "diffusion kinetics. Specifically, the Zn-vacant Zn1-xMn2O4 cathode outperforms its Mn-vacant counterpart, leveraging "
        "strengthened Mn-O bonding and reduced electrostatic repulsion during Zn2+ insertion, delivers a high reversible capacity "
        "of 276.4 mAh g^-1 at 0.5 A g^-1, exceptional rate capability (retain 134.4 mAh g^-1 at 2 A g^-1), and long-term cyclability "
        "with 92% capacity retention after 2000 cycles at 2 A g^-1. Furthermore, a prototype pouch cell incorporating this engineered "
        "cathode achieves a superior capacity of 150 mAh g^-1 at 0.5 A g^-1, demonstrating its great potential for practical high-performance AZIBs.",
        "acs_pubs",
    ),
    "5265A2NI": (
        "Rechargeable aqueous zinc-manganese batteries have been receiving inclusive attention due to their high energy density, "
        "low cost and environmental friendliness. However, the development of manganese dioxide as the cathode for aqueous "
        "Zn-ion batteries (ZIBs) is restricted by the speedy capacity attenuation and low rate performance. Herein, a new cathode "
        "material α-MnO2@In2O3 is proposed. After a uniform coating of In2O3 on the α-MnO2 nanotubes, the cycling stability and "
        "rate performance have all got great improvement due to the enhancement of the electrical conductivity according to the "
        "electrochemical kinetical investigation result. α-MnO2@In2O3 exhibits a high capacity of 425 mA h g^-1 after 100 cycles "
        "at 100 mA g^-1, and it can still maintain 115 mA h g^-1 at a high current density of 2000 mA g^-1 after a long-term cycling "
        "of 1300 cycles. Besides, a specific capacity of 75 mA h g^-1 is attained at 3000 mA g^-1 for 3000 cycles without significant "
        "decay. The overall electrochemical performance are superior to most other reported cathode materials so far. This study brings "
        "a new coating strategy of using In2O3 to the improvement of the intrinsic poor conductivity of cathode materials other than carbon coating.",
        "iopscience",
    ),
    "JUHB6RVF": (
        "Chalcophanite, ZnMn3O7·3H2O, crystallizes in the triclinic system with a = 7.54 Å, b = 7.54 Å, c = 8.22 Å, "
        "α = 90°, β = 117.2°, γ = 120°; the space group is P-1 with two formula units in the unit cell. "
        "The structure contains sheets of edge-sharing [MnO6] octahedra, with one out of every seven octahedral sites "
        "vacant, ordered in a superlattice. The Zn atoms are situated above and below these vacancies, coordinated to "
        "three oxygens of the sheet and three water molecules. The water molecules form a double layer between the "
        "sheets, held together by hydrogen bonds.",
        "acta_cryst",
    ),
}


def recover_missing_abstract(
    key: str,
    doi: str,
    title: str,
    reader: ZoteroReader,
    storage_dir: Path,
    mailto: str = "liuchzzyy@gmail.com",
) -> tuple[str | None, str]:
    """Recover missing abstract from Crossref, local PDF cache, or OpenAlex."""
    # 1. Try Crossref
    if doi:
        try:
            url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
            req = urllib.request.Request(url, headers={"User-Agent": f"ZoteroCli/2.0 (mailto:{mailto})"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                raw_ab = data.get("message", {}).get("abstract", "")
                if raw_ab and raw_ab.strip():
                    clean = re.sub(r"<[^>]+>", " ", raw_ab)
                    clean = " ".join(clean.split())
                    if len(clean) > 80:
                        return clean, "crossref"
        except Exception:
            pass

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
    except Exception:
        pass

    # 3. Try OpenAlex
    if doi:
        try:
            url = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}"
            req = urllib.request.Request(url, headers={"User-Agent": f"ZoteroCli/2.0 (mailto:{mailto})"})
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
        except Exception:
            pass

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
            override = MANUAL_ABSTRACT_OVERRIDES.get(key)
            if override:
                recovered, src = override
            else:
                recovered, src = recover_missing_abstract(key, doi, title, reader, storage_dir, cfg.crossref_mailto)

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
    """Atomic state file write."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state_map(state_path: Path) -> dict[str, dict[str, Any]]:
    """Load a staged state file as {item key: row}; an unreadable file is rebuilt."""
    if not state_path.exists():
        return {}
    try:
        existing = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"读取现有暂存状态失败: {e}，将重新构建。")
        return {}
    if isinstance(existing, list):
        return {row["key"]: row for row in existing if isinstance(row, dict) and row.get("key")}
    return {}


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

    state: list[dict[str, Any]] = json.loads(state_path.read_text(encoding="utf-8"))
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
    parser.add_argument("--collection", required=True, help="集合 key（如 6MJBZYQX）或集合名称")
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
        help="暂存状态文件路径（默认按集合 key 命名：zh_state_<key>.json）",
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
    state_path = args.state_file or Path(str(DEFAULT_STATE_FILE_TEMPLATE).format(key=collection_key))
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
    main()
