"""zot schema — emit machine-readable schema for every command.

Derived from the Click command tree so the schema cannot drift from the actual CLI.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

import click

from zotero_cli import __version__
from zotero_cli.output.envelope import SCHEMA_VERSION, envelope_error, envelope_ok


def _param_type_name(param: click.Parameter) -> str:
    t = param.type
    name = getattr(t, "name", None) or type(t).__name__.lower()
    if isinstance(t, click.Choice):
        return "choice"
    if isinstance(t, click.Path):
        return "path"
    if isinstance(t, click.IntRange):
        return "integer"
    if name in ("integer", "int"):
        return "integer"
    if name in ("boolean", "bool"):
        return "boolean"
    if name == "float":
        return "number"
    return "string"


def _param_to_dict(param: click.Parameter) -> dict:
    d: dict[str, Any] = {
        "name": param.name,
        "kind": "argument" if isinstance(param, click.Argument) else "option",
        "type": _param_type_name(param),
        "required": bool(param.required),
    }
    if isinstance(param, click.Option):
        d["flags"] = list(param.opts) + list(param.secondary_opts)
        d["is_flag"] = bool(getattr(param, "is_flag", False))
        if param.help:
            d["help"] = param.help
    default = param.default
    if callable(default):
        try:
            default = default()
        except Exception:
            default = None
    if default is not None and default is not False and type(default).__name__ != "Sentinel":
        try:
            json.dumps(default)
            d["default"] = default
        except TypeError:
            d["default"] = str(default)
    if isinstance(param.type, click.Choice):
        d["choices"] = list(param.type.choices)
    if param.nargs and param.nargs != 1:
        d["nargs"] = param.nargs
    return d


def _command_to_dict(cmd: click.Command, path: list[str]) -> dict:
    # Imported lazily: the cli entry point imports the command tree, and this
    # module is part of that tree, so a module-level import would cycle.
    from zotero_cli.cli.safety import safety_tier_for_path

    # The root node is the CLI binary, not the Python function name ("main").
    name = " ".join(path) if path else "zot"
    data: dict[str, Any] = {
        "name": name,
        "help": (cmd.help or "").strip().splitlines()[0] if cmd.help else "",
        "safety_tier": safety_tier_for_path(path),
        "since": "0.3.0",
        "deprecated": False,
    }
    params = [p for p in cmd.params if not (isinstance(p, click.Option) and p.name == "help")]
    data["params"] = [_param_to_dict(p) for p in params]
    if isinstance(cmd, click.Group):
        subs = {}
        for sub_name, sub_cmd in sorted(cmd.commands.items()):
            subs[sub_name] = _command_to_dict(sub_cmd, path + [sub_name])
        data["subcommands"] = subs
    return data


def _resolve_command(root: click.Command, path: list[str]) -> click.Command | None:
    current: click.Command = root
    for part in path:
        if not isinstance(current, click.Group):
            return None
        nxt = current.commands.get(part)
        if nxt is None:
            return None
        current = nxt
    return current


def _flatten_commands(node: dict, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], dict]:
    out: dict[tuple[str, ...], dict] = {path: node}
    for sub_name, sub_node in node.get("subcommands", {}).items():
        out.update(_flatten_commands(sub_node, path + (sub_name,)))
    return out


def _option_flags(node: dict) -> set[str]:
    flags: set[str] = set()
    for p in node.get("params", []):
        if p.get("kind") == "option":
            flags.update(p.get("flags", []))
    return flags


def compute_schema_diff(before: dict, after: dict) -> dict[str, Any]:
    """Return a structural diff between two schema trees.

    Tracks added/removed commands (by dotted path) and added/removed option
    flags per surviving command. Type/help/default changes are intentionally
    out of scope to keep the diff useful and short.
    """
    before_cmds = _flatten_commands(before)
    after_cmds = _flatten_commands(after)
    before_paths = set(before_cmds)
    after_paths = set(after_cmds)

    def label(p: tuple[str, ...]) -> str:
        return ".".join(p) or "(root)"

    commands_added = sorted(label(p) for p in after_paths - before_paths)
    commands_removed = sorted(label(p) for p in before_paths - after_paths)

    commands_changed: dict[str, dict[str, list[str]]] = {}
    for path in sorted(before_paths & after_paths):
        added = sorted(_option_flags(after_cmds[path]) - _option_flags(before_cmds[path]))
        removed = sorted(_option_flags(before_cmds[path]) - _option_flags(after_cmds[path]))
        if added or removed:
            entry: dict[str, list[str]] = {}
            if added:
                entry["params_added"] = added
            if removed:
                entry["params_removed"] = removed
            commands_changed[label(path)] = entry

    return {
        "commands_added": commands_added,
        "commands_removed": commands_removed,
        "commands_changed": commands_changed,
    }


def _validate_cached_tree(tree: object, path: str = "(root)") -> None:
    """Validate the shape of a cached schema tree before diffing.

    Raises ValueError with a human-actionable message for anything that is not
    a schema tree, so the command can emit a typed validation_error instead of
    crashing on malformed-but-valid JSON (arrays, null, scalar leaf nodes).
    """
    if not isinstance(tree, dict):
        raise ValueError(f"{path} must be a JSON object, got {type(tree).__name__}")
    subcommands = tree.get("subcommands")
    if subcommands is not None:
        if not isinstance(subcommands, dict):
            raise ValueError(f"{path}.subcommands must be a JSON object")
        for name, child in subcommands.items():
            if not isinstance(child, dict):
                raise ValueError(f"{path}.subcommands[{name!r}] must be a JSON object")
            _validate_cached_tree(child, f"{path}.subcommands[{name!r}]")
    params = tree.get("params")
    if params is not None:
        if not isinstance(params, list):
            raise ValueError(f"{path}.params must be a JSON array")
        for index, param in enumerate(params):
            if not isinstance(param, dict):
                raise ValueError(f"{path}.params[{index}] must be a JSON object")


def _load_cached_schema(path: str) -> tuple[dict, dict[str, str]]:
    """Read a previously-emitted schema file. Accepts a full envelope or a bare data tree."""
    with open(path, encoding="utf-8") as f:
        loaded = json.load(f)
    if not isinstance(loaded, dict):
        raise ValueError(f"cached schema must be a JSON object, got {type(loaded).__name__}")
    if "data" in loaded:
        data = loaded["data"]
        if not isinstance(data, dict):
            raise ValueError("cached schema envelope 'data' must be a JSON object")
        meta = loaded.get("meta") or {}
        if not isinstance(meta, dict):
            raise ValueError("cached schema envelope 'meta' must be a JSON object")
        version_info = {
            "schema_version": meta.get("schema_version", "unknown"),
            "cli_version": meta.get("cli_version", "unknown"),
        }
        _validate_cached_tree(data)
        return data, version_info
    _validate_cached_tree(loaded)
    return loaded, {"schema_version": "unknown", "cli_version": "unknown"}


def _emit_schema_error(code: str, message: str, *, hint: str, exit_code: int) -> NoReturn:
    env = envelope_error(code=code, message=message, retryable=False, hint=hint)
    click.echo(json.dumps(env, indent=2, ensure_ascii=False))
    raise SystemExit(exit_code)


@click.command("schema")
@click.argument("command_path", nargs=-1)
@click.option(
    "--diff",
    "diff_path",
    type=click.Path(dir_okay=False, path_type=str),
    default=None,
    help="Path to a previously-emitted schema. Output a structural diff against the current schema.",
)
@click.pass_context
def schema_cmd(ctx: click.Context, command_path: tuple[str, ...], diff_path: str | None) -> None:
    """Emit machine-readable schema for the CLI or one command.

    \b
    Examples:
      zot schema                       # full tree
      zot schema search                # schema for one command
      zot schema collection add        # nested subcommand
      zot schema --diff prev.json      # structural diff vs cached schema
    """
    root = ctx.find_root().command
    if command_path and diff_path is not None:
        _emit_schema_error(
            "validation_error",
            "COMMAND_PATH cannot be combined with --diff",
            hint="Run 'zot schema --diff prev.json' to diff full schema trees, or 'zot schema <command>' without --diff",
            exit_code=3,
        )

    if command_path:
        joined = " ".join(command_path)
        normalized_path = joined.replace(".", " ").split()
        if not normalized_path:
            _emit_schema_error(
                "validation_error",
                "Invalid command path: provide a command name or dot-separated path",
                hint="Run 'zot schema' for the full tree or 'zot schema <command>' for one command",
                exit_code=3,
            )
        target = _resolve_command(root, normalized_path)
        if target is None:
            _emit_schema_error(
                "not_found",
                f"Command '{joined}' not found",
                hint="Run 'zot schema' to list all commands",
                exit_code=4,
            )
        data = _command_to_dict(target, normalized_path)
    else:
        data = _command_to_dict(root, [])

    if diff_path is not None:
        try:
            before, before_versions = _load_cached_schema(diff_path)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            _emit_schema_error(
                "validation_error",
                f"Could not read cached schema at {diff_path!r}: {e}",
                hint="Provide a JSON file produced by 'zot schema'",
                exit_code=3,
            )
        data = {
            "from": before_versions,
            "to": {"schema_version": SCHEMA_VERSION, "cli_version": __version__},
            **compute_schema_diff(before, data),
        }

    env = envelope_ok(
        data,
        meta={
            "schema_version": SCHEMA_VERSION,
            "cli_version": __version__,
        },
    )
    click.echo(json.dumps(env, indent=2, ensure_ascii=False))
