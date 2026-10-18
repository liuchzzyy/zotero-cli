"""Send semantically selected DOI items to the Zotero inbox using zotero-cli's Web API writer.

Run this file with /Users/liuchzzyy/CodeSpace/zotero-cli/.venv/bin/python.
The local Zotero SQLite database is never written to; readback uses the Web API.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from zotero_cli.core.zotero.writer import ZoteroWriter

import zh_text

SCRIPT_DIR = Path("/Users/liuchzzyy/CodeSpace/PaperCrossref")
INBOX_KEY = "AG7NQ5UW"
INBOX_NAME = "00 | 收件箱"
FLAGS = ("is_cathode_material", "is_characterization_technique")
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
STATUS_COLUMNS = ("zotero_status", "zotero_key")
TRANSLATED_TITLE_LABEL = "translated-title:"
REQUIRED_COLUMNS = ("doi", "title_zh", "abstract_zh", *FLAGS)


class ZoteroInboxError(RuntimeError):
    """An inbox operation failed or cannot be verified."""


def select_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return the rows the screening stage marked `selected`.

    The producer writes that column, so the selection policy lives in one place
    (`semantic_enrichment.is_selected`) and the importer does not re-derive it. A
    CSV without the column — one annotated before the three-stage split — falls
    back to the flag rule so an older file still imports correctly.
    """
    chosen = []
    for row in rows:
        doi = row.get("doi")
        if not isinstance(doi, str) or doi != doi.strip() or not DOI_PATTERN.fullmatch(doi):
            raise ZoteroInboxError(f"无效 DOI：{doi!r}")
        if any(row.get(key) not in {"0", "1", "uncertain"} for key in FLAGS):
            raise ZoteroInboxError(f"语义标记缺失或无效：{doi}")
        marker = str(row.get("selected", "")).strip()
        if marker in {"0", "1"}:
            picked = marker == "1"
        else:
            picked = any(row[key] in {"1", "uncertain"} for key in FLAGS)
        if picked:
            chosen.append(row)
    return chosen


def resolve_metadata(doi: str) -> dict:
    """Use zotero-cli's Crossref mapper; never create a bare record on resolution failure."""
    from zotero_cli.core.zotero.metadata import resolve_doi

    fields = resolve_doi(doi)
    if not isinstance(fields, dict) or not fields.get("title"):
        raise ZoteroInboxError(f"Crossref 无完整标题，未导入 DOI：{doi}")
    returned_doi = fields.get("DOI")
    if not isinstance(returned_doi, str) or returned_doi.strip().casefold() != doi.casefold():
        raise ZoteroInboxError(f"Crossref DOI 不匹配，未导入：{doi}")
    return fields


def _verify(api: Any, key: str, doi: str, title_zh: str, abstract_zh: str, *, must_have_inbox: bool = True) -> None:
    item = api.item(key)
    if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
        raise ZoteroInboxError(f"Zotero 回读条目格式无效：{doi} / {key}")
    data = item["data"]
    if not isinstance(data.get("DOI"), str) or data["DOI"].strip().casefold() != doi.casefold():
        raise ZoteroInboxError(f"Zotero 回读 DOI 与原始值不一致：{doi} / {key}")
    if must_have_inbox and INBOX_KEY not in data.get("collections", []):
        raise ZoteroInboxError(f"Zotero 回读条目未进入收件箱：{doi} / {key}")
    extra = data.get("extra")
    expected = f"{TRANSLATED_TITLE_LABEL} {title_zh}"
    if not isinstance(extra, str) or expected not in extra.splitlines():
        raise ZoteroInboxError(f"Zotero 回读条目缺少中文标题：{doi} / {key}")
    # An abstract that was translated must land in abstractNote verbatim; a field
    # the write dropped looks exactly like a successful import. A missing field
    # counts as empty — but then a non-empty translation still fails here.
    if (data.get("abstractNote") or "") != abstract_zh:
        raise ZoteroInboxError(f"Zotero 回读条目中文摘要不一致：{doi} / {key}")


def verify_inbox(api: Any) -> None:
    """Fail closed if the configured collection is not the user's inbox."""
    collection = api.collection(INBOX_KEY)
    if collection.get("key") != INBOX_KEY or collection.get("data", {}).get("name") != INBOX_NAME:
        raise ZoteroInboxError("目标收件箱 key/name 与预期不符，停止导入")


