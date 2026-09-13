from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from tests.support import FIXTURES_DIR, REPO_ROOT

TOOLS_DIR = REPO_ROOT / "tools"
ZSH_SCRIPTS = [
    TOOLS_DIR / "zotero-workflow-common.zsh",
    TOOLS_DIR / "run-rag-workspace.zsh",
    TOOLS_DIR / "run-rag-evidence-search.zsh",
]


@pytest.mark.parametrize("script", ZSH_SCRIPTS, ids=lambda path: path.name)
def test_zsh_script_is_executable_and_syntax_valid(script: Path) -> None:
    assert os.access(script, os.X_OK)
    result = subprocess.run(["zsh", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", ZSH_SCRIPTS[1:], ids=lambda path: path.name)
def test_zsh_entrypoint_help(script: Path) -> None:
    result = subprocess.run([str(script), "--help"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("Usage:")


def test_workspace_zsh_dry_run_is_isolated() -> None:
    suffix = uuid.uuid4().hex[:8]
    workspace_name = f"tool-test-{suffix}"
    relative_run_dir = Path("log") / f"tool-test-{suffix}"
    run_dir = REPO_ROOT / relative_run_dir
    workspace_dir = REPO_ROOT / ".workspace" / workspace_name
    env = {
        **os.environ,
        "ZOT_DATA_DIR": str(FIXTURES_DIR),
        "ZOT_LIBRARY_ID": "",
        "ZOT_API_KEY": "",
        "ZOT_EMBEDDING_KEY": "",
        "ZOT_RERANK_KEY": "",
    }
    try:
        result = subprocess.run(
            [
                str(TOOLS_DIR / "run-rag-workspace.zsh"),
                "--workspace",
                workspace_name,
                "--collections",
                "",
                "--scan-limit",
                "3",
                "--progress-every",
                "1",
                "--dry-run",
                "--output-dir",
                str(relative_run_dir),
                "--hide-diagnostics",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "Dry-run complete" in result.stdout
        assert not workspace_dir.exists()
        assert not run_dir.exists()
    finally:
        if run_dir.exists():
            shutil.rmtree(run_dir)
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)


def test_powershell_workflow_scripts_are_removed() -> None:
    assert list(TOOLS_DIR.glob("*.ps1")) == []
