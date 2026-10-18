"""Stable CLI exit codes and typed error emission for orchestrators and agents.

Exit code contract (documented in AGENTS.md / the bundled skill):
0 = ok, 1 = runtime/internal error, 2 = auth, 3 = validation/configuration/params,
4 = not found, 5 = network, 6 = conflict.
"""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import sys
import traceback
from dataclasses import dataclass
from typing import NoReturn

import click

from zotero_cli.output.envelope import envelope_error

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_AUTH = 2
EXIT_VALIDATION = 3
EXIT_NOT_FOUND = 4
EXIT_NETWORK = 5
EXIT_CONFLICT = 6

CODE_TO_EXIT = {
    # authentication / authorization
    "auth_missing": EXIT_AUTH,
    "auth_invalid": EXIT_AUTH,
    "auth_expired": EXIT_AUTH,
    # validation, configuration, confirmation, usage
    "validation_error": EXIT_VALIDATION,
    "configuration_error": EXIT_VALIDATION,
    "confirmation_required": EXIT_VALIDATION,
    # lookups
    "not_found": EXIT_NOT_FOUND,
    # network / remote provider
    "network_error": EXIT_NETWORK,
    "rate_limited": EXIT_NETWORK,
    # remote library conflicts
    "conflict": EXIT_CONFLICT,
    # runtime / internal failures
    "runtime_error": EXIT_RUNTIME,
    "internal_error": EXIT_RUNTIME,
    "api_error": EXIT_RUNTIME,
}


_verbose_mode: contextvars.ContextVar[bool] = contextvars.ContextVar("_verbose_mode", default=False)
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def set_verbose(enabled: bool) -> None:
    """Record the parsed --verbose flag for the current invocation/context."""
    _verbose_mode.set(enabled)


def verbose_enabled() -> bool:
    """Return True when tracebacks were requested.

    --verbose is recorded from the parsed root flags (never from raw argv), and
    the ZOT_DEBUG environment variable is its scripting equivalent.
    """
    if _verbose_mode.get():
        return True
    return os.environ.get("ZOT_DEBUG", "").strip().lower() in _TRUTHY_ENV_VALUES


def exit_code_for(error_code: str) -> int:
    return CODE_TO_EXIT.get(error_code, EXIT_RUNTIME)


def report_error(
    code: str,
    message: str,
    *,
    output_json: bool,
    retryable: bool = False,
    hint: str = "",
    context: str = "",
    committed: bool = False,
) -> None:
    """Emit an error without exiting. JSON -> stdout envelope; text -> stderr human line.

    committed=True marks a write that may have reached the server, so callers
    must reconcile instead of blindly retrying.
    """
    extra = {"committed": True} if committed else None
    if output_json:
        env = envelope_error(
            code=code,
            message=message,
            retryable=retryable,
            hint=hint,
            context=context,
            **(extra or {}),
        )
        click.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        click.echo(f"Error: {message}", err=True)
        if hint:
            click.echo(f"Hint: {hint}", err=True)
        if context:
            click.echo(f"Context: {context}", err=True)
        if committed:
            click.echo("Committed: the write may have reached the server; reconcile before retrying.", err=True)


def emit_error(
    code: str,
    message: str,
    *,
    output_json: bool,
    retryable: bool = False,
    hint: str = "",
    context: str = "",
    committed: bool = False,
    exit_code: int | None = None,
) -> NoReturn:
    """Emit an error and exit with a code mapped from code.

    committed=True marks a write that may have reached the server.
    """
    report_error(
        code,
        message,
        output_json=output_json,
        retryable=retryable,
        hint=hint,
        context=context,
        committed=committed,
    )
    raise SystemExit(exit_code if exit_code is not None else exit_code_for(code))


@dataclass(frozen=True)
class ErrorSpec:
    """Classification of an exception into the public error contract."""

    code: str
    message: str
    retryable: bool = False
    hint: str = ""
    context: str = "cli"
    committed: bool = False


def _split_message(message: str) -> tuple[str, str]:
    """Split a multi-line exception message into (message, hint) for humans/agents."""
    lines = [line.strip() for line in message.strip().splitlines() if line.strip()]
    if not lines:
        return "Unexpected error", ""
    if len(lines) == 1:
        return lines[0], ""
    return lines[0], " ".join(lines[1:])


def _config_error_types() -> tuple[type[BaseException], ...]:
    """Return the config package's typed exceptions when available.

    Lazy and defensive: keeps the CLI boundary importable while the config
    package is being refactored in parallel.
    """
    try:
        from zotero_cli import config as config_module

        library_error = getattr(config_module, "LibraryResolutionError", None)
        config_error = getattr(config_module, "ConfigError", None)
    except Exception:
        return ()
    found: list[type[BaseException]] = []
    for candidate in (library_error, config_error):
        if isinstance(candidate, type) and issubclass(candidate, BaseException):
            found.append(candidate)
    return tuple(found)


def _class_name_is(exc: BaseException, name: str) -> bool:
    return type(exc).__name__ == name


