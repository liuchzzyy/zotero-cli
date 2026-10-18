"""The `zot` command-line interface.

The console-script entry point remains `zotero_cli.cli:main`; `main` is defined in
`root` and imported here together with the internals tests consume directly.
"""

from __future__ import annotations

from zotero_cli.cli.global_flags import _hoist_global_flags as _hoist_global_flags
from zotero_cli.cli.root import main as main
from zotero_cli.cli.tiered_group import TieredGroup as TieredGroup

__all__ = ["_hoist_global_flags", "TieredGroup", "main"]
