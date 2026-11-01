"""ai-note2 模板加载、条目类型限制与第二步核对。"""

from __future__ import annotations

import json

import pytest

from zotero_cli.ai_notes2 import templates, verification
from zotero_cli.ai_notes2.errors import AiNote2Error
from zotero_cli.ai_notes2.inputs import SUPPORTED_ITEM_TYPE, InputSnapshot, SourceNote, collect_inputs
from zotero_cli.ai_notes2.validation import allocate_indexes, validate_document
from zotero_cli.zotero.models import Creator, Item, Note

pytestmark = pytest.mark.unit


@pytest.fixture
def allocated(document):
    return allocate_indexes(document)


def _snapshot(document: dict) -> InputSnapshot:
    return InputSnapshot(
        item=_item(),
        library_ref={"library_type": "user", "library_id": "synthetic"},
        source_note=SourceNote("N1", "# AI条目分析 - T", "a" * 64, "AI条目分析 - T"),
        documents=list(document["inputs"]["documents"]),
        source_blocks=list(document["source_blocks"]),
        projection=json.dumps({"blocks": document["source_blocks"]}, ensure_ascii=False),
        character_count=10,
    )


def _item(item_type: str = "journalArticle") -> Item:
    return Item(
        key="ITEM1",
        item_type=item_type,
        title="Synthetic paper",
        creators=[Creator("A", "Author", "author")],
        abstract=None,
        date="2024",
        url=None,
        doi="10.1/x",
        tags=[],
        collections=[],
        date_added="",
        date_modified="",
    )


class Reader:
    def __init__(self, item_type="journalArticle"):
        self._item = _item(item_type)
        self._library_id = 1
        self.pdf_calls = 0

    def get_item(self, key):
        return self._item

    def get_notes(self, key):
        return [Note("N1", "ITEM1", "# AI条目分析 - T\n内容")]

    def get_attachments(self, key):
        self.pdf_calls += 1
        return []


def test_template_includes_all_record_contract_fields():
    from zotero_cli.ai_notes2.models import RECORD_TABLES, note2_json_schema

    contract = templates.schema_fields()
    schema = note2_json_schema()
    for _chain, _table, _identity, prefix in RECORD_TABLES:
        assert prefix in contract
        for field in schema["$defs"][prefix]["required"]:
            assert field in contract
    assert '"source_blocks"' not in contract


def test_prompt_shared_type_references_are_closed():
    contract = json.loads(templates.schema_fields())
    definitions = {**contract["records"], **contract["shared_types"]}

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].split("/")[-1] in definitions
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(contract)


def test_template_renamed_to_evidence_chain():
    assert templates.available_templates() == ["evidence-chain", "verification"]
    assert templates.template_path().name == "evidence-chain.md"
    assert templates.template_path("evidence_chain").name == "evidence-chain.md"


def test_template_placeholders_do_not_eat_paper_braces():
    template = templates.load_template("evidence-chain")
    rendered = templates.format_template(
        template,
        item_key="K1",
        title="A {brace} title",
        authors="X",
        journal="",
        date="2024",
        doi="",
        source_note_key="N1",
        schema_fields="{}",
        source_note="s",
        blocks="b",
    )
    assert "A {brace} title" in rendered
    assert "未知" in rendered
    assert "{schema_fields}" not in rendered


@pytest.mark.parametrize("item_type", ["book", "bookSection", "conferencePaper", "preprint", "review", "thesis"])
def test_only_journal_article_is_supported(item_type):
    reader = Reader(item_type)
    with pytest.raises(AiNote2Error) as error:
        collect_inputs(reader, "ITEM1")
    assert error.value.hint_key == "unsupported_item_type"
    assert "journalArticle" in str(error.value)
    assert reader.pdf_calls == 0


def test_supported_item_type_constant_is_journal_article():
    assert SUPPORTED_ITEM_TYPE == "journalArticle"


def test_external_criterion_is_not_required_to_cite_paper_pdf(document):
    from zotero_cli.ai_notes2.validation import allocate_indexes

    document["review"]["criterion_source_set_id"] = "external-set"
    document["review"]["criterion_sources"] = [
        {
            "criterion_source_id": "criterion",
            "record_meta": {"state": "active", "replaces": [], "replaced_by": [], "note": None},
            "source_identity": {
                "creators": [],
                "title": "判准来源",
                "year": None,
                "doi": None,
                "standard_id": None,
                "url": None,
            },
            "edition_or_version": None,
            "source_revision": 1,
            "content_snapshot_ref": None,
            "retrieved_at": None,
            "access_scope": "仅书目信息",
            "check_status": "待核对",
            "locations": [],
            "purpose_note": "待查的外部判准",
        }
    ]
    allocated = allocate_indexes(document)
    assert verification.check_sources(allocated, _snapshot(document)) == []


def test_program_level_check_rejects_excerpt_not_from_frozen_block(allocated, document):
    snapshot = _snapshot(document)
    assert verification.check_sources(allocated, snapshot) == []

    data = allocated.to_dict()
    data["thought"]["nodes"][0]["text_locations"][0]["text_excerpt"] = "模型编造的原文"
    with pytest.raises(Exception):
        validate_document(data)


def test_program_level_check_flags_unknown_figure(allocated, document):
    snapshot = _snapshot(document)
    data = allocated.to_dict()
    data["evidence"]["evidence_records"][0]["figure_refs"][0]["figure_id"] = "S99"
    errors = verification.check_sources(allocated, snapshot)
    assert errors == []
    # 直接篡改快照图注后，同一文档应被核对层报出。
    snapshot.source_blocks[0]["figure_ids"] = []
    errors = verification.check_sources(allocated, snapshot)
    assert any("图" in item for item in errors)


