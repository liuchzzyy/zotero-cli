"""按安全层级分组渲染命令列表的 Click Group。

zot --help 先列出读取类命令，再把写入类和破坏性命令列在明确的
"MUTATES LIBRARY" 标题下，使浏览帮助的代理程序不会把改动库的命令误认为
无害命令。层级归属来自 zotero_cli.cli.safety。

该 Group 同时负责 CLI 的顶层错误边界：未捕获异常与 Click 用法错误会被转换为
文档约定的 JSON envelope / 人类可读错误格式以及带类型的退出码，而不泄漏
回溯信息。
"""

from __future__ import annotations

import errno
import sys
from typing import Any

import click
import typer._click as typer_click
from typer.core import TyperGroup

from zotero_cli.cli.global_flags import _hoist_global_flags, detect_json_output
from zotero_cli.cli.safety import DESTRUCTIVE, READ, WRITE, safety_tier_for_path
from zotero_cli.output.exit_codes import (
    EXIT_RUNTIME,
    EXIT_VALIDATION,
    emit_error,
    emit_exception,
    emit_usage_error,
    set_verbose,
    verbose_enabled,
)


class TieredGroup(TyperGroup):
    """按安全层级分组渲染命令列表的 Click Group。"""

    def main(  # type: ignore[override]
        self,
        args: list[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        windows_expand_args: bool = True,
        **extra: Any,
    ) -> Any:
        if args is None:
            args = sys.argv[1:]
        args = _hoist_global_flags(list(args), self)
        # 每次调用都重新初始化；根回调记录解析出的 --verbose 选项，
        # ZOT_DEBUG 由 verbose_enabled() 按需读取。
        set_verbose(False)

        if not standalone_mode:
            return super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )

        json_output = detect_json_output(args)
        try:
            result = super().main(
                args=args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                windows_expand_args=windows_expand_args,
                **extra,
            )
        except SystemExit:
            # 显式退出（版本等）保留原始退出码。
            raise
        except click.exceptions.Exit as exc:
            # Typer 在 standalone_mode=False 时保留 Click 的 Exit 异常；
            # 统一转换为 SystemExit，维持现有 CLI 与测试观察到的退出码。
            raise SystemExit(exc.exit_code) from None
        except (click.ClickException, typer_click.ClickException) as exc:
            if isinstance(exc, (click.UsageError, typer_click.exceptions.UsageError)):
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
        except click.Abort:
            click.echo("Aborted!", err=True)
            raise SystemExit(EXIT_RUNTIME) from None
        except (EOFError, KeyboardInterrupt) as exc:
            click.echo(file=sys.stderr)
            click.echo("Aborted!", err=True)
            raise SystemExit(EXIT_RUNTIME) from exc
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.EPIPE:
                raise SystemExit(EXIT_RUNTIME) from None
            emit_exception(exc, output_json=json_output, verbose=verbose_enabled())
        except BaseException as exc:  # noqa: BLE001 - 错误边界正是此处的职责
            emit_exception(exc, output_json=json_output, verbose=verbose_enabled())

        if isinstance(result, int):
            raise SystemExit(result)
        return result

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        commands: list[tuple[str, click.Command]] = []
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
