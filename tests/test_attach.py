"""Tests for file attachment upload."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from zotero_cli_agent.core.writer import ZoteroWriteError, ZoteroWriter


class TestAttachWriter:
    @patch("zotero_cli_agent.core.writer.zotero.Zotero")
    def test_upload_attachment_success(self, mock_zotero_cls, tmp_path):
        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        mock_zot.attachment_simple.return_value = {
            "success": [{"key": "ATT001", "filename": "test.pdf"}],
            "failure": [],
            "unchanged": [],
        }
        writer = ZoteroWriter(library_id="123", api_key="abc")
        key = writer.upload_attachment("PARENT1", pdf)
        assert key == "ATT001"
        mock_zot.attachment_simple.assert_called_once()

    @patch("zotero_cli_agent.core.writer.zotero.Zotero")
    def test_upload_attachment_unchanged(self, mock_zotero_cls, tmp_path):
        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        mock_zot.attachment_simple.return_value = {
            "success": [],
            "failure": [],
            "unchanged": [{"key": "ATT001"}],
        }
        writer = ZoteroWriter(library_id="123", api_key="abc")
        key = writer.upload_attachment("PARENT1", pdf)
        assert key == "ATT001"

    @patch("zotero_cli_agent.core.writer.zotero.Zotero")
    def test_upload_attachment_failure(self, mock_zotero_cls, tmp_path):
        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        mock_zot.attachment_simple.return_value = {
            "success": [],
            "failure": [{"key": "", "message": "Upload failed"}],
            "unchanged": [],
        }
        writer = ZoteroWriter(library_id="123", api_key="abc")
        with pytest.raises(ZoteroWriteError, match="Upload failed"):
            writer.upload_attachment("PARENT1", pdf)

    def test_upload_attachment_file_not_found(self):
        with patch("zotero_cli_agent.core.writer.zotero.Zotero"):
            writer = ZoteroWriter(library_id="123", api_key="abc")
            with pytest.raises(ZoteroWriteError, match="not found"):
                writer.upload_attachment("PARENT1", Path("/nonexistent/file.pdf"))

    @patch("zotero_cli_agent.core.writer.zotero.Zotero")
    def test_upload_attachment_empty_response(self, mock_zotero_cls, tmp_path):
        mock_zot = MagicMock()
        mock_zotero_cls.return_value = mock_zot
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4 test")
        mock_zot.attachment_simple.return_value = {
            "success": [],
            "failure": [],
            "unchanged": [],
        }
        writer = ZoteroWriter(library_id="123", api_key="abc")
        with pytest.raises(ZoteroWriteError, match="Unexpected"):
            writer.upload_attachment("PARENT1", pdf)
