"""Offline safety regressions through real staging, checkpoints and write code."""

from __future__ import annotations

import copy
import csv
import io
import json
from pathlib import Path
from typing import Any

import batch_translate_metadata as batch_zh
import httpx
import pytest
import translate_abstracts as abstracts
import zotero_inbox as inbox
from pyzotero import zotero

from zotero_cli.zotero.writer import ZoteroWriter
from zotero_cli.zotero.writer_errors import ZoteroWriteError

KEY = "PAPER001"
TITLE = "A fixture paper about cathodes"
DOI = "10.1000/fixture"
SOURCE = "The fixture investigates a cathode using controlled measurements and reports the resulting observations. " * 2
CORRECTED_SOURCE = "The corrected fixture reports a different observation supported by revised measurements. " * 2
OLD_ZH = "这是旧输入对应的测试译文，不应复用于已经修正的摘要。"
NEW_ZH = "这是修正后输入对应的测试译文，仅供离线安全测试使用。"


def item(abstract: str = SOURCE, *, title: str = TITLE, doi: str = DOI) -> dict[str, Any]:
    return {
        "key": KEY,
        "version": 7,
        "library": {"type": "user", "id": 123},
        "links": {},
        "meta": {},
        "data": {
            "key": KEY,
            "version": 7,
            "itemType": "journalArticle",
            "title": title,
            "DOI": doi,
            "abstractNote": abstract,
            "extra": "funder: fixture\ntranslated-title: 测试标题",
        },
    }


class LibraryAPI:
    """Only the remote API boundary is replaced; writer and tools stay real."""

    def __init__(self, current: dict[str, Any]) -> None:
        self.current = copy.deepcopy(current)
        self.writes: list[dict[str, Any]] = []

    def item(self, key: str) -> dict[str, Any]:
        assert key == KEY
        return copy.deepcopy(self.current)

    def update_item(self, payload: dict[str, Any]) -> bool:
        assert payload["key"] == KEY
        assert payload["version"] == self.current["version"], "API version conflict"
        self.writes.append(copy.deepcopy(payload))
        self.current["data"].update(payload["data"])
        self.current["version"] += 1
        self.current["data"]["version"] = self.current["version"]
        return True

    def collection_items(self, _key: str) -> list[dict[str, Any]]:
        return [self.item(KEY)]

    def everything(self, values: Any) -> Any:
        return values


def writer_for(api: Any) -> ZoteroWriter:
    writer = object.__new__(ZoteroWriter)
    writer._zot = api
    return writer


