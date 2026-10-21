"""基础 PDF 附件选择规则。"""

from __future__ import annotations

import shutil
from pathlib import Path

from zotero_cli.models import Attachment


def select_pdf_attachment(attachments: list[Attachment], attachment_key: str | None = None) -> Attachment:
    """有多个 PDF 时要求明确指定附件键，避免误用正文或补充材料。"""
    if attachment_key is not None:
        for attachment in attachments:
            if attachment.key == attachment_key:
                return attachment
        raise LookupError(f"PDF attachment '{attachment_key}' not found")
    if not attachments:
        raise LookupError("No PDF attachment found")
    if len(attachments) > 1:
        raise ValueError("Multiple PDFs; select one with --attachment KEY")
    return attachments[0]


def export_pdf(source_path: Path, destination_path: Path) -> None:
    """以排他方式复制 PDF；仅清理本次创建但未完成的目标文件。"""
    created = False
    try:
        with source_path.open("rb") as source, destination_path.open("xb") as destination:
            created = True
            shutil.copyfileobj(source, destination)
    except Exception:
        if created:
            try:
                destination_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise
