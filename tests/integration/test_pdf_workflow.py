"""Integration coverage for the MinerU-only shared PDF pipeline."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.support import invoke_cli

from zotero_cli.config import PdfConfig
from zotero_cli.core.mineru import MinerUClient, MinerUParseCache, MinerUParseResult, _RemoteParse
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
    cache.get.return_value = parsed

    with (
        patch("zotero_cli.commands.pdf.MinerUParseCache", return_value=cache),
        patch("zotero_cli.commands.pdf.load_pdf_config", return_value=PdfConfig(mineru_token="token")),
    ):
        markdown_result = invoke_cli(["pdf", "ATTN001"])
        structured_result = invoke_cli(["pdf", "ATTN001", "--structured"], json_output=True)

    assert markdown_result.exit_code == 0
    assert "Cloud parsed Markdown" in markdown_result.output
    assert json.loads(structured_result.output)["data"]["content_list"][0]["text"] == "evidence"


def test_pdf_cli_missing_mineru_token_is_configuration_error() -> None:
    cache = MagicMock()
    cache.get.return_value = None
    with (
        patch("zotero_cli.commands.pdf.MinerUParseCache", return_value=cache),
        patch("zotero_cli.commands.pdf.load_pdf_config", return_value=PdfConfig()),
    ):
        result = invoke_cli(["pdf", "ATTN001"])
    assert result.exit_code == 3
    assert "MinerU API token is not configured" in result.output
    cache.ensure.assert_not_called()
