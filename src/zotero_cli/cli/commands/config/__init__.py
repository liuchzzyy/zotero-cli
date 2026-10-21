"""`zot config`：查看配置、管理 profile 与 MinerU 缓存。"""

from __future__ import annotations

import typer

from zotero_cli.cli.commands.config.cache import app as cache_app
from zotero_cli.cli.commands.config.commands import init_command, show_command
from zotero_cli.cli.commands.config.profiles import app as profile_app

app = typer.Typer(
    help="管理 zot 配置。",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.command("init")(init_command)
app.command("show")(show_command)
app.add_typer(profile_app, name="profile")
app.add_typer(cache_app, name="cache")


@app.callback()
def config_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
