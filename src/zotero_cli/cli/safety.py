"""CLI 命令安全层级的唯一权威定义。

每个顶层命令——以及每个会改动持久状态（库、本地配置、workspace 或缓存）的
嵌套子命令——都恰好属于一个层级：

* `read`        — 可重复执行；不改动持久状态。
* `write`       — 创建或改动持久状态（库、配置、workspace）。
* `destructive` — 删除或不可逆地重写持久状态。

有两个使用方读取本模块：`zot --help`（通过 `zotero_cli.cli.tiered_group`
按层级对命令分组）和 `zot schema`（对外声明每个命令的 `safety_tier`）。
不要在别处重复维护该映射。

本模块刻意不导入 `cli` 包的其他部分，以便各命令模块使用它而不会产生循环导入。
"""

from __future__ import annotations

from collections.abc import Sequence

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

# 顶层命令默认属于 `read`，除非在此列出。嵌套条目覆盖那些虽位于 read 层级
# 父命令之下、却会改动库的子命令。
SAFETY_TIERS: dict[tuple[str, ...], str] = {
    # 只读命令
    ("config",): READ,
    ("zotero",): READ,
    ("metadata",): READ,
    ("ai-pdf",): READ,
    ("ai-note",): WRITE,
    ("completions",): READ,
    ("workspace",): READ,
    ("schema",): READ,
    ("metadata", "update-status"): WRITE,
    # Zotero 基础库写入及破坏性命令
    ("zotero", "add"): WRITE,
    ("zotero", "attach"): WRITE,
    ("zotero", "pdf", "export"): WRITE,
    ("zotero", "pdf", "upload"): WRITE,
    ("zotero", "note"): WRITE,
    ("zotero", "tag"): WRITE,
    ("zotero", "update"): WRITE,
    ("zotero", "delete"): DESTRUCTIVE,
    ("zotero", "collection", "create"): WRITE,
    ("zotero", "collection", "move"): WRITE,
    ("zotero", "collection", "rename"): WRITE,
    ("zotero", "collection", "reorganize"): WRITE,
    ("zotero", "collection", "delete"): DESTRUCTIVE,
    ("zotero", "trash", "restore"): WRITE,
    # 会改动本地配置/缓存状态的嵌套 config 命令
    ("config", "init"): WRITE,
    ("config", "profile", "set"): WRITE,
    ("config", "cache", "clear"): DESTRUCTIVE,
    # 会改动本地 workspace/索引状态的嵌套 workspace 命令
    ("workspace", "new"): WRITE,
    ("workspace", "add"): WRITE,
    ("workspace", "remove"): WRITE,
    ("workspace", "import"): WRITE,
    ("workspace", "index"): WRITE,
    ("workspace", "reindex"): WRITE,
    ("workspace", "embed"): WRITE,
    ("workspace", "delete"): DESTRUCTIVE,
}

# 由 SAFETY_TIERS 派生的命令名集合，供 `zot --help` 分节使用。
READ_COMMANDS = frozenset(path[0] for path, tier in SAFETY_TIERS.items() if len(path) == 1 and tier == READ)
WRITE_COMMANDS = frozenset(path[0] for path, tier in SAFETY_TIERS.items() if len(path) == 1 and tier == WRITE)
DESTRUCTIVE_COMMANDS = frozenset(
    path[0] for path, tier in SAFETY_TIERS.items() if len(path) == 1 and tier == DESTRUCTIVE
)


def safety_tier_for_path(path: Sequence[str]) -> str:
    """返回命令路径的安全层级（例如 `["collection", "delete"]`）。

    未显式列出的嵌套子命令继承其父命令的层级；未知的顶层命令视为只读，
    与 Click 的命令树一致（任何会改动库的命令都必须在此显式分类）。
    """
    if not path:
        return READ
    tier = SAFETY_TIERS.get(tuple(path))
    if tier is not None:
        return tier
    return SAFETY_TIERS.get((path[0],), READ)
