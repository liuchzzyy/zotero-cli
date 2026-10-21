"""`zot` Typer 根入口：全局选项、请求作用域与更新提示。"""

from __future__ import annotations

import os
import sys

import click
import typer
from typer.main import get_command

from zotero_cli import __version__
from zotero_cli.cli.ai_notes import ai_note
from zotero_cli.cli.ai_pdf import ai_pdf
from zotero_cli.cli.metadata import metadata_app
from zotero_cli.cli.registry import register_commands
from zotero_cli.cli.tiered_group import TieredGroup
from zotero_cli.cli.workspace.typer_commands import workspace_app
from zotero_cli.cli.zotero.reading import app as zotero_reading_app
from zotero_cli.output.envelope import request_scope
from zotero_cli.output.exit_codes import set_verbose

app = typer.Typer(
    cls=TieredGroup,
    add_completion=False,
    help="zot — Zotero CLI for AI agents。",
    no_args_is_help=False,
    context_settings={"help_option_names": ["--help"]},
    rich_markup_mode=None,
)
app.add_typer(zotero_reading_app, name="zotero", help="管理和读取 Zotero 基础数据。")
app.add_typer(workspace_app, name="workspace", help="管理本地工作区及其检索索引。")
app.add_typer(metadata_app, name="metadata", help="读取和更新 Zotero 元数据。")
app.command(name="ai-pdf", help="通过 MinerU 解析 Zotero PDF。")(ai_pdf)
app.command(name="ai-note", help="生成并写回 AI 分析笔记。")(ai_note)


def _version_callback(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    """在执行常规参数解析前输出版本。"""
    if value and not ctx.resilient_parsing:
        typer.echo(f"zot, version {__version__}")
        ctx.exit()


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    output_json: bool | None = typer.Option(None, "--json", help="以 JSON 输出；非 TTY 时自动启用。"),
    output_no_json: bool = typer.Option(False, "--no-json", help="即使非 TTY 也强制人类可读输出。"),
    limit: int = typer.Option(50, help="结果上限。"),
    detail: str = typer.Option("standard", help="输出详情：minimal、standard 或 full。"),
    no_interaction: bool = typer.Option(False, "--no-interaction", help="自动化时禁用交互提示。"),
    verbose: bool = typer.Option(False, "--verbose", help="意外错误时输出回溯。"),
    profile: str | None = typer.Option(None, help="配置 profile 名称。"),
    library: str = typer.Option("user", help="库：user 或 group:<id>。"),
    version: bool = typer.Option(
        False, "--version", is_eager=True, callback=_version_callback, help="显示版本并退出。"
    ),
) -> None:
    """运行 `zot zotero` 访问基础 Zotero 能力。"""
    if detail not in {"minimal", "standard", "full"}:
        raise typer.BadParameter("detail 必须是 minimal、standard 或 full")
    scope = request_scope()
    request_id = scope.__enter__()
    ctx.call_on_close(lambda: scope.__exit__(None, None, None))
    ctx.ensure_object(dict)
    ctx.obj["request_id"] = request_id
    set_verbose(verbose)
    if output_json is not None and output_no_json:
        raise typer.BadParameter("不能同时使用 --json 和 --no-json")
    if output_json is None:
        environment_format = os.environ.get("ZOT_FORMAT", "").lower()
        if environment_format == "json":
            output_json = True
        elif environment_format in {"table", "text"}:
            output_json = False
        else:
            output_json = not sys.stdout.isatty()
    if output_no_json:
        output_json = False
    ctx.obj.update(
        json=output_json,
        limit=limit,
        detail=detail,
        no_interaction=no_interaction,
        verbose=verbose,
        profile=profile or os.environ.get("ZOT_PROFILE"),
    )
    if library == "user":
        ctx.obj.update(library_type="user", group_id=None)
    elif library.startswith("group:") and library[6:].isdigit():
        ctx.obj.update(library_type="group", group_id=library[6:])
    else:
        raise typer.BadParameter("library 必须是 user 或 group:<id>")
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


def _after_command(_result: object, **options: object) -> None:
    """仅在交互模式下显示更新提示。"""
    if options.get("output_json") or options.get("no_interaction") or not sys.stderr.isatty():
        return
    from zotero_cli.core.version_check import check_for_update, update_check_disabled, upgrade_command

    if update_check_disabled():
        return
    latest = check_for_update(__version__)
    if latest:
        click.echo(
            click.style(f"\n 可更新：v{__version__} → v{latest}。运行：{upgrade_command()}", fg="yellow"), err=True
        )


main = get_command(app)
main._result_callback = _after_command
register_commands(main)
