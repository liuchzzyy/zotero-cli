"""按对象注册 Zotero 基础与应用命令。"""

from zotero_cli.cli.commands.zotero import attachments, collections, items, notes, pdf, tags, trash

zotero_app = items.app
zotero_app.add_typer(collections.app, name="collection")

__all__ = ["items", "attachments", "collections", "notes", "pdf", "tags", "trash", "zotero_app"]