class ModelAPI:
    """Return a model response at the chat/translation service boundary."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.calls: list[Any] = []

    def translate(self, rows: list[dict[str, Any]]) -> dict[str, str]:
        self.calls.append(copy.deepcopy(rows))
        return {row["key"]: self.target for row in rows}

    def chat(self, prompt: str, **_kwargs: Any) -> str:
        self.calls.append(prompt)
        rows = json.loads(prompt.split("输入 JSON：\n", 1)[1])
        return json.dumps(
            [{"key": row["key"], "title": row["title"], "abstract_zh": self.target} for row in rows],
            ensure_ascii=False,
        )


class HTTPAPI(LibraryAPI):
    """Real pyzotero uses this versioned HTTP transport, never a live service."""

    def __init__(self) -> None:
        super().__init__(item())
        self.requests: list[httpx.Request] = []
        self.item_reads = 0
        self.change_on_read: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET" and path == "/itemFields":
            return httpx.Response(200, json=[{"field": field} for field in self.current["data"]])
        if request.method == "GET" and path == f"/users/123/items/{KEY}":
            self.item_reads += 1
            if self.item_reads == self.change_on_read:
                self.current["data"]["abstractNote"] = NEW_ZH
                self.current["version"] += 1
                self.current["data"]["version"] = self.current["version"]
            return httpx.Response(200, json=self.current)
        if request.method == "GET" and path == "/users/123/collections/COLL0001/items":
            return httpx.Response(200, json=[self.current])
        if request.method == "PATCH" and path == f"/users/123/items/{KEY}":
            if request.headers.get("If-Unmodified-Since-Version") != str(self.current["version"]):
                return httpx.Response(412, text="Version conflict")
            self.current["data"].update(json.loads(request.content))
            self.current["version"] += 1
            self.current["data"]["version"] = self.current["version"]
            return httpx.Response(204, headers={"Last-Modified-Version": str(self.current["version"])})
        raise AssertionError(f"Unexpected offline request: {request.method} {request.url}")


def candidates_for(pipeline: str, api: Any, tmp_path: Path) -> list[dict[str, Any]]:
    if pipeline == "abstracts":
        return abstracts.select_items_to_translate([api.item(KEY)])[0]
    cfg = type("Config", (), {"data_dir": tmp_path, "crossref_mailto": "fixture@example.invalid"})()
    return batch_zh.inventory_collection(api, None, cfg, "COLL0001")[0]


def stage(pipeline: str, rows: list[dict[str, Any]], path: Path, model: ModelAPI) -> list[dict[str, Any]]:
    if pipeline == "abstracts":
        return abstracts.stage_translations(rows, path, custom_translator=model.translate)
    return batch_zh.stage_translations(rows, path, model, max_workers=1)


def apply(pipeline: str, rows: list[dict[str, Any]], path: Path, api: LibraryAPI, model: ModelAPI) -> dict[str, int]:
    if pipeline == "abstracts":
        return abstracts.run_abstract_translation(
            api, writer_for(api), rows, path, dry_run=False, custom_translator=model.translate
        )
    return batch_zh.apply_updates_from_state(api, writer_for(api), path, max_workers=1)


@pytest.mark.parametrize("pipeline", ["abstracts", "batch_zh"])
@pytest.mark.parametrize("changed_field", ["abstractNote", "title", "DOI"])
def test_resume_never_applies_translation_of_changed_input(pipeline: str, changed_field: str, tmp_path: Path) -> None:
    api = LibraryAPI(item())
    path = tmp_path / "checkpoint.json"
    first_model = ModelAPI(OLD_ZH)
    stage(pipeline, candidates_for(pipeline, api, tmp_path), path, first_model)
    assert json.loads(path.read_text())[0]["abstract_zh"] == OLD_ZH

    api.current["data"][changed_field] = {
        "abstractNote": CORRECTED_SOURCE,
        "title": "A corrected fixture title",
        "DOI": "10.1000/corrected-fixture",
    }[changed_field]
    fresh_rows = candidates_for(pipeline, api, tmp_path)
    fresh_model = ModelAPI(NEW_ZH)
    stage(pipeline, fresh_rows, path, fresh_model)
    result = apply(pipeline, fresh_rows, path, api, fresh_model)

    assert fresh_model.calls, "Changed title/DOI/source must invalidate the cached translation"
    assert result.get("updated", result.get("abstract_updated")) == 1
    assert api.current["data"]["abstractNote"] == NEW_ZH
    assert len(api.writes) == 1
    saved = json.loads(path.read_text())[0]
    assert saved["abstract_zh"] == NEW_ZH
    assert saved.get("status", saved.get("abstract_status")) == "updated"


@pytest.mark.parametrize("pipeline", ["abstracts", "batch_zh"])
@pytest.mark.parametrize("changed_field", ["abstractNote", "chinese_abstract", "title", "DOI"])
def test_apply_rereads_and_refuses_changes_since_staging(pipeline: str, changed_field: str, tmp_path: Path) -> None:
    api = LibraryAPI(item())
    path = tmp_path / "checkpoint.json"
    rows = candidates_for(pipeline, api, tmp_path)
    model = ModelAPI(OLD_ZH)
    stage(pipeline, rows, path, model)
    if changed_field == "chinese_abstract":
        api.current["data"]["abstractNote"] = NEW_ZH
    else:
        api.current["data"][changed_field] = {
            "abstractNote": CORRECTED_SOURCE,
            "title": "A different title before apply",
            "DOI": "10.1000/different-before-apply",
        }[changed_field]
    before = copy.deepcopy(api.current)

    try:
        result = apply(pipeline, rows, path, api, model)
    except abstracts.AbstractTranslationError:
        result = {"abstract_failed": 1}

    assert not api.writes, "A staged translation must not overwrite changed remote metadata"
    assert api.current == before
    assert result.get("abstract_failed") == 1
    saved = json.loads(path.read_text())[0]
    assert saved.get("status", saved.get("abstract_status")) != "updated"


@pytest.mark.parametrize("pipeline", ["abstracts", "batch_zh"])
def test_apply_accepts_exact_raw_source_with_abstract_label(pipeline: str, tmp_path: Path) -> None:
    api = LibraryAPI(item("Abstract: " + SOURCE))
    path = tmp_path / "checkpoint.json"
    rows = candidates_for(pipeline, api, tmp_path)
    model = ModelAPI(NEW_ZH)
    stage(pipeline, rows, path, model)
    result = apply(pipeline, rows, path, api, model)
    assert result.get("updated", result.get("abstract_updated")) == 1
    assert api.current["data"]["abstractNote"] == NEW_ZH
    assert len(api.writes) == 1


@pytest.mark.parametrize("pipeline", ["abstracts", "batch_zh"])
def test_force_does_not_overwrite_a_different_valid_chinese_abstract(pipeline: str, tmp_path: Path) -> None:
    api = LibraryAPI(item(OLD_ZH))
    # Force/recovery callers can provide a Chinese source, but the final write
    # guard must still preserve a different, valid Chinese abstract.
    rows = [{"key": KEY, "title": TITLE, "doi": DOI, "abstract": OLD_ZH, "source_abstract": OLD_ZH}]
    path = tmp_path / "checkpoint.json"
    model = ModelAPI(NEW_ZH)
    stage(pipeline, rows, path, model)
    try:
        apply(pipeline, rows, path, api, model)
    except abstracts.AbstractTranslationError:
        pass
    assert not api.writes
    assert api.current["data"]["abstractNote"] == OLD_ZH


@pytest.mark.parametrize("pipeline", ["abstracts", "batch_zh"])
@pytest.mark.parametrize("concurrent_edit", [True, False], ids=["version-conflict", "unchanged-version"])
def test_apply_uses_first_read_version_through_real_http_transport(
    pipeline: str, concurrent_edit: bool, tmp_path: Path
) -> None:
    transport = HTTPAPI()
    path = tmp_path / "checkpoint.json"
    model = ModelAPI(OLD_ZH)
    with httpx.Client(transport=httpx.MockTransport(transport), trust_env=False) as client:
        remote = zotero.Zotero("123", "user", "offline-test-key", client=client)
        writer = writer_for(remote)
        rows = candidates_for(pipeline, remote, tmp_path)
        stage(pipeline, rows, path, model)
        transport.item_reads = 0
        transport.change_on_read = 2 if concurrent_edit else None
        if pipeline == "abstracts":
            if concurrent_edit:
                with pytest.raises(ZoteroWriteError) as caught:
                    abstracts.run_abstract_translation(
                        remote, writer, rows, path, dry_run=False, custom_translator=model.translate
                    )
                assert caught.value.code == "conflict"
                assert caught.value.retryable is False
            else:
                result = abstracts.run_abstract_translation(
                    remote, writer, rows, path, dry_run=False, custom_translator=model.translate
                )
                assert result["updated"] == 1
        else:
            result = batch_zh.apply_updates_from_state(remote, writer, path, max_workers=1)
            assert result["abstract_failed" if concurrent_edit else "abstract_updated"] == 1

    patches = [request for request in transport.requests if request.method == "PATCH"]
    assert len(patches) == (0 if concurrent_edit else 1)
    if patches:
        assert patches[0].headers["If-Unmodified-Since-Version"] == "7"
        assert json.loads(patches[0].content)["abstractNote"] == OLD_ZH
    assert transport.current["data"]["abstractNote"] == (NEW_ZH if concurrent_edit else OLD_ZH)


@pytest.mark.parametrize("raw_abstract", ["", None, "DOI: 10.1000/fixture"])
def test_recovered_abstract_is_applied_only_over_exact_inventoried_source(
    raw_abstract: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = LibraryAPI(item())
    api.current["data"]["abstractNote"] = raw_abstract
    requests = []

    def crossref_response(request: Any, **_kwargs: Any) -> io.BytesIO:
        requests.append(request.full_url)
        assert request.full_url.startswith("https://api.crossref.org/works/")
        return io.BytesIO(json.dumps({"message": {"abstract": SOURCE}}).encode())

    monkeypatch.setattr(batch_zh.urllib.request, "urlopen", crossref_response)
    path = tmp_path / "checkpoint.json"
    rows = candidates_for("batch_zh", api, tmp_path)
    model = ModelAPI(NEW_ZH)
    stage("batch_zh", rows, path, model)
    result = apply("batch_zh", rows, path, api, model)
    assert result["abstract_updated"] == 1
    assert api.current["data"]["abstractNote"] == NEW_ZH
    assert len(api.writes) == 1
    assert len(requests) == 1


@pytest.mark.parametrize("target", ["摘要：", " 摘要 \t：  ", "Abstract: 摘要：", "摘要：Abstract:", "English only"])
def test_inbox_rejects_empty_or_non_chinese_normalized_abstract(target: str) -> None:
    with pytest.raises(inbox.ZoteroInboxError, match="中文摘要"):
        inbox.chinese_fields("测试标题", target, SOURCE, DOI)


def test_inbox_validates_normalized_batch_before_first_write(tmp_path: Path) -> None:
    rows = [
        {
            "doi": DOI,
            "title_zh": "测试标题",
            "abstract": SOURCE,
            "abstract_zh": NEW_ZH,
            "is_cathode_material": "1",
            "is_characterization_technique": "0",
        },
        {
            "doi": "10.1000/second-fixture",
            "title_zh": "另一个测试标题",
            "abstract": SOURCE,
            "abstract_zh": "摘要：",
            "is_cathode_material": "1",
            "is_characterization_technique": "0",
        },
    ]
    path = tmp_path / "inbox.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    before = path.read_bytes()
    # No boundary method exists: validation must finish before even checking
    # the inbox collection or resolving DOI metadata.
    with pytest.raises(inbox.ZoteroInboxError, match="中文摘要"):
        inbox.sync_csv(path, object(), object())
    assert path.read_bytes() == before


@pytest.mark.parametrize("target", ["摘要：", "Abstract: 摘要：", "摘要：Abstract:", "English only"])
def test_model_parser_rejects_empty_or_non_chinese_normalized_abstract(target: str) -> None:
    rows = [{"key": KEY, "title": TITLE, "abstract": SOURCE}]
    response = json.dumps([{"key": KEY, "title": TITLE, "abstract_zh": target}])
    with pytest.raises(abstracts.AbstractTranslationError, match="中文摘要"):
        abstracts.parse_model_response(response, rows)


@pytest.mark.parametrize("pipeline", ["abstracts", "batch_zh"])
def test_staging_does_not_checkpoint_an_empty_normalized_translation(
    pipeline: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = LibraryAPI(item())
    rows = candidates_for(pipeline, api, tmp_path)
    path = tmp_path / "checkpoint.json"
    # Retry delays are not part of the safety property.
    monkeypatch.setattr(batch_zh.time, "sleep", lambda _seconds: None)
    with pytest.raises(abstracts.AbstractTranslationError, match="中文摘要"):
        stage(pipeline, rows, path, ModelAPI("摘要："))
    assert not path.exists()
    assert not api.writes


@pytest.mark.parametrize("pipeline", ["abstracts", "batch_zh"])
@pytest.mark.parametrize("target", ["摘要：", "", None, "English only"])
def test_invalid_translation_in_existing_checkpoint_cannot_clear_abstract(
    pipeline: str, target: str | None, tmp_path: Path
) -> None:
    api = LibraryAPI(item())
    rows = candidates_for(pipeline, api, tmp_path)
    path = tmp_path / "checkpoint.json"
    model = ModelAPI(NEW_ZH)
    stage(pipeline, rows, path, model)
    saved = json.loads(path.read_text())
    saved[0]["abstract_zh"] = target
    path.write_text(json.dumps(saved), encoding="utf-8")
    try:
        result = apply(pipeline, rows, path, api, model)
    except abstracts.AbstractTranslationError:
        result = {"abstract_failed": 1}
    assert result.get("abstract_failed") == 1
    assert not api.writes
    assert api.current["data"]["abstractNote"] == SOURCE


@pytest.mark.parametrize("raw", ["这是一个简短但完整的中文测试摘要。", "摘要：这是一个简短但完整的中文测试摘要。"])
def test_batch_inventory_preserves_short_valid_chinese_without_recovery(
    raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = LibraryAPI(item(raw))

    def unexpected_recovery(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("A short valid Chinese abstract is not a publisher stub")

    monkeypatch.setattr(batch_zh.urllib.request, "urlopen", unexpected_recovery)
    assert candidates_for("batch_zh", api, tmp_path) == []
    assert not api.writes


def test_abstract_collection_name_ambiguity_is_rejected_before_fetching_items() -> None:
    class CollectionsAPI:
        def collection(self, _spec: str) -> None:
            raise LookupError("Not a key")

        def collections(self) -> list[dict[str, Any]]:
            return [
                {"key": "COLL0001", "data": {"name": "Fixture collection"}},
                {"key": "COLL0002", "data": {"name": "Fixture collection"}},
            ]

        def everything(self, values: Any) -> Any:
            return values

        def collection_items(self, _key: str) -> list[Any]:
            return []

    with pytest.raises(abstracts.AbstractTranslationError, match="COLL0001.*COLL0002"):
        abstracts.resolve_collection_items(CollectionsAPI(), "fixture COLLECTION")
