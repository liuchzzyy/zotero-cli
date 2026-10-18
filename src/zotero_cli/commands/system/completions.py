"""zot completions: generate a shell completion script."""

from __future__ import annotations

import subprocess

import click

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


def _fallback(shell: str, failure: str) -> None:
    """Emit a deterministic, explicit fallback when the real generator is unavailable.

    stdout stays pipeable (the eval one-liner); the reason is reported on stderr
    so redirecting stdout into a shell profile never loses the warning.
    """
    info = SHELLS[shell]
    click.echo(f"# Warning: {failure}; emitting the static completion snippet instead.", err=True)
    click.echo(f"# Add this to {info['file']}:")
    click.echo(info["script"])


@click.command("completions")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completions_cmd(shell: str) -> None:
    """Generate shell completion script.

    Add the output to your shell profile to enable tab completions.

    \b
    Examples:
      zot completions bash >> ~/.bashrc
      zot completions zsh >> ~/.zshrc
      zot completions fish > ~/.config/fish/completions/zot.fish
    """
    import os

    env = os.environ.copy()
    env[SHELLS[shell]["var"].split("=")[0]] = SHELLS[shell]["var"].split("=")[1]
    try:
        result = subprocess.run(
            ["zot"],
            env=env,
            capture_output=True,
            text=True,
            timeout=_COMPLETION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _fallback(shell, f"generating completions with 'zot' timed out after {_COMPLETION_TIMEOUT_SECONDS}s")
        return
    except OSError as exc:
        _fallback(shell, f"could not execute 'zot' to generate completions ({exc.strerror or exc})")
        return

    if result.returncode != 0:
        _fallback(shell, f"'zot' exited with status {result.returncode} while generating completions")
        return
    if not result.stdout:
        _fallback(shell, "'zot' produced no completion script")
        return
    click.echo(result.stdout)
