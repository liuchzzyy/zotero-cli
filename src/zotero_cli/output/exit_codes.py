"""面向编排器与代理的稳定 CLI 退出码与类型化错误输出。

退出码契约（记录于 AGENTS.md / 内置 skill）：
0 = 正常，1 = 运行时/内部错误，2 = 认证，3 = 校验/配置/参数，
4 = 未找到，5 = 网络，6 = 冲突。
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

import typer
import typer._click as typer_click

from zotero_cli.output.envelope import envelope_error

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_AUTH = 2
EXIT_VALIDATION = 3
EXIT_NOT_FOUND = 4
EXIT_NETWORK = 5
EXIT_CONFLICT = 6

CODE_TO_EXIT = {
    # 认证 / 授权
    "auth_missing": EXIT_AUTH,
    "auth_invalid": EXIT_AUTH,
    "auth_expired": EXIT_AUTH,
    # 校验、配置、确认、用法
    "validation_error": EXIT_VALIDATION,
    "configuration_error": EXIT_VALIDATION,
    "confirmation_required": EXIT_VALIDATION,
    # 查找
    "not_found": EXIT_NOT_FOUND,
    # 网络 / 远程提供方
    "network_error": EXIT_NETWORK,
    "rate_limited": EXIT_NETWORK,
    # 远程文库冲突
    "conflict": EXIT_CONFLICT,
    # 运行时 / 内部故障
    "runtime_error": EXIT_RUNTIME,
    "internal_error": EXIT_RUNTIME,
    "api_error": EXIT_RUNTIME,
}


_verbose_mode: contextvars.ContextVar[bool] = contextvars.ContextVar("_verbose_mode", default=False)
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def set_verbose(enabled: bool) -> None:
    """记录当前调用/上下文中解析到的 --verbose 标志。"""
    _verbose_mode.set(enabled)


def verbose_enabled() -> bool:
    """当请求输出 traceback 时返回 True。

    --verbose 从解析后的根标志记录（绝不直接读取原始 argv），环境变量
    ZOT_DEBUG 是其脚本等价形式。
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
    """输出错误但不退出。JSON -> stdout envelope；文本 -> stderr 人类可读行。

    committed=True 表示写入可能已到达服务器，因此调用方必须进行对账，
    而不是盲目重试。
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
        typer.echo(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        typer.echo(f"Error: {message}", err=True)
        if hint:
            typer.echo(f"Hint: {hint}", err=True)
        if context:
            typer.echo(f"Context: {context}", err=True)
        if committed:
            typer.echo("Committed: the write may have reached the server; reconcile before retrying.", err=True)


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
    """输出错误，并以由 code 映射得到的退出码退出。

    committed=True 表示写入可能已到达服务器。
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
    """将异常归类到公开的错误契约中。"""

    code: str
    message: str
    retryable: bool = False
    hint: str = ""
    context: str = "cli"
    committed: bool = False


def _split_message(message: str) -> tuple[str, str]:
    """将多行异常消息拆分为 (message, hint)，供人类与代理使用。"""
    lines = [line.strip() for line in message.strip().splitlines() if line.strip()]
    if not lines:
        return "Unexpected error", ""
    if len(lines) == 1:
        return lines[0], ""
    return lines[0], " ".join(lines[1:])


def _config_error_types() -> tuple[type[BaseException], ...]:
    """在可用时返回配置包的类型化异常。

    延迟且防御性处理：在配置包并行重构期间，保持 CLI 边界可导入。
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
    """在不引入循环导入的情况下检测读取器的用户数据 schema 不受支持错误。"""
    if _class_name_is(exc, "ZoteroSchemaError"):
        return True
    try:
        from zotero_cli.zotero.reader import ZoteroSchemaError
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
    """将异常映射到公开的错误契约（code/message/hint/retryable）。

    优先级：
    1. 带显式 .code 属性的异常（类型化领域错误）；
    2. 配置包的类型化异常（LibraryResolutionError / ConfigError）；
    3. 文件系统/权限/TOML 问题 -> configuration_error（退出码 3）；
    4. sqlite/OSError -> runtime_error（退出码 1）；
    5. 其他任何情况 -> internal_error（退出码 1）。
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
            hint="Run 'zot zotero collection list' to see available collections",
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


def emit_usage_error(exc: typer_click.ClickException, *, output_json: bool) -> NoReturn:
    """将 Click 用法/参数错误作为 validation_error 输出并退出码 3。

    用法错误是代理契约的一部分：JSON 模式仍须产生可解析的 envelope，
    退出码 3 是文档化的参数/配置错误码。
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
    """输出已归类的异常（绝不输出原始 traceback），并以对应退出码退出。

    --verbose / ZOT_DEBUG 会额外把 traceback 写入 stderr 以便诊断；机器可读
    通道（stdout envelope）不受影响。
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
