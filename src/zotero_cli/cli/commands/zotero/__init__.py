"""`zot zotero` 的基础命令。"""

from zotero_cli.cli.commands.zotero import (
    add,
    attachments,
    cite,
    collection_reorganize,
    delete,
    duplicates,
    export,
    notes,
    open_item,
    pdf,
    relate,
    tag,
    trash,
    update,
)
from zotero_cli.cli.commands.zotero.collection import app as collection_app
from zotero_cli.cli.commands.zotero.reading import app as zotero_app

zotero_app.add_typer(collection_app, name="collection")

__all__ = [
    "add",
    "attachments",
    "cite",
    "collection_reorganize",
    "delete",
    "duplicates",
    "export",
    "notes",
    "open_item",
    "pdf",
    "relate",
    "tag",
    "trash",
    "update",
]
