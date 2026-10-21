"""按安全层级分组渲染命令列表，并承载 CLI 顶层错误边界。

`zot --help` 先列出读取类命令，再把写入类和破坏性命令列在明确的
"MUTATES LIBRARY" 标题下，使浏览帮助的代理程序不会把改动库的命令误认为
无害命令。层级归属来自 `zotero_cli.cli.app.safety`。

错误边界通过包装 Typer 生成的根命令实现：未捕获异常与用法错误会被转换为
文档约定的 JSON envelope / 人类可读错误格式以及带类型的退出码，而不泄漏回溯。
"""

from __future__ import annotations

import errno
import sys
from collections.abc import Sequence
from typing import Any

import typer
import typer._click as typer_click
from typer.core import TyperGroup

from zotero_cli.cli.app.flags import _hoist_global_flags, detect_json_output
from zotero_cli.cli.app.safety import DESTRUCTIVE, READ, WRITE, safety_tier_for_path
from zotero_cli.output.exit_codes import (
    EXIT_RUNTIME,
    EXIT_VALIDATION,
    emit_error,
    emit_exception,
    emit_usage_error,
    set_verbose,
    verbose_enabled,
)


def install_error_boundary(command: typer_click.Command) -> typer_click.Command:
    """给 Typer 生成的根命令安装统一错误边界。

    只包装最终根命令，子组保持纯 Typer 行为。
    """
    original_main = command.main

    def guarded_main(args: Sequence[str] | None = None, *main_args: Any, **main_kwargs: Any) -> Any:
        argv = list(sys.argv[1:] if args is None else args)
        hoisted = _hoist_global_flags(argv, command)
        set_verbose(False)
        json_output = detect_json_output(hoisted)
        standalone = main_kwargs.get("standalone_mode", True)
        main_kwargs["standalone_mode"] = False
        try:
            result = original_main(hoisted, *main_args, **main_kwargs)
        except SystemExit:
            # 显式退出（帮助、版本、schema 错误）保留原始退出码。
            raise
        except typer_click.ClickException as exc:
            if isinstance(exc, typer_click.exceptions.UsageError):
                if json_output:
                    emit_usage_error(exc, output_json=True)
                exc.show()
                raise SystemExit(EXIT_VALIDATION) from None
            if json_output:
                emit_error(
                    "runtime_error",
                    exc.format_message(),
                    output_json=True,
                    hint="Run 'zot --help' for usage",
                    context="cli",
                )
            exc.show()
            raise SystemExit(exc.exit_code) from None
        except typer.Abort:
            typer.echo("Aborted!", err=True)
            raise SystemExit(EXIT_RUNTIME) from None
        except (EOFError, KeyboardInterrupt) as exc:
            typer.echo(file=sys.stderr)
            typer.echo("Aborted!", err=True)
            raise SystemExit(EXIT_RUNTIME) from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.EPIPE:
                raise SystemExit(EXIT_RUNTIME) from None
            emit_exception(exc, output_json=json_output, verbose=verbose_enabled())
        except BaseException as exc:  # noqa: BLE001 - 错误边界正是此处的职责
            emit_exception(exc, output_json=json_output, verbose=verbose_enabled())

        if isinstance(result, int):
            raise SystemExit(result)
        if standalone:
            return None
        return result

    command.main = guarded_main  # ty: ignore[invalid-assignment]
    return command


TIER_PANELS: dict[str, str] = {
    READ: "Read commands",
    WRITE: "Write commands (MUTATES LIBRARY)",
    DESTRUCTIVE: "Destructive commands (MUTATES LIBRARY)",
}


class TieredGroup(TyperGroup):
    """按安全层级分组渲染命令列表的 Typer 命令组。"""

    def get_command(self, ctx: typer_click.Context, cmd_name: str) -> typer_click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            path: list[str] = []
            ancestor = ctx
            while ancestor.parent is not None:
                path.insert(0, ancestor.info_name or "")
                ancestor = ancestor.parent
            tier = safety_tier_for_path([*path, cmd_name])
            setattr(cmd, "rich_help_panel", TIER_PANELS.get(tier, "Read commands"))
        return cmd

    def format_commands(self, ctx: typer_click.Context, formatter: typer_click.HelpFormatter) -> None:
        commands: list[tuple[str, typer_click.Command]] = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is None or getattr(cmd, "hidden", False):
                continue
            commands.append((name, cmd))

        path: list[str] = []
        ancestor = ctx
        while ancestor.parent is not None:
            path.insert(0, ancestor.info_name or "")
            ancestor = ancestor.parent

        def rows_for(tier: str) -> list[tuple[str, str]]:
            return [
                (name, cmd.get_short_help_str(limit=70))
                for name, cmd in commands
                if safety_tier_for_path([*path, name]) == tier
            ]

        sections = [
            ("Read commands", rows_for(READ)),
            ("Write commands (MUTATES LIBRARY)", rows_for(WRITE)),
            ("Destructive commands (MUTATES LIBRARY)", rows_for(DESTRUCTIVE)),
        ]

        for title, rows in sections:
            if not rows:
                continue
            with formatter.section(title):
                formatter.write_dl(rows)


__all__ = ["TieredGroup", "install_error_boundary"]
