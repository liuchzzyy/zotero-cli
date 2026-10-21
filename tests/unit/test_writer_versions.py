"""版本护栏阻止过期的派生元数据覆盖当前编辑。"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from zotero_cli.zotero.access.errors import ZoteroWriteError
from zotero_cli.zotero.access.writer import ZoteroWriter


def test_expected_version_rejects_a_newer_remote_item() -> None:
    writer = object.__new__(ZoteroWriter)
    writer._zot = Mock()
    writer._zot.item.return_value = {"key": "ITEM1", "version": 18, "data": {"extra": "user edit"}}

    with pytest.raises(ZoteroWriteError) as error:
        writer.update_item("ITEM1", {"extra": "stale computed text"}, expected_version=17)

    assert error.value.code == "conflict"
    writer._zot.update_item.assert_not_called()


def test_expected_version_submits_the_same_version_it_checked() -> None:
    writer = object.__new__(ZoteroWriter)
    writer._zot = Mock()
    item = {"key": "ITEM1", "version": 17, "data": {"extra": "funder: keep"}}
    writer._zot.item.return_value = item

    writer.update_item("ITEM1", {"extra": "funder: keep\ntranslated-title: 标题"}, expected_version=17)

    writer._zot.update_item.assert_called_once_with(item)
    assert item["version"] == 17
