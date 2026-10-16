"""Send semantically selected DOI items to the Zotero inbox using zotero-cli's Web API writer.

Run this file with /Users/liuchzzyy/CodeSpace/zotero-cli/.venv/bin/python.
The local Zotero SQLite database is never written to; readback uses the Web API.
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import date, timedelta
from pathlib import Path

SCRIPT_DIR = Path("/Users/liuchzzyy/CodeSpace/PaperCrossref")
INBOX_KEY = "AG7NQ5UW"
INBOX_NAME = "00 | 收件箱"
FLAGS = ("is_cathode_material", "is_characterization_technique")
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
STATUS_COLUMNS = ("zotero_status", "zotero_key")


class ZoteroInboxError(RuntimeError):
    """An inbox operation failed or cannot be verified."""


def select_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Either topic flag 1/uncertain qualifies; journal priority is unrelated."""
    chosen = []
    for row in rows:
        doi = row.get("doi")
        if not isinstance(doi, str) or doi != doi.strip() or not DOI_PATTERN.fullmatch(doi):
            raise ZoteroInboxError(f"无效 DOI：{doi!r}")
        if any(row.get(key) not in {"0", "1", "uncertain"} for key in FLAGS):
            raise ZoteroInboxError(f"语义标记缺失或无效：{doi}")
        if any(row[key] in {"1", "uncertain"} for key in FLAGS):
            chosen.append(row)
    return chosen


def resolve_metadata(doi: str) -> dict:
    """Use zotero-cli's Crossref mapper; never create a bare record on resolution failure."""
    from zotero_cli.core.metadata_resolver import resolve_doi

    fields = resolve_doi(doi)
    if not isinstance(fields, dict) or not fields.get("title"):
        raise ZoteroInboxError(f"Crossref 无完整标题，未导入 DOI：{doi}")
    returned_doi = fields.get("DOI")
    if not isinstance(returned_doi, str) or returned_doi.strip().casefold() != doi.casefold():
        raise ZoteroInboxError(f"Crossref DOI 不匹配，未导入：{doi}")
    return fields


def _verify(api, key: str, doi: str, *, must_have_inbox: bool = True) -> None:
    item = api.item(key)
    if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
        raise ZoteroInboxError(f"Zotero 回读条目格式无效：{doi} / {key}")
    data = item["data"]
    if not isinstance(data.get("DOI"), str) or data["DOI"].strip().casefold() != doi.casefold():
        raise ZoteroInboxError(f"Zotero 回读 DOI 与原始值不一致：{doi} / {key}")
    if must_have_inbox and INBOX_KEY not in data.get("collections", []):
        raise ZoteroInboxError(f"Zotero 回读条目未进入收件箱：{doi} / {key}")


def verify_inbox(api) -> None:
    """Fail closed if the configured collection is not the user's inbox."""
    collection = api.collection(INBOX_KEY)
    if collection.get("key") != INBOX_KEY or collection.get("data", {}).get("name") != INBOX_NAME:
        raise ZoteroInboxError("目标收件箱 key/name 与预期不符，停止导入")


def import_doi(doi: str, api, writer) -> str:
    """Create a fresh DOI item directly in the inbox, without library deduplication."""
    metadata = resolve_metadata(doi)
    if not isinstance(metadata, dict) or not metadata.get("title"):
        raise ZoteroInboxError(f"Crossref 元数据不足，未导入：{doi}")
    key = writer.add_item(doi=doi, extra_fields={**metadata, "collections": [INBOX_KEY]})
    _verify(api, key, doi)
    return key


def target_date() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read only a CSV with completed semantic columns; never operate on partial output."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        if not columns or not {"doi", "title_zh", *FLAGS}.issubset(columns):
            raise ZoteroInboxError("CSV 缺少 doi、title_zh 或语义筛选列；先完成 03:00 的标注")
        rows = list(reader)
    if any(row.get("title_zh") is None for row in rows):
        raise ZoteroInboxError("CSV 中存在无效中文标题列")
    return rows


def sync_csv(path: Path, api, writer) -> int:
    """Import each selected row once; preserve verified progress after each item."""
    rows = read_rows(path)
    selected = select_rows(rows)
    if not selected:
        return 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        columns = csv.DictReader(handle).fieldnames
    fields = list(dict.fromkeys([*columns, *STATUS_COLUMNS]))
    pending = [row for row in selected if not (row.get("zotero_status") == "added" and row.get("zotero_key"))]
    if not pending:
        return 0
    verify_inbox(api)
    for row in rows:
        if row not in selected and not row.get("zotero_status"):
            row.update(zotero_status="skipped", zotero_key="")
    count = 0
    for row in rows:
        if row not in selected:
            continue
        if row.get("zotero_status") == "added" and row.get("zotero_key"):
            continue  # Only protects a re-run of this CSV; never searches the Zotero library.
        key = import_doi(row["doi"], api, writer)
        row.update(zotero_status="added", zotero_key=key)
        count += 1
        temp = path.with_suffix(path.suffix + ".tmp")
        try:
            with temp.open("w", newline="", encoding="utf-8-sig") as handle:
                writer_csv = csv.DictWriter(handle, fieldnames=fields)
                writer_csv.writeheader()
                writer_csv.writerows(rows)
            temp.replace(path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    return count


def open_zotero():
    """Open the configured Zotero user library through zotero-cli's Web API writer."""
    from zotero_cli.config import load_config, resolve_write_credentials
    from zotero_cli.core.writer import ZoteroWriter

    config = load_config()
    library_id, api_key = resolve_write_credentials(config, library_type="user", group_id=None)
    if not library_id or not api_key:
        raise ZoteroInboxError("zotero-cli 未配置用户库写入权限")
    writer = ZoteroWriter(library_id, api_key)
    return writer._zot, writer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="把昨天 CSV 中语义筛出的 DOI 导入 Zotero 收件箱")
    parser.add_argument("--date", help="可选：指定已存在的 CSV 日期 YYYY-MM-DD；默认昨天")
    parser.add_argument("--dry-run", action="store_true", help="只预览候选 DOI，不访问 Zotero Web API")
    args = parser.parse_args(argv)
    selected_date = args.date or target_date()
    try:
        if date.fromisoformat(selected_date).isoformat() != selected_date:
            raise ValueError("not canonical ISO date")
    except ValueError as exc:
        raise ZoteroInboxError(f"日期格式错误：{selected_date}") from exc
    path = SCRIPT_DIR / f"updates_{selected_date}.csv"
    if not path.is_file():
        raise ZoteroInboxError(f"找不到已完成的 CSV：{path}")
    if args.dry_run:
        rows = read_rows(path)
        selected = select_rows(rows)
        print(f"[dry-run] {len(selected)} 个候选 DOI：{[row['doi'] for row in selected]}")
        return
    api, writer = open_zotero()
    count = sync_csv(path, api, writer)
    print(f"Zotero 收件箱核实完成：{count} 个 DOI 已导入；CSV：{path}")


if __name__ == "__main__":
    try:
        main()
    except (ZoteroInboxError, OSError) as exc:
        raise SystemExit(f"Zotero 收件箱同步失败：{exc}") from exc