def _is_zotero_schema_error(exc: BaseException) -> bool:
    """Detect the reader's unsupported-userdata-schema error without an import cycle."""
    if _class_name_is(exc, "ZoteroSchemaError"):
        return True
    try:
        from zotero_cli.core.zotero.reader import ZoteroSchemaError
    except Exception:
        return False
    return isinstance(exc, ZoteroSchemaError)


def _typed_config_exception(exc: BaseException, class_name: str) -> bool:
    if _class_name_is(exc, class_name):
        return True
    for candidate in _config_error_types():
        if candidate.__name__ == class_name and isinstance(exc, candidate):
            return True
    return False


def classify_exception(exc: BaseException, *, context: str = "cli") -> ErrorSpec:
    """Map an exception to the public error contract (code/message/hint/retryable).

    Precedence:
    1. exceptions carrying an explicit .code attribute (typed domain errors);
    2. the config package's typed exceptions (LibraryResolutionError / ConfigError);
    3. filesystem/permission/TOML problems -> configuration_error (exit 3);
    4. sqlite/OSError -> runtime_error (exit 1);
    5. anything else -> internal_error (exit 1).
    """
    explicit_code = getattr(exc, "code", None)
    if isinstance(explicit_code, str) and explicit_code:
        return ErrorSpec(
            code=explicit_code,
            message=str(exc) or explicit_code,
            retryable=bool(getattr(exc, "retryable", False)),
            hint=str(getattr(exc, "hint", "") or ""),
            context=context,
            committed=bool(getattr(exc, "committed", False)),
        )

    if _typed_config_exception(exc, "LibraryResolutionError"):
        return ErrorSpec(
            code="not_found",
            message=str(exc) or "Requested library is not available in the local database",
            hint="Run 'zot collection list' to see available collections",
            context=context,
        )
    if _typed_config_exception(exc, "ConfigError"):
        message, hint = _split_message(str(exc))
        return ErrorSpec(code="configuration_error", message=message, hint=hint, context=context)

    if _is_zotero_schema_error(exc):
        return ErrorSpec(
            code="configuration_error",
            message=f"Unsupported Zotero database schema: {exc}",
            hint="Update zotero-cli or use a supported Zotero version",
            context=context,
        )

    if isinstance(exc, (FileNotFoundError, PermissionError, IsADirectoryError)):
        message, hint = _split_message(str(exc))
        return ErrorSpec(code="configuration_error", message=message, hint=hint, context=context)
    if type(exc).__name__ == "TOMLDecodeError":
        message, hint = _split_message(str(exc))
        return ErrorSpec(
            code="configuration_error",
            message=f"Could not read configuration: {message}",
            hint=hint or "Fix the config file or run 'zot config init'",
            context=context,
        )
    if isinstance(exc, sqlite3.Error):
        message, hint = _split_message(str(exc))
        return ErrorSpec(
            code="runtime_error",
            message=f"Local Zotero database error: {message}",
            hint=hint or "Run 'zot config show' to check the data directory and database path",
            context=context,
        )
    if isinstance(exc, OSError):
        message, hint = _split_message(str(exc))
        return ErrorSpec(code="runtime_error", message=message, hint=hint, context=context)

    return ErrorSpec(
        code="internal_error",
        message=f"Unexpected {type(exc).__name__}: {exc}",
        hint="Re-run with --verbose to see the traceback, and report this as a bug",
        context=context,
    )


def emit_usage_error(exc: click.ClickException, *, output_json: bool) -> NoReturn:
    """Emit a Click usage/parameter error as validation_error and exit 3.

    Usage errors are part of the agent contract: JSON mode must still produce a
    parseable envelope, and exit 3 is the documented params/configuration code.
    """
    report_error(
        "validation_error",
        exc.format_message(),
        output_json=output_json,
        hint="Run 'zot --help' for usage, or 'zot schema' for the command tree",
        context="usage",
    )
    raise SystemExit(EXIT_VALIDATION)


def emit_exception(
    exc: BaseException,
    *,
    output_json: bool,
    verbose: bool = False,
    context: str = "cli",
) -> NoReturn:
    """Emit a classified exception (never a raw traceback) and exit with its code.

    --verbose / ZOT_DEBUG opt into writing the traceback to stderr for
    diagnostics; the machine-readable channel (stdout envelope) is unaffected.
    """
    spec = classify_exception(exc, context=context)
    if verbose:
        traceback.print_exception(exc, file=sys.stderr)
        sys.stderr.flush()
    report_error(
        spec.code,
        spec.message,
        output_json=output_json,
        retryable=spec.retryable,
        hint=spec.hint,
        context=spec.context,
        committed=spec.committed,
    )
    raise SystemExit(exit_code_for(spec.code))


__all__ = [
    "CODE_TO_EXIT",
    "verbose_enabled",
    "set_verbose",
    "EXIT_AUTH",
    "EXIT_CONFLICT",
    "EXIT_NETWORK",
    "EXIT_NOT_FOUND",
    "EXIT_OK",
    "EXIT_RUNTIME",
    "EXIT_VALIDATION",
    "ErrorSpec",
    "classify_exception",
    "emit_error",
    "emit_exception",
    "emit_usage_error",
    "exit_code_for",
    "report_error",
]