def chinese_fields(title_zh: object, abstract_zh: object, source_abstract: str, doi: str) -> tuple[str, str]:
    """Validate and normalise the Chinese title/abstract before any Zotero write.

    Both fields must be single-line and script-free, with a half-width space at
    every CJK/Latin boundary; `zh_text.normalize` enforces that whatever the
    upstream model emitted. An English abstract that exists must come with a
    Chinese translation — a silently empty field would look like a successful
    import — while a paper Crossref has no abstract for keeps both fields empty.
    """
    if not isinstance(title_zh, str) or not title_zh.strip() or "\n" in title_zh or "\r" in title_zh:
        raise ZoteroInboxError(f"中文标题缺失或含换行，未导入：{doi}")
    if not isinstance(abstract_zh, str):
        raise ZoteroInboxError(f"中文摘要列无效，未导入：{doi}")
    if source_abstract.strip() and not abstract_zh.strip():
        raise ZoteroInboxError(f"缺少中文摘要，未导入：{doi}")
    if not source_abstract.strip() and abstract_zh.strip():
        raise ZoteroInboxError(f"原始摘要为空却提供了中文摘要，未导入：{doi}")
    try:
        normalized_abstract = zh_text.require_chinese_abstract(abstract_zh) if source_abstract.strip() else ""
    except ValueError as exc:
        raise ZoteroInboxError(f"中文摘要无效，未导入：{doi}：{exc}") from exc
    return zh_text.normalize(title_zh), normalized_abstract


def import_doi(
    doi: str, title_zh: str, abstract_zh: str, api: Any, writer: ZoteroWriter, *, source_abstract: str = ""
) -> str:
    """Create a DOI item with a translated title in Extra and a translated abstract."""
    translated_title, translated_abstract = chinese_fields(title_zh, abstract_zh, source_abstract, doi)
    metadata = resolve_metadata(doi)
    if not isinstance(metadata, dict) or not metadata.get("title"):
        raise ZoteroInboxError(f"Crossref 元数据不足，未导入：{doi}")
    existing_extra = metadata.get("extra") or ""
    if not isinstance(existing_extra, str):
        raise ZoteroInboxError(f"Crossref Extra 格式无效，未导入：{doi}")
    other_lines = [
        line for line in existing_extra.splitlines() if not line.strip().lower().startswith(TRANSLATED_TITLE_LABEL)
    ]
    extra = "\n".join([*other_lines, f"{TRANSLATED_TITLE_LABEL} {translated_title}"])
    # The Chinese abstract replaces Crossref's English one; the English text stays
    # available in the CSV and in Crossref itself.
    fields = {**metadata, "extra": extra, "collections": [INBOX_KEY]}
    if translated_abstract:
        fields["abstractNote"] = translated_abstract
    else:
        fields.pop("abstractNote", None)
    key = writer.add_item(doi=doi, extra_fields=fields)
    _verify(api, key, doi, translated_title, translated_abstract)
    return key


def target_date() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read only a CSV with completed semantic columns; never operate on partial output."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames
        if not columns or not set(REQUIRED_COLUMNS).issubset(columns):
            missing = sorted(set(REQUIRED_COLUMNS) - set(columns or []))
            raise ZoteroInboxError(f"CSV 缺少列 {missing}；先完成上游标注（含中文摘要）")
        rows = list(reader)
    if any(row.get("title_zh") is None for row in rows):
        raise ZoteroInboxError("CSV 中存在无效中文标题列")
    return rows


def _replace_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    """Rewrite the CSV atomically, checkpointing after each imported item.

    The temp name is unique per process: a fixed `path + ".tmp"` is shared by
    every writer, so two concurrent runs interleave their rows into one temp file
    and rename the mixture into place. `replace` (rename(2)) then makes the switch
    atomic, so the CSV is always either the previous checkpoint or a complete new
    one.
    """
    temp = path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temp.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        temp.replace(path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def sync_csv(path: Path, api: Any, writer: ZoteroWriter) -> int:
    """Import each selected row once; preserve verified progress after each item."""
    rows = read_rows(path)
    selected = select_rows(rows)
    for row in selected:
        # Validate every row before the first write, so a bad abstract cannot
        # strand a partially imported CSV.
        chinese_fields(row.get("title_zh"), row.get("abstract_zh"), row.get("abstract") or "", row["doi"])
    if not selected:
        return 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        columns = csv.DictReader(handle).fieldnames
    if columns is None:
        raise ZoteroInboxError("CSV 表头在准备写入时丢失")
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
        key = import_doi(
            row["doi"],
            row["title_zh"],
            row.get("abstract_zh") or "",
            api,
            writer,
            source_abstract=row.get("abstract") or "",
        )
        row.update(zotero_status="added", zotero_key=key)
        count += 1
        _replace_csv(path, rows, fields)
    return count


def open_zotero() -> tuple[Any, ZoteroWriter]:
    """Open the configured Zotero user library through zotero-cli's Web API writer."""
    from zotero_cli.config import load_config, resolve_write_credentials
    from zotero_cli.core.zotero.writer import ZoteroWriter

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
    path.unlink()
    print(f"Zotero 收件箱核实完成：{count} 个 DOI 已导入；已删除 CSV：{path}")


if __name__ == "__main__":
    try:
        main()
    except (ZoteroInboxError, OSError) as exc:
        raise SystemExit(f"Zotero 收件箱同步失败：{exc}") from exc
