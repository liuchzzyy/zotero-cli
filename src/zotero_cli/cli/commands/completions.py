"""zot completions：生成 shell 补全脚本。"""

from __future__ import annotations

import json
import os
import subprocess

import typer

from zotero_cli.cli.output.envelope import envelope_ok

# 有界等待：补全生成会调用已安装的 zot，绝不能静默地卡住用户的 shell 启动文件。
_COMPLETION_TIMEOUT_SECONDS = 10

SHELLS = {
    "bash": {
        "var": "_ZOT_COMPLETE=bash_source",
        "script": 'eval "$(_ZOT_COMPLETE=bash_source zot)"',
        "file": "~/.bashrc",
    },
    "zsh": {
        "var": "_ZOT_COMPLETE=zsh_source",
        "script": 'eval "$(_ZOT_COMPLETE=zsh_source zot)"',
        "file": "~/.zshrc",
    },
    "fish": {
        "var": "_ZOT_COMPLETE=fish_source",
        "script": "_ZOT_COMPLETE=fish_source zot | source",
        "file": "~/.config/fish/completions/zot.fish",
    },
}


def _fallback_script(shell: str) -> str:
    info = SHELLS[shell]
    return f"# Add this to {info['file']}:\n{info['script']}\n"


def _generate(shell: str) -> tuple[str, str | None, str | None]:
    """返回 (script, failure_reason, warning)，失败时使用静态片段。

    当已安装的 zot 可执行文件不可用、超时、以非零状态退出或未产出脚本时，
    返回确定性的回退结果；失败原因会输出到 stderr（并写入 JSON envelope），
    而不是静默失败。
    """
    info = SHELLS[shell]
    env = os.environ.copy()
    env[info["var"].split("=")[0]] = info["var"].split("=")[1]
    try:
        result = subprocess.run(
            ["zot"],
            env=env,
            capture_output=True,
            text=True,
            timeout=_COMPLETION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        failure = f"generating completions with 'zot' timed out after {_COMPLETION_TIMEOUT_SECONDS}s"
        return (
            _fallback_script(shell),
            failure,
            f"# Warning: {failure}; emitting the static completion snippet instead.",
        )
    except OSError as exc:
        failure = f"could not execute 'zot' to generate completions ({exc.strerror or exc})"
        return (
            _fallback_script(shell),
            failure,
            f"# Warning: {failure}; emitting the static completion snippet instead.",
        )
    if result.returncode != 0:
        failure = f"'zot' exited with status {result.returncode} while generating completions"
        return (
            _fallback_script(shell),
            failure,
            f"# Warning: {failure}; emitting the static completion snippet instead.",
        )
    if not result.stdout:
        failure = "'zot' produced no completion script"
        return (
            _fallback_script(shell),
            failure,
            f"# Warning: {failure}; emitting the static completion snippet instead.",
        )
    return result.stdout, None, None


def completions_cmd(
    ctx: typer.Context,
    shell: str = typer.Argument(help="shell：bash、zsh 或 fish。"),
) -> None:
    """生成 shell 补全脚本。

    将输出加入你的 shell 配置文件即可启用 Tab 补全。JSON 模式返回单个
    envelope {shell, script, fallback, warning}，而不是原始脚本；警告仍输出到 stderr。

    \b
    示例：
      zot completions bash >> ~/.bashrc
      zot completions zsh >> ~/.zshrc
      zot completions fish > ~/.config/fish/completions/zot.fish
      zot --json completions bash    # envelope {shell, script, fallback, warning}
    """
    if shell not in SHELLS:
        raise typer.BadParameter("shell 必须是 bash、zsh 或 fish")
    json_out = bool(ctx.obj.get("json", False)) if ctx.obj else False
    script, failure, warning = _generate(shell)
    fallback = failure is not None
    if warning:
        typer.echo(warning, err=True)
    if json_out:
        payload = {"shell": shell, "script": script, "fallback": fallback, "warning": failure}
        typer.echo(json.dumps(envelope_ok(payload), indent=2, ensure_ascii=False))
        return
    typer.echo(script)