def test_verification_accepts_single_json_fence_and_checks_contract():
    from zotero_cli.ai_notes2.validation import Note2ValidationError

    raw = '```json\n{"verdicts": [{"object_id": "X", "status": "无法核对", "source_refs": [], "note": "待核"}]}\n```'
    assert verification._parse_verdicts(raw, {"X": set()})[0]["object_id"] == "X"
    with pytest.raises(Note2ValidationError):
        verification._parse_verdicts(raw, {"Y": set()})
    with pytest.raises(Note2ValidationError):
        verification._parse_verdicts(raw + " extra", {"X": set()})


def test_diagnostic_artifacts_preserve_generated_document_and_raw(tmp_path):
    from zotero_cli.ai_notes2.execution import _write_diagnostic

    directory = tmp_path / "diagnostic"
    _write_diagnostic(directory, "generated.json", '{"a":1}')
    _write_diagnostic(directory, "verification-response.txt", "not-json")
    assert (directory / "generated.json").read_text() == '{"a":1}'
    assert (directory / "verification-response.txt").read_text() == "not-json"
    assert (directory.stat().st_mode & 0o077) == 0
    assert ((directory / "verification-response.txt").stat().st_mode & 0o077) == 0
    with pytest.raises(FileExistsError):
        _write_diagnostic(directory, "generated.json", "replacement")


def test_failed_verification_retains_raw_response(allocated, document, monkeypatch):
    snapshot = _snapshot(document)
    monkeypatch.setattr(verification, "model_supports_request", lambda *args, **kwargs: True)

    class Client:
        config = type("Config", (), {"model": "m", "max_tokens": 0})()
        last_model = "m"
        last_finish_reason = "stop"
        last_usage = None

        def get_model_chain(self):
            return ["m"]

        def chat(self, prompt, **kwargs):
            return "not-json"

    captured = []
    with pytest.raises(Exception):
        verification.verify_document(allocated, snapshot, client=Client(), response_sink=captured.append)
    assert captured == ["not-json"]


def test_model_verification_marks_unsupported_without_rewriting(allocated, document, monkeypatch):
    snapshot = _snapshot(document)
    monkeypatch.setattr(verification, "model_supports_request", lambda *args, **kwargs: True)

    class Client:
        last_model = "m"
        last_usage = None
        config = type("Config", (), {"model": "m", "max_tokens": 0})()

        def get_model_chain(self):
            return ["m"]

        def chat(self, prompt, *, figures=None, progress=None, allowed_models=None):
            assert figures == []
            assert "<untrusted-document>" in prompt
            return json.dumps(
                {
                    "verdicts": [
                        {
                            "object_id": entry["object_id"],
                            "status": "不支持" if entry["object_id"] == "EC-E001" else "支持",
                            "source_refs": (
                                []
                                if entry["object_id"] == "RC-A001"
                                else [
                                    {
                                        "source_set_id": "set-1",
                                        "attachment_key": "SYNATT01",
                                        "block_id": "B001",
                                    }
                                ]
                            ),
                            "note": "方向相反" if entry["object_id"] == "EC-E001" else "已核对",
                        }
                        for entry in allocated.to_dict()["object_registry"]
                    ]
                }
            )

    report = verification.verify_document(allocated, snapshot, client=Client())
    assert report.model_used is True
    assert [item["object_id"] for item in report.unsupported] == ["EC-E001"]
    assert allocated.to_dict()["evidence"]["evidence_records"][0]["reported_result"]["statement"].startswith("任意原值")


def test_model_verification_rejects_missing_and_duplicate_verdicts(allocated, document, monkeypatch):
    snapshot = _snapshot(document)
    monkeypatch.setattr(verification, "model_supports_request", lambda *args, **kwargs: True)

    class Client:
        last_model = "m"
        last_usage = None
        config = type("Config", (), {"model": "m", "max_tokens": 0})()
        response = {"verdicts": []}

        def get_model_chain(self):
            return ["m"]

        def chat(self, prompt, *, figures=None, progress=None, allowed_models=None):
            return json.dumps(self.response)

    client = Client()
    with pytest.raises(Exception):
        verification.verify_document(allocated, snapshot, client=client)
    identifiers = [entry["object_id"] for entry in allocated.to_dict()["object_registry"]]
    client.response = {
        "verdicts": [{"object_id": item, "status": "支持", "source_refs": [], "note": "已核对"} for item in identifiers]
    }
    client.response["verdicts"].append({"object_id": identifiers[0], "status": "支持"})
    with pytest.raises(Exception):
        verification.verify_document(allocated, snapshot, client=client)


def test_model_verification_rejects_unknown_object_ids(allocated, document, monkeypatch):
    snapshot = _snapshot(document)
    monkeypatch.setattr(verification, "model_supports_request", lambda *args, **kwargs: True)

    class Client:
        last_model = "m"
        last_usage = None
        config = type("Config", (), {"model": "m", "max_tokens": 0})()

        def get_model_chain(self):
            return ["m"]

        def chat(self, prompt, *, figures=None, progress=None, allowed_models=None):
            return json.dumps({"verdicts": [{"object_id": "INVENTED", "status": "支持"}]})

    with pytest.raises(Exception):
        verification.verify_document(allocated, snapshot, client=Client())
