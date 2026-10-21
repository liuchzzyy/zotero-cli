"""Single source of truth for CLI command safety tiers.

Every top-level command — and every nested subcommand that mutates durable state
(library, local config, workspace or cache) — has exactly one tier:

* `read`        — safe to run repeatedly; no durable mutation.
* `write`       — creates or mutates durable state (library, config, workspace).
* `destructive` — deletes or irreversibly rewrites durable state.

Two consumers read this module: `zot --help` (which groups commands by tier via
`zotero_cli.cli.tiered_group`) and `zot schema` (which advertises each command's
`safety_tier`). Never duplicate the mapping elsewhere.

This module intentionally imports nothing from the rest of the `cli` package so
command modules can consume it without creating an import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence

READ = "read"
WRITE = "write"
DESTRUCTIVE = "destructive"

# Top-level commands default to `read` unless listed here. Nested entries cover
# subcommands that mutate the library despite sitting under a read-tier parent.
SAFETY_TIERS: dict[tuple[str, ...], str] = {
    # read-only commands
    ("config",): READ,
    ("zotero",): READ,
    ("summarize",): READ,
    ("summarize-all",): READ,
    ("pdf",): READ,
    ("completions",): READ,
    ("workspace",): READ,
    ("schema",): READ,
    # 尚未迁移的派生命令
    ("ai_analyze",): WRITE,
    ("update-status",): DESTRUCTIVE,
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
    # nested config commands that mutate local configuration/cache state
    ("config", "init"): WRITE,
    ("config", "profile", "set"): WRITE,
    ("config", "cache", "clear"): DESTRUCTIVE,
    # nested workspace commands that mutate local workspace/index state
    ("workspace", "new"): WRITE,
    ("workspace", "add"): WRITE,
    ("workspace", "remove"): WRITE,
    ("workspace", "import"): WRITE,
    ("workspace", "index"): WRITE,
    ("workspace", "reindex"): WRITE,
    ("workspace", "embed"): WRITE,
    ("workspace", "delete"): DESTRUCTIVE,
}

# Command-name sets derived from SAFETY_TIERS for `zot --help` sectioning.
READ_COMMANDS = frozenset(path[0] for path, tier in SAFETY_TIERS.items() if len(path) == 1 and tier == READ)
WRITE_COMMANDS = frozenset(path[0] for path, tier in SAFETY_TIERS.items() if len(path) == 1 and tier == WRITE)
DESTRUCTIVE_COMMANDS = frozenset(
    path[0] for path, tier in SAFETY_TIERS.items() if len(path) == 1 and tier == DESTRUCTIVE
)


def safety_tier_for_path(path: Sequence[str]) -> str:
    """Return the safety tier for a command path (e.g. `["collection", "delete"]`).

    Nested subcommands without an explicit entry inherit their parent's tier;
    unknown top-level commands are treated as read-only, matching Click's tree
    (any command that mutates the library must be classified explicitly here).
    """
    if not path:
        return READ
    tier = SAFETY_TIERS.get(tuple(path))
    if tier is not None:
        return tier
    return SAFETY_TIERS.get((path[0],), READ)
