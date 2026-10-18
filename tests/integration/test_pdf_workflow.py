"""Integration coverage for the MinerU-only shared PDF pipeline."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.support import invoke_cli

from zotero_cli.config import PdfConfig
from zotero_cli.core.mineru import MinerUClient, MinerUError, MinerUParseCache, MinerUParseResult
from zotero_cli.core.mineru.client import _RemoteParse
from zotero_cli.core.rag import chunk_mineru_content


def _archive() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("full.md", "# Attention\nCloud parsed Markdown")
        archive.writestr(
            "attention_content_list.json",
            json.dumps(
                [
                    {"type": "text", "text": "Introduction", "text_level": 1, "page_idx": 0},
                    {"type": "text", "text": "structured attention evidence", "page_idx": 0},
                ]
            ),
        )
    return output.getvalue()


class _Response:
    def __init__(self, status_code: int = 200, payload: dict | None = None, content: bytes = b"") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, archive: bytes) -> None:
        self.archive = archive
        self.remote_name = ""

    def post(self, _url: str, *, json: dict, **_kwargs: object) -> _Response:
        self.remote_name = json["files"][0]["name"]
        return _Response(payload={"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["upload://one"]}})

    def put(self, _url: str, **_kwargs: object) -> _Response:
        return _Response(status_code=200)

    def get(self, url: str, **_kwargs: object) -> _Response:
        if "extract-results" in url:
            return _Response(
                payload={
                    "code": 0,
                    "data": {
                        "extract_result": [
                            {"file_name": self.remote_name, "state": "done", "full_zip_url": "download://one"}
                        ]
                    },
                }
            )
        return _Response(content=self.archive)


def test_official_upload_poll_download_flow_returns_markdown_and_json(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    client = MinerUClient("token", session=_Session(_archive()))  # type: ignore[arg-type]
    result = client.parse_many([pdf])[pdf]
    assert isinstance(result, _RemoteParse)
    assert "Cloud parsed Markdown" in result.markdown
    assert result.content_list[1]["text"] == "structured attention evidence"


def test_ai_markdown_and_rag_json_share_one_cloud_parse(tmp_path) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"pdf")
    remote = _RemoteParse(
        markdown="# Attention\nCloud parsed Markdown",
        content_list=[{"type": "text", "text": "structured attention evidence", "page_idx": 0}],
        archive=_archive(),
        archive_members=["full.md", "attention_content_list.json"],
        batch_id="batch-1",
    )
    client = MagicMock()
    client.parse_many.return_value = {pdf: remote}
    cache = MinerUParseCache(tmp_path / "cache", client=client, model_version="vlm")

    ai_input = cache.ensure(pdf).markdown
    rag_input = cache.ensure(pdf).content_list
    rag_chunks = chunk_mineru_content(rag_input, "Attention")

    assert "Cloud parsed Markdown" in ai_input
    assert "structured attention evidence" in rag_chunks[0].content
    client.parse_many.assert_called_once()


def test_pdf_cli_reads_canonical_markdown_and_structured_json(tmp_path) -> None:
    root = tmp_path / "parsed"
    root.mkdir()
    markdown_path = root / "document.md"
    content_path = root / "content_list.json"
    manifest_path = root / "manifest.json"
    markdown_path.write_text("# Attention\nCloud parsed Markdown")
    content_path.write_text(json.dumps([{"type": "text", "text": "evidence"}]))
    manifest_path.write_text("{}")
    parsed = MinerUParseResult(
        source_path=Path("attention.pdf"),
        fingerprint="a" * 64,
        model_version="vlm",
        root=root,
        markdown_path=markdown_path,
        content_list_path=content_path,
        manifest_path=manifest_path,
    )
    cache = MagicMock()
    cache.lookup.return_value = (parsed, parsed.fingerprint)

    with (
        patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache),
        patch("zotero_cli.commands.attachments.pdf.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
    ):
        markdown_result = invoke_cli(["pdf", "ATTN001"])
        structured_result = invoke_cli(["pdf", "ATTN001", "--structured"], json_output=True)

    assert markdown_result.exit_code == 0
    assert "Cloud parsed Markdown" in markdown_result.output
    assert json.loads(structured_result.output)["data"]["content_list"][0]["text"] == "evidence"


def test_pdf_cli_missing_mineru_token_is_configuration_error() -> None:
    cache = MagicMock()
    cache.lookup.return_value = (None, None)
    with (
        patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache),
        patch("zotero_cli.commands.attachments.pdf.load_pdf_config", return_value=PdfConfig()),
    ):
        result = invoke_cli(["pdf", "ATTN001"])
    assert result.exit_code == 3
    assert "MinerU API token is not configured" in result.output
    cache.ensure.assert_not_called()


def _cached_package(
    tmp_path: Path, *, markdown: bytes = b"# Attention\nCloud parsed Markdown", content_list: str | None = None
) -> MinerUParseResult:
    root = tmp_path / "parsed"
    root.mkdir()
    markdown_path = root / "document.md"
    content_path = root / "content_list.json"
    manifest_path = root / "manifest.json"
    markdown_path.write_bytes(markdown)
    content_path.write_text(
        content_list if content_list is not None else json.dumps([{"type": "text", "text": "evidence"}])
    )
    manifest_path.write_text("{}")
    return MinerUParseResult(
        source_path=Path("attention.pdf"),
        fingerprint="a" * 64,
        model_version="vlm",
        root=root,
        markdown_path=markdown_path,
        content_list_path=content_path,
        manifest_path=manifest_path,
    )


def _cache_for(parsed: MinerUParseResult) -> MagicMock:
    cache = MagicMock()
    cache.lookup.return_value = (parsed, parsed.fingerprint)
    return cache


def test_pdf_cli_invalid_model_version_is_configuration_error() -> None:
    result = invoke_cli(
        ["pdf", "ATTN001"],
        json_output=True,
        env={"ZOT_MINERU_MODEL_VERSION": "../vlm"},
    )
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "configuration_error"
    assert "model version" in payload["error"]["message"]
    assert (result.stderr or "") == ""


def test_pdf_cli_cache_configuration_error_is_configuration_error() -> None:
    with patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", side_effect=MinerUError("bad model version")):
        result = invoke_cli(["pdf", "ATTN001"], json_output=True)
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "configuration_error"
    assert "bad model version" in payload["error"]["message"]


def test_pdf_cli_unreadable_pdf_is_typed_runtime_error() -> None:
    cache = MagicMock()
    cache.lookup.side_effect = OSError("permission denied")
    with patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache):
        result = invoke_cli(["pdf", "ATTN001"], json_output=True)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "runtime_error"
    assert "permission denied" in payload["error"]["message"]
    assert "Traceback" not in result.output


def test_pdf_cli_unsafe_cache_state_is_typed_internal_error() -> None:
    cache = MagicMock()
    cache.lookup.side_effect = MinerUError("MinerU cache path must stay strictly inside its fingerprint directory")
    with patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache):
        result = invoke_cli(["pdf", "ATTN001"], json_output=True)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "internal_error"
    assert "cache path" in payload["error"]["message"]


def test_pdf_cli_corrupt_cached_markdown_is_typed_internal_error(tmp_path: Path) -> None:
    parsed = _cached_package(tmp_path, markdown=b"# broken \xff\xfe")
    cache = _cache_for(parsed)
    with patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache):
        result = invoke_cli(["pdf", "ATTN001"], json_output=True)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "internal_error"
    assert "unreadable" in payload["error"]["message"]
    assert "Traceback" not in result.output


def test_pdf_cli_corrupt_cached_content_list_is_typed_internal_error(tmp_path: Path) -> None:
    parsed = _cached_package(tmp_path, content_list="{not json")
    cache = _cache_for(parsed)
    with patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache):
        result = invoke_cli(["pdf", "ATTN001", "--structured"], json_output=True)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "internal_error"
    assert "unreadable" in payload["error"]["message"]


def test_pdf_cli_renderer_failure_is_typed_internal_error(tmp_path: Path) -> None:
    parsed = _cached_package(tmp_path)
    cache = _cache_for(parsed)
    with (
        patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache),
        patch("zotero_cli.commands.attachments.pdf.format_pdf_text", side_effect=RuntimeError("markup broke")),
    ):
        result = invoke_cli(["pdf", "ATTN001", "--section", "1"], json_output=True)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "internal_error"
    assert "markup broke" in payload["error"]["message"]
    combined = result.output + (result.stderr or "")
    assert "Traceback" not in combined


def test_pdf_cli_reader_failure_is_typed_runtime_error() -> None:
    with patch("zotero_cli.commands.attachments.pdf.ZoteroReader", side_effect=OSError("database unreadable")):
        result = invoke_cli(["pdf", "ATTN001"], json_output=True)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "runtime_error"
    assert "database unreadable" in payload["error"]["message"]
    assert "Traceback" not in result.output


def test_pdf_cli_missing_database_is_configuration_error() -> None:
    result = invoke_cli(
        ["pdf", "ATTN001"],
        json_output=True,
        env={"ZOT_DATA_DIR": "/tmp/definitely-not-a-zotero-data-dir"},
    )
    assert result.exit_code == 3, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "configuration_error"
    assert "database not found" in payload["error"]["message"].lower()
    assert (result.stderr or "") == ""


def test_pdf_cli_verbose_prints_traceback_for_configuration_errors() -> None:
    result = invoke_cli(
        ["pdf", "ATTN001", "--verbose"],
        json_output=True,
        env={"ZOT_DATA_DIR": "/tmp/definitely-not-a-zotero-data-dir"},
    )
    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "configuration_error"
    assert "Traceback (most recent call last)" in (result.stderr or "")


def test_pdf_cli_verbose_prints_traceback_for_unexpected_errors() -> None:
    with patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", side_effect=ValueError("boom-inside-pdf")):
        result = invoke_cli(["pdf", "ATTN001", "--verbose"], json_output=True)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "internal_error"
    assert "boom-inside-pdf" in payload["error"]["message"]
    assert "Traceback (most recent call last)" in (result.stderr or "")


def test_pdf_cli_list_attachments_unknown_item_is_not_found() -> None:
    reader = MagicMock()
    reader.get_pdf_attachments.return_value = []
    reader.get_item.return_value = None
    with patch("zotero_cli.commands.attachments.pdf.ZoteroReader", return_value=reader):
        result = invoke_cli(["pdf", "NOPDFKEY", "--list-attachments"], json_output=True)
    assert result.exit_code == 4, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "not_found"
    assert "not found" in payload["error"]["message"]
    reader.close.assert_called_once()


def test_pdf_cli_unknown_item_without_listing_is_not_found() -> None:
    reader = MagicMock()
    reader.get_pdf_attachments.return_value = []
    reader.get_item.return_value = None
    with patch("zotero_cli.commands.attachments.pdf.ZoteroReader", return_value=reader):
        result = invoke_cli(["pdf", "NOPDFKEY"], json_output=True)
    assert result.exit_code == 4, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "not_found"
    assert "not found" in payload["error"]["message"]
    reader.close.assert_called_once()


def test_pdf_cli_list_attachments_item_without_pdf_is_not_found() -> None:
    reader = MagicMock()
    reader.get_pdf_attachments.return_value = []
    reader.get_item.return_value = MagicMock()
    with patch("zotero_cli.commands.attachments.pdf.ZoteroReader", return_value=reader):
        result = invoke_cli(["pdf", "NOPDFKEY", "--list-attachments"], json_output=True)
    assert result.exit_code == 4, result.output
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "not_found"
    assert "No PDF attachment" in payload["error"]["message"]


def test_pdf_cli_passes_lookup_fingerprint_to_ensure(tmp_path: Path) -> None:
    parsed = _cached_package(tmp_path)
    cache = MagicMock()
    cache.lookup.return_value = (None, "prehashed-fingerprint")
    cache.ensure.return_value = parsed
    with (
        patch("zotero_cli.commands.attachments.pdf.MinerUParseCache", return_value=cache),
        patch("zotero_cli.commands.attachments.pdf.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
    ):
        result = invoke_cli(["pdf", "ATTN001"], json_output=True)
    assert result.exit_code == 0, result.output
    cache.ensure.assert_called_once()
    assert cache.ensure.call_args.kwargs["fingerprint"] == "prehashed-fingerprint"
    cache.lookup.assert_called_once()
