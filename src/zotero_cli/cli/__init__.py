"""`zot` 命令行界面。

控制台脚本入口仍为 `zotero_cli.cli:main`；`main` 定义在 `root` 中，
在此与测试直接使用的内部对象一并导入。
"""

from __future__ import annotations

from zotero_cli.cli.global_flags import _hoist_global_flags as _hoist_global_flags
from zotero_cli.cli.root import main as main
from zotero_cli.cli.tiered_group import TieredGroup as TieredGroup

__all__ = ["_hoist_global_flags", "TieredGroup", "main"]
