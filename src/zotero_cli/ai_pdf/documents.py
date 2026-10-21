"""Zotero 条目 PDF 附件的查找与选择服务。"""

from __future__ import annotations

from zotero_cli.ai_pdf.errors import PdfDocumentError
from zotero_cli.ai_pdf.models import ParsedPdfDocument, PdfDocument, ProgressCallback
from zotero_cli.ai_pdf.parsing import PdfParsingService
from zotero_cli.zotero.access.reader import ZoteroReader
from zotero_cli.zotero.pdf import select_pdf_attachment


class ZoteroPdfDocuments:
    """把 Zotero 查询与附件选择封装为应用服务。"""

    def __init__(self, reader: ZoteroReader, parsing: PdfParsingService | None = None) -> None:
        self._reader = reader
        self._parsing = parsing or PdfParsingService()

    def select(self, item_key: str, attachment_key: str | None = None) -> PdfDocument:
        """选择唯一或显式指定的 PDF 附件。"""
        attachments = self._reader.get_pdf_attachments(item_key)
        if not attachments:
            if self._reader.get_item(item_key) is None:
                raise PdfDocumentError(f"Item '{item_key}' not found", kind="item")
            raise PdfDocumentError(f"No PDF attachment found for '{item_key}'", kind="pdf")
        try:
            attachment = select_pdf_attachment(attachments, attachment_key)
        except (LookupError, ValueError) as error:
            kind = "attachment" if isinstance(error, LookupError) else "selection"
            raise PdfDocumentError(str(error), kind=kind) from error
        if not attachment.path or not attachment.path.exists():
            raise PdfDocumentError(f"PDF file not found at {attachment.path or attachment.filename}", kind="file")
        return PdfDocument(item_key=item_key, attachment=attachment, pdf_path=attachment.path)

    def parse(
        self,
        item_key: str,
        attachment_key: str | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> ParsedPdfDocument:
        """选择附件并返回其缓存优先的解析结果。"""
        document = self.select(item_key, attachment_key)
        return ParsedPdfDocument(document=document, parsed=self._parsing.parse(document.pdf_path, progress_callback))
