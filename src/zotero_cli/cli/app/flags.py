"""全局选项上提与根命令组的输出模式检测。

--json / --no-json 必须能在 argv 中的任意位置生效，即使位于子命令之后：
代理程序习惯性地把它们追加到末尾，否则 Click 会将其视为未知选项而拒绝。
布尔全局选项 --no-interaction 和 --verbose，以及取值型全局选项 --detail
（minimal/standard/full）同理，后者正是当前在子命令之后写 "--detail minimal"
所表达的含义。

上提发生在 Click 解析之前，并遵循当前命令的选项参数个数，因此看起来像选项
的选项*值*不会被误认作全局选项。像 "--detail" 这种缺少有效值的裸取值选项会
被单独上提，留给 Click 报告缺少参数错误，而不会吞掉后续的选项或子命令词元。
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

import click

# 可从 argv 任意位置安全上提的布尔根选项。
_HOISTED_FLAGS = frozenset({"--json", "--no-json", "--no-interaction", "--verbose"})
# 可安全上提的取值型根选项（值紧随其后或以等号连接）。
_HOISTED_VALUE_FLAGS = frozenset({"--detail"})
# --detail 允许的取值，用于避免吞掉其他选项。
_DETAIL_CHOICES = frozenset({"minimal", "standard", "full"})


def detect_json_output(args: Sequence[str]) -> bool:
    """在 Click 解析前尽力检测输出模式，供其之前抛出的错误使用。

    与根回调的优先级保持一致：显式选项 > ZOT_FORMAT > TTY 自动检测
    （stdout 被管道重定向时视为 JSON）。顶层错误边界借此让解析阶段产生的
    用法错误仍输出到正确的通道。
    """
    if "--json" in args:
        return True
    if "--no-json" in args:
        return False
    env_fmt = os.environ.get("ZOT_FORMAT", "").lower()
    if env_fmt == "json":
        return True
    if env_fmt in ("table", "text"):
        return False
    return not sys.stdout.isatty()


def _hoist_global_flags(args: list[str], command: click.Command) -> list[str]:
    """将独立的全局选项移到根位置，同时保留 Click 选项的取值。

    在把词元解释为全局选项之前，先遵循当前命令的选项参数个数。特别是，
    --json 这样的取值不能被丢弃，否则后续的 --dry-run 会被当作它的选项值。
    未被消耗的 -- 会终止上提，正如它终止选项解析一样。
    """

    def options_for(cmd: click.Command) -> dict[str, click.Option]:
        return {
            flag: param
            for param in cmd.params
            if hasattr(param, "opts") and hasattr(param, "secondary_opts")
            for flag in param.opts + param.secondary_opts
        }

    flags: list[str] = []
    rest: list[str] = []
    options = options_for(command)
    values_left = 0
    index = 0
    while index < len(args):
        tok = args[index]
        if values_left:
            rest.append(tok)
            values_left -= 1
            index += 1
            continue
        if tok == "--":
            rest.extend(args[index:])
            break
        if tok in _HOISTED_FLAGS:
            flags.append(tok)
            index += 1
            continue

        name, separator, _value = tok.partition("=")
        if name in _HOISTED_VALUE_FLAGS:
            if separator:
                flags.append(tok)
                index += 1
                continue
            following = args[index + 1] if index + 1 < len(args) else None
            is_flag_like = following is None or following.startswith("-")
            commands = getattr(command, "commands", {})
            is_subcommand = following in commands
            if following is not None and not is_flag_like and not is_subcommand:
                flags.extend([tok, following])
                index += 2
                continue
            # 缺少应有的取值：以显式空值上提该选项，让 Click 针对该选项本身
            # 报告校验错误，而不是吞掉下一个选项或子命令词元。
            flags.append(f"{tok}=")
            index += 1
            continue

        rest.append(tok)
        option = options.get(name)
        attached_value = bool(separator)
        if option is None and tok.startswith("-") and not tok.startswith("--"):
            # Click 接受短选项组合与紧贴取值（-dtext）。
            for char_index, char in enumerate(tok[1:], 2):
                option = options.get(f"-{char}")
                if option is not None and not option.is_flag and not option.count:
                    attached_value = char_index < len(tok)
                    break
        if option is not None and not option.is_flag and not option.count:
            values_left = max(0, option.nargs - int(attached_value))
        elif tok in getattr(command, "commands", {}):
            command = command.commands[tok]
            options = options_for(command)
        index += 1
    return flags + rest
