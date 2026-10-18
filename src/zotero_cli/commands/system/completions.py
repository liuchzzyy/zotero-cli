"""zot completions: generate a shell completion script."""

from __future__ import annotations

import json
import os
import subprocess

import click

from zotero_cli.output.envelope import envelope_ok

# Bounded wait: completion generation shells out to the installed zot and must
# never hang a user's shell startup file silently.
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
    """Return (script, failure_reason, warning) where a failure uses the static snippet.

    A deterministic fallback is returned when the installed zot executable is
    unavailable, times out, exits non-zero or produces no script; the reason is
    surfaced on stderr (and in the JSON envelope) instead of failing silently.
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


@click.command("completions")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.pass_context
def completions_cmd(ctx: click.Context, shell: str) -> None:
    """Generate shell completion script.

    Add the output to your shell profile to enable tab completions. JSON mode
    returns one envelope {shell, script, fallback, warning} instead of the raw
    script; warnings still go to stderr.

    \b
    Examples:
      zot completions bash >> ~/.bashrc
      zot completions zsh >> ~/.zshrc
      zot completions fish > ~/.config/fish/completions/zot.fish
      zot --json completions bash    # envelope {shell, script, fallback, warning}
    """
    json_out = bool(ctx.obj.get("json", False)) if ctx.obj else False
    script, failure, warning = _generate(shell)
    fallback = failure is not None
    if warning:
        click.echo(warning, err=True)
    if json_out:
        payload = {"shell": shell, "script": script, "fallback": fallback, "warning": failure}
        click.echo(json.dumps(envelope_ok(payload), indent=2, ensure_ascii=False))
        return
    click.echo(script)
