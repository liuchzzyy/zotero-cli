"""zot schema — 为每个命令输出机器可读的 schema。

schema 由 Click 命令树派生，因此不会偏离实际 CLI。
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

import click
import typer
from typer.core import TyperArgument

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
        "kind": "argument" if isinstance(param, (click.Argument, TyperArgument)) else "option",
        "type": _param_type_name(param),
        "required": bool(param.required),
    }
    if hasattr(param, "opts"):
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
    # 惰性导入：cli 入口会导入命令树，而本模块属于该命令树的一部分，
    # 模块级导入会造成循环导入。
    from zotero_cli.cli.app.safety import safety_tier_for_path

    # 根节点是 CLI 二进制名，而不是 Python 函数名（"main"）。
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
    if hasattr(cmd, "commands"):
        subs = {}
        for sub_name, sub_cmd in sorted(cmd.commands.items()):
            subs[sub_name] = _command_to_dict(sub_cmd, path + [sub_name])
        data["subcommands"] = subs
    return data


def _resolve_command(root: click.Command, path: list[str]) -> click.Command | None:
    current: click.Command = root
    for part in path:
        commands = getattr(current, "commands", None)
        if not isinstance(commands, dict):
            return None
        nxt = commands.get(part)
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
    """返回两棵 schema 树之间的结构差异。

    按点分路径跟踪新增/删除的命令，以及仍存在命令的新增/删除选项。
    类型/帮助/默认值的变更刻意不在范围内，以保持 diff 简洁有用。
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
    """在 diff 之前校验缓存 schema 树的结构。

    对任何不是 schema 树的内容抛出带有可操作提示的 ValueError，使命令能够
    输出带类型的 validation_error，而不会在合法 JSON 但结构错误（数组、null、
    标量叶子节点）时崩溃。
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
    """读取此前输出的 schema 文件。既接受完整 envelope，也接受裸 data 树。"""
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
    typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    raise SystemExit(exit_code)


def schema_cmd(
    ctx: typer.Context,
    command_path: list[str] = typer.Argument(default=None, help="命令路径；可用点号分隔。"),
    diff_path: str | None = typer.Option(None, "--diff", help="与缓存的 schema 做结构 diff。"),
) -> None:
    """输出整个 CLI 或单个命令的机器可读 schema。

    \b
    示例：
      zot schema                       # 完整命令树
      zot schema search                # 单个命令的 schema
      zot schema collection add        # 嵌套子命令
      zot schema --diff prev.json      # 与缓存 schema 的结构 diff
    """
    command_path = tuple(command_path or ())
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
    typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
