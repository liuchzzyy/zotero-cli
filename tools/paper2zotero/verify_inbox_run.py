"""Verify this run's imports only, through the Web API.

The inbox accumulates papers across runs, and items imported before the Chinese
abstract feature legitimately hold Crossref's English abstract and an
unnormalised title. Scope the checks to a dateAdded cutoff (UTC, as the API
returns it) so a pre-existing row cannot mask or fake a regression.
"""

import re
import sys
from collections import Counter

from pyzotero import zotero

from zotero_cli.config import load_config, resolve_write_credentials

INBOX = "AG7NQ5UW"
LABEL = "translated-title:"
# Zotero returns dateAdded in UTC; this run finished 17:34 CST = 09:34 UTC.
CUTOFF = sys.argv[1] if len(sys.argv) > 1 else "2026-10-17T09:30"

CJK = r"[\u3400-\u4dbf\u4e00-\u9fff]"
LAT = r"[0-9A-Za-z\u0370-\u03ff\u1f00-\u1fff]"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


def has_script(text: str) -> bool:
    """Same three representations `zh_text.has_script` rejects."""
    if re.search(r"</?(?:sup|sub)\b", text, re.IGNORECASE):
        return True
    if re.search(r"[_^]\{[^{}]*\}", text):
        return True
    return any("\u2070" <= ch <= "\u209c" for ch in text)


cfg = load_config()
library_id, api_key = resolve_write_credentials(cfg, library_type="user")
z = zotero.Zotero(str(library_id), "user", api_key)

items = z.everything(z.collection_items_top(INBOX))
papers = [i["data"] for i in items if i["data"].get("itemType") not in {"note", "attachment"}]
fresh = [d for d in papers if (d.get("dateAdded") or "") >= CUTOFF]
print(f"inbox papers: {len(papers)} | added at/after {CUTOFF}: {len(fresh)}\n")

check("this run imported 33 papers", len(fresh) == 33, str(len(fresh)))

dois = [d.get("DOI", "").strip().casefold() for d in fresh]
check("every paper has a DOI", all(dois), f"{sum(1 for d in dois if not d)} missing")
dupes = [doi for doi, n in Counter(dois).items() if n > 1]
check("no duplicate DOI within this run", not dupes, str(dupes[:5]))

with_title = [d for d in fresh if any(line.startswith(LABEL) for line in (d.get("extra") or "").splitlines())]
check("every paper carries translated-title in Extra", len(with_title) == len(fresh),
      f"{len(with_title)}/{len(fresh)}")

with_abs = [d for d in fresh if (d.get("abstractNote") or "").strip()]
check("26 papers carry a Chinese abstract", len(with_abs) == 26, str(len(with_abs)))
check("7 papers with no Crossref abstract keep the field empty", len(fresh) - len(with_abs) == 7,
      str(len(fresh) - len(with_abs)))
check("every stored abstract is Chinese, not the English original",
      all(any("\u4e00" <= ch <= "\u9fff" for ch in (d.get("abstractNote") or "")) for d in with_abs))

script_hits, unspaced, doubled, split_unit, space_before_punct, labelled = [], [], [], [], [], []
for data in fresh:
    extra = data.get("extra") or ""
    line = next((ln for ln in extra.splitlines() if ln.startswith(LABEL)), "")
    title = line[len(LABEL):].strip()
    abstract = data.get("abstractNote") or ""
    for field, text in (("title", title), ("abstract", abstract)):
        if not text:
            continue
        if has_script(text):
            script_hits.append((data.get("key"), field))
        if re.search(rf"(?<={CJK})(?={LAT})|(?<={LAT})(?={CJK})", text):
            unspaced.append((data.get("key"), field))
        if re.search(r"\S  +\S", text):
            doubled.append((data.get("key"), field))
        if re.search(r"[A-Za-z] - [A-Za-z]", text):
            split_unit.append((data.get("key"), field))
        if re.search(r"\s[，。、：；！？）]", text):
            space_before_punct.append((data.get("key"), field))
    if title.startswith("摘要"):
        labelled.append(data.get("key"))

check("no super/subscript in any stored title or abstract", not script_hits, str(script_hits[:3]))
check("0 unspaced CJK/Latin boundaries", not unspaced, str(unspaced[:3]))
check("0 doubled spaces", not doubled, str(doubled[:3]))
check("0 split units", not split_unit, str(split_unit[:3]))
check("0 spaces before CJK punctuation", not space_before_punct, str(space_before_punct[:3]))
check("no title starts with a stray 摘要 label", not labelled, str(labelled[:3]))

sample = next((d for d in fresh if (d.get("abstractNote") or "").strip()), None)
if sample:
    line = next(ln for ln in (sample["extra"] or "").splitlines() if ln.startswith(LABEL))
    print("\nsample:", sample.get("key"), "|", sample.get("DOI"))
    print("  Extra   :", line[:110])
    print("  abstract:", (sample.get("abstractNote") or "")[:150])

print(f"\n{len(failures)} failed check(s)")
sys.exit(1 if failures else 0)
