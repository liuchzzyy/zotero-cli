"""Read-only inventory: which CSV DOIs are already in the Zotero inbox.

No writes. Used to decide whether an import run would create duplicates.
"""

import csv
import sys
from pathlib import Path

from pyzotero import zotero

from zotero_cli.config import load_config, resolve_write_credentials

INBOX = "AG7NQ5UW"
CSV = Path("/Users/liuchzzyy/CodeSpace/PaperCrossref/updates_2026-10-16.csv")

cfg = load_config()
library_id, api_key = resolve_write_credentials(cfg, library_type="user")
z = zotero.Zotero(str(library_id), "user", api_key)

items = z.everything(z.collection_items_top(INBOX))
inbox_dois: dict[str, list[str]] = {}
for item in items:
    data = item.get("data", {})
    if data.get("itemType") in {"note", "attachment"}:
        continue
    doi = (data.get("DOI") or "").strip().casefold()
    if doi:
        inbox_dois.setdefault(doi, []).append(data.get("key"))

print(f"inbox items: {len(items)} | distinct DOIs: {len(inbox_dois)}")

if not CSV.is_file():
    print(f"CSV missing: {CSV}")
    sys.exit(0)

with CSV.open(encoding="utf-8-sig", newline="") as handle:
    reader = csv.DictReader(handle)
    rows = list(reader)

print(f"CSV rows: {len(rows)} | columns: {reader.fieldnames}")
csv_dois = {row["doi"].casefold() for row in rows}
overlap = csv_dois.intersection(inbox_dois)
print(f"CSV DOIs already in inbox: {len(overlap)}")

duplicates = {doi: keys for doi, keys in inbox_dois.items() if len(keys) > 1}
print(f"inbox DOIs already duplicated: {len(duplicates)} | extra copies: {sum(len(k) - 1 for k in duplicates.values())}")

selected = [row for row in rows if (row.get("selected") or "").strip() == "1"]
print(f"rows marked selected: {len(selected)}")
selected_overlap = {row["doi"].casefold() for row in selected}.intersection(inbox_dois)
print(f"selected rows already in inbox: {len(selected_overlap)}")
