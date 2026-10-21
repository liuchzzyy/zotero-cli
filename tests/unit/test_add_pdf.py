"""`add_item_from_pdf` 领域编排测试：正常流程、部分失败与错误分类。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.pdf import AddFromPdfError, add_item_from_pdf


@pytest.fixture
def dummy_pdf(tmp_path: Path) -> Path:
    pdf = tmp_path / "article.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy content")
    return pdf


def test_add_item_from_pdf_success(dummy_pdf: Path) -> None:
    writer = MagicMock()
    writer.add_item.return_value = "NEW001"
    writer.upload_attachment.return_value = "ATT001"

    result = add_item_from_pdf(
        dummy_pdf,
        library_id="123",
        api_key="fake-key",
        doi_override="10.1234/test",
        resolve=False,
        writer_factory=lambda **_: writer,
    )

    assert result.key == "NEW001"
    assert result.doi == "10.1234/test"
    assert result.attachment_key == "ATT001"
    assert result.attachment_error is None
    payload = result.data_payload()
    assert payload["key"] == "NEW001"
    assert payload["attachment_key"] == "ATT001"
    assert payload["sync_required"] is True


def test_add_item_from_pdf_attachment_failure_returns_partial_state(dummy_pdf: Path) -> None:
    writer = MagicMock()
    writer.add_item.return_value = "NEW001"
    writer.upload_attachment.side_effect = ZoteroWriteError("attachment failed", code="network_error")

    result = add_item_from_pdf(
        dummy_pdf,
        library_id="123",
        api_key="fake-key",
        doi_override="10.1234/test",
        resolve=False,
        writer_factory=lambda **_: writer,
    )

    assert result.key == "NEW001"
    assert result.attachment_key is None
    assert result.attachment_error is not None
    assert result.attachment_error.code == "network_error"


def test_add_item_from_pdf_write_error_raises_classified_error(dummy_pdf: Path) -> None:
    writer = MagicMock()
    writer.add_item.side_effect = ZoteroWriteError("creation rejected", code="validation_error")

    with pytest.raises(AddFromPdfError) as exc_info:
        add_item_from_pdf(
            dummy_pdf,
            library_id="123",
            api_key="fake-key",
            doi_override="10.1234/test",
            resolve=False,
            writer_factory=lambda **_: writer,
        )

    assert exc_info.value.code == "validation_error"


def test_add_item_from_pdf_replays_cached_envelope(dummy_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cached = {"ok": True, "data": {"key": "CACHED01"}, "meta": {}}
    from zotero_cli.zotero.access import idempotency

    monkeypatch.setattr(idempotency, "begin_mutation", Mock(return_value=cached))
    writer_factory = MagicMock(side_effect=AssertionError("命中缓存时不应构造写入器"))

    result = add_item_from_pdf(
        dummy_pdf,
        library_id="123",
        api_key="fake-key",
        doi_override="10.1234/test",
        resolve=False,
        idempotency_key="idemp-key-1",
        writer_factory=writer_factory,
    )

    assert result.cached_envelope == cached
    writer_factory.assert_not_called()
