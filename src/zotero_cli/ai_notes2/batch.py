"""对 Zotero 集合批量运行 zot ai-note2，逐条记录结果并可续跑。"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ZOT = REPO / ".venv" / "bin" / "zot"
DROPPED_ENV = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "PYTHONSTARTUP")
SUCCESS = {"ok", "local_only"}


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in DROPPED_ENV}
    env["PYTHONNOUSERSITE"] = "1"
    return env


def zot_json(args: list[str], timeout: float | None = None) -> tuple[int, dict, str]:
    proc = subprocess.run(
        [str(ZOT), "--json", *args],
        cwd=REPO,
        env=child_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    text = proc.stdout.strip()
    try:
        envelope = json.loads(text) if text else {}
    except json.JSONDecodeError:
        envelope = {"ok": False, "error": {"code": "bad_json", "message": text[-800:]}}
    return proc.returncode, envelope, proc.stderr


def resolve_collection(value: str) -> tuple[str, str]:
    rc, envelope, err = zot_json(["zotero", "collection", "list"])
    if rc != 0 or not envelope.get("ok"):
        raise SystemExit(f"无法读取集合列表：{err.strip() or envelope}")
    flat: list[dict] = []

    def walk(nodes: list[dict]) -> None:
        for node in nodes:
            flat.append(node)
            walk(node.get("children") or [])

    walk(envelope["data"])
    for node in flat:
        if node["key"] == value:
            return node["key"], node["name"]
    for node in flat:
        if node["name"].casefold() == value.casefold():
            return node["key"], node["name"]
    raise SystemExit(f"找不到集合：{value}")


def collection_items(key: str) -> list[dict]:
    rc, envelope, err = zot_json(["zotero", "collection", "items", key])
    if rc != 0 or not envelope.get("ok"):
        raise SystemExit(f"无法读取集合条目：{err.strip() or envelope}")
    return envelope["data"]


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"items": {}}
    return json.loads(path.read_text("utf-8"))


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行 zot ai-note2（集合级、可续跑）")
    parser.add_argument("--collection", required=True, help="集合 key 或精确名称")
    parser.add_argument("--apply", action="store_true", help="调用模型并写入 Zotero；缺省只列清单")
    parser.add_argument("--dry-run", action="store_true", help="传给 ai-note2 的 --dry-run：解析输入但不写")
    parser.add_argument("--local-only", action="store_true", help="只写本地产物，不改 Zotero")
    parser.add_argument("--force", action="store_true", help="已有发布记录时新建运行并追加 note")
    parser.add_argument("--limit", type=int, help="最多处理多少条")
    parser.add_argument("--out-dir", help="输出目录，默认 ~/CodeSpace/Hermes/ainote2_<COLKEY>")
    parser.add_argument("--max-minutes", type=float, default=0.0, help="本轮总时长预算，0 表示不限")
    parser.add_argument("--item-timeout-minutes", type=float, default=90.0, help="单条超时")
    parser.add_argument("--retry-failed", action="store_true", help="重新处理上一轮失败/超时条目")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    colkey, colname = resolve_collection(args.collection)
    items = [i for i in collection_items(colkey) if i.get("item_type") == "journalArticle"]
    out_dir = (
        Path(args.out_dir).expanduser() if args.out_dir else Path.home() / "CodeSpace" / "Hermes" / f"ainote2_{colkey}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "runs").mkdir(exist_ok=True)
    state_path = out_dir / "state.json"
    state = load_state(state_path)
    entries = state.setdefault("items", {})

    if not args.apply and not args.dry_run:
        for item in items:
            done = entries.get(item["key"], {}).get("status")
            print(f"{item['key']}  {done or 'pending':10s}  {item['title'][:80]}")
        print(f"\n集合 {colname}（{colkey}）：{len(items)} 条 journalArticle；加 --apply 或 --dry-run 执行。")
        return 0

    started = time.monotonic()
    report: dict[str, object] = {
        "collection": colkey,
        "name": colname,
        "mode": "apply" if args.apply else "dry_run",
        "started": stamp(),
    }
    buckets: dict[str, list[str]] = {
        "ok": [],
        "dry_run": [],
        "skipped": [],
        "failed": [],
        "timeout": [],
        "deferred": [],
        "retry_pending": [],
    }
    processed = 0

    for item in items:
        key = item["key"]
        previous = entries.get(key, {}).get("status")
        if previous in SUCCESS and not args.force:
            buckets["ok"].append(key)
            continue
        if previous in {"failed", "timeout", "deferred"} and not args.retry_failed and not args.force:
            buckets["retry_pending"].append(key)
            continue
        if args.limit is not None and processed >= args.limit:
            buckets["deferred"].append(key)
            continue
        if args.max_minutes and (time.monotonic() - started) / 60 >= args.max_minutes:
            buckets["deferred"].append(key)
            continue

        command = ["ai-note2", key, "--progress-lines"]
        if args.dry_run:
            command.append("--dry-run")
        if args.local_only:
            command.append("--local-only")
        if args.force:
            command.append("--force")
        else:
            command += ["--idempotency-key", f"ainote2-{colkey}-{key}-v1"]

        print(f"[{stamp()}] {key} {item['title'][:70]}", flush=True)
        began = time.monotonic()
        try:
            rc, envelope, stderr = zot_json(command, timeout=args.item_timeout_minutes * 60)
        except subprocess.TimeoutExpired:
            entries[key] = {"status": "timeout", "at": stamp(), "title": item["title"]}
            save_state(state_path, state)
            buckets["timeout"].append(key)
            processed += 1
            continue

        (out_dir / "runs" / f"{key}.json").write_text(json.dumps(envelope, ensure_ascii=False, indent=2), "utf-8")
        if stderr.strip():
            (out_dir / "runs" / f"{key}.log").write_text(stderr, "utf-8")

        data = envelope.get("data") or {}
        status = data.get("status")
        if rc == 0 and envelope.get("ok") and status in SUCCESS:
            bucket = "ok"
        elif status == "skipped":
            bucket = "skipped"
        elif status == "dry_run":
            bucket = "dry_run"
        else:
            bucket = "failed"

        entries[key] = {
            "status": bucket,
            "ai_status": status,
            "run_number": data.get("run_number"),
            "note_key": data.get("note_key"),
            "objects": data.get("object_count"),
            "error": (envelope.get("error") or {}).get("code"),
            "message": (envelope.get("error") or {}).get("message"),
            "exit_code": rc,
            "seconds": round(time.monotonic() - began, 1),
            "at": stamp(),
            "title": item["title"],
        }
        save_state(state_path, state)
        buckets[bucket].append(key)
        processed += 1

    report.update(
        {
            "finished": stamp(),
            "counts": {name: len(keys) for name, keys in buckets.items()},
            "items": {key: entries[key] for keys in buckets.values() for key in keys if key in entries},
        }
    )
    (out_dir / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))
    return 0 if not buckets["failed"] and not buckets["timeout"] else 1


if __name__ == "__main__":
    sys.exit(main())
