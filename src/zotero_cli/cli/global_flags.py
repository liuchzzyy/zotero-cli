"""Global flag hoisting and output-mode detection for the root group.

--json / --no-json must work wherever they appear in argv, even after the
subcommand: agents append them out of habit and Click would otherwise reject
them as unknown options. The same applies to the boolean global flags
--no-interaction and --verbose, and to the value-taking global --detail
(minimal/standard/full), which is what "--detail minimal" after a subcommand
means today.

Hoisting happens before Click parses, following the active command's option
arity so option *values* that look like flags are never mistaken for globals.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

import click

# Boolean root options that are safe to hoist from anywhere in argv.
_HOISTED_FLAGS = frozenset({"--json", "--no-json", "--no-interaction", "--verbose"})
# Value-taking root options that are safe to hoist (value follows or is attached).
_HOISTED_VALUE_FLAGS = frozenset({"--detail"})


def detect_json_output(args: Sequence[str]) -> bool:
    """Best-effort output-mode detection for errors raised before Click parsing.

    Mirrors the root callback precedence: explicit flag > ZOT_FORMAT > TTY
    auto-detect (piped stdout => JSON). Used by the top-level error boundary so
    usage errors raised during parsing still emit the right channel.
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
    """Move standalone global flags to the root, preserving Click option values.

    Follow the active command's option arity before interpreting a token as a
    global flag. In particular, a value such as --json must not disappear and
    cause the following --dry-run to become an option value. An unconsumed
    -- terminates hoisting just as it terminates option parsing.
    """

    def options_for(cmd: click.Command) -> dict[str, click.Option]:
        return {
            flag: param
            for param in cmd.params
            if isinstance(param, click.Option)
            for flag in param.opts + param.secondary_opts
        }

    flags: list[str] = []
    rest: list[str] = []
    options = options_for(command)
    values_left = 0
    tokens = iter(args)
    for tok in tokens:
        if values_left:
            rest.append(tok)
            values_left -= 1
            continue
        if tok == "--":
            rest.extend([tok, *tokens])
            break
        if tok in _HOISTED_FLAGS:
            flags.append(tok)
            continue

        name, separator, _value = tok.partition("=")
        if name in _HOISTED_VALUE_FLAGS:
            flags.append(tok)
            if not separator:
                try:
                    flags.append(next(tokens))
                except StopIteration:
                    pass
            continue

        rest.append(tok)
        option = options.get(name)
        attached_value = bool(separator)
        if option is None and tok.startswith("-") and not tok.startswith("--"):
            # Click accepts short-option clusters and attached values (-dtext).
            for index, char in enumerate(tok[1:], 2):
                option = options.get(f"-{char}")
                if option is not None and not option.is_flag and not option.count:
                    attached_value = index < len(tok)
                    break
        if option is not None and not option.is_flag and not option.count:
            values_left = max(0, option.nargs - int(attached_value))
        elif isinstance(command, click.Group) and tok in command.commands:
            command = command.commands[tok]
            options = options_for(command)
    return flags + rest
