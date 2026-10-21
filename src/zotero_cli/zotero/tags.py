"""批量更新条目标签的领域操作。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.writer import ZoteroWriter

ReportTagUpdate = Callable[[dict[str, Any], bool], None]


@dataclass(frozen=True)
class TagUpdateResult:
    """批量标签更新的成功与失败记录。"""

    succeeded: list[dict[str, Any]]
    failed: list[dict[str, Any]]


def update_tags(
    writer: ZoteroWriter,
    keys: list[str],
    *,
    add_tag: str | None = None,
    remove_tag: str | None = None,
    report: ReportTagUpdate | None = None,
) -> TagUpdateResult:
    """按输入顺序更新标签，并保留每次成功写入的结果。"""
    succeeded: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for key in keys:
        try:
            if add_tag:
                writer.add_tags(key, [add_tag])
                row = {"key": key, "tags_added": [add_tag]}
                succeeded.append(row)
                if report:
                    report(row, False)
            if remove_tag:
                writer.remove_tags(key, [remove_tag])
                row = {"key": key, "tags_removed": [remove_tag]}
                succeeded.append(row)
                if report:
                    report(row, False)
        except ZoteroWriteError as error:
            row = {
                "key": key,
                "error": {
                    "code": error.code,
                    "message": str(error),
                    "retryable": error.retryable,
                    "committed": error.committed,
                },
            }
            failed.append(row)
            if report:
                report(row, True)
    return TagUpdateResult(succeeded=succeeded, failed=failed)
