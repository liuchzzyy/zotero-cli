"""`zot` 命令行界面。

控制台脚本入口仍为 `zotero_cli.cli:main`；`main` 定义在 `root` 中，
在此与测试直接使用的内部对象一并导入。
"""

from __future__ import annotations

from zotero_cli.cli.app.flags import _hoist_global_flags as _hoist_global_flags
from zotero_cli.cli.app.group import TieredGroup as TieredGroup
from zotero_cli.cli.app.root import main as main

__all__ = ["_hoist_global_flags", "TieredGroup", "main"]
