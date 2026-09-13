from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from click.testing import CliRunner, Result

from zotero_cli.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"

ISOLATED_ENV: dict[str, str] = {
    "ZOT_DATA_DIR": str(FIXTURES_DIR),
    "ZOT_FORMAT": "table",
    "ZOT_LIBRARY_ID": "",
    "ZOT_API_KEY": "",
    "ZOT_EMBEDDING_URL": "",
    "ZOT_EMBEDDING_KEY": "",
    "ZOT_EMBEDDING_MODEL": "",
    "ZOT_RERANK_URL": "",
    "ZOT_RERANK_KEY": "",
    "ZOT_RERANK_MODEL": "",
}


def invoke_cli(
    args: Sequence[str],
    *,
    json_output: bool = False,
    env: Mapping[str, str] | None = None,
    runner: CliRunner | None = None,
) -> Result:
    """Invoke the CLI against isolated fixtures with consistent defaults."""
    command = ["--json", *args] if json_output else list(args)
    command_env = {**ISOLATED_ENV, **dict(env or {})}
    return (runner or CliRunner()).invoke(main, command, env=command_env)


def invoke_agent_cli(
    args: Sequence[str],
    env: Mapping[str, str] | None = None,
    *,
    runner: CliRunner | None = None,
) -> Result:
    """Invoke with TTY auto-detection enabled for agent-contract tests."""
    return invoke_cli(args, env={"ZOT_FORMAT": "", **dict(env or {})}, runner=runner)


def parse_json_output(output: str) -> dict[str, Any]:
    """Parse a final JSON envelope while ignoring structured progress lines."""
    lines = [line for line in output.splitlines() if not line.lstrip().startswith('{"event"')]
    parsed = json.loads("\n".join(lines))
    if not isinstance(parsed, dict):
        raise AssertionError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed
