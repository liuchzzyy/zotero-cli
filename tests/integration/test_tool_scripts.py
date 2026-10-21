from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import uuid
from pathlib import Path

import pytest
from tests.support import FIXTURES_DIR, REPO_ROOT

TOOLS_DIR = (REPO_ROOT / "tool" / "paperrag") if (REPO_ROOT / "tool" / "paperrag").exists() else (REPO_ROOT / "tool")
ZSH_SCRIPTS = [
    TOOLS_DIR / "zotero-workflow-common.zsh",
    TOOLS_DIR / "run-rag-workspace.zsh",
    TOOLS_DIR / "run-rag-evidence-search.zsh",
]

# Keep a hung helper script from blocking the entire suite (M-19).
SUBPROCESS_TIMEOUT = 120


@pytest.mark.parametrize("script", ZSH_SCRIPTS, ids=lambda path: path.name)
def test_zsh_script_is_executable_and_syntax_valid(script: Path) -> None:
    assert os.access(script, os.X_OK)
    result = subprocess.run(["zsh", "-n", str(script)], capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", ZSH_SCRIPTS[1:], ids=lambda path: path.name)
def test_zsh_entrypoint_help(script: Path) -> None:
    result = subprocess.run(
        [str(script), "--help"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT
    )
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
            timeout=SUBPROCESS_TIMEOUT,
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


def test_workspace_zsh_default_collection_uses_renamed_inbox(tmp_path: Path) -> None:
    data_dir = tmp_path / "zotero"
    data_dir.mkdir()
    db_path = data_dir / "zotero.sqlite"
    shutil.copy2(FIXTURES_DIR / "zotero.sqlite", db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE collections SET collectionName = ? WHERE collectionName = ?",
            ("00 | 收件箱", "Machine Learning"),
        )

    suffix = uuid.uuid4().hex[:8]
    workspace_name = f"tool-test-{suffix}"
    run_dir = REPO_ROOT / "log" / f"tool-test-{suffix}"
    env = {**os.environ, "ZOT_DATA_DIR": str(data_dir), "ZOT_LIBRARY_ID": "", "ZOT_API_KEY": ""}
    try:
        result = subprocess.run(
            [
                str(TOOLS_DIR / "run-rag-workspace.zsh"),
                "--workspace",
                workspace_name,
                "--dry-run",
                "--keep-inventory",
                "--output-dir",
                str(run_dir),
                "--hide-diagnostics",
            ],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        inventory = json.loads((run_dir / "inventory.json").read_text(encoding="utf-8"))
        assert inventory["collections"] == ["00 | 收件箱"]
        assert inventory["collection_item_counts"]["00 | 收件箱"] > 0
        assert not (REPO_ROOT / ".workspace" / workspace_name).exists()
    finally:
        if run_dir.exists():
            shutil.rmtree(run_dir)


def test_default_workflow_output_dirs_are_unique() -> None:
    common = TOOLS_DIR / "zotero-workflow-common.zsh"
    command = (
        f'source "{common}"\n'
        f'workflow_output_dir "{REPO_ROOT}" "" "rag-workspace"\n'
        f'workflow_output_dir "{REPO_ROOT}" "" "rag-workspace"\n'
    )
    result = subprocess.run(
        ["zsh", "-c", command], cwd=REPO_ROOT, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 2
    # Same-second invocations must not collide on one run directory.
    assert lines[0] != lines[1]
    for line in lines:
        assert Path(line).parent == REPO_ROOT / "log"
        assert Path(line).name.startswith("rag-workspace-")


def test_success_cleanup_removes_a_generated_run_dir() -> None:
    suffix = uuid.uuid4().hex[:8]
    run_dir = REPO_ROOT / "log" / f"tool-test-clean-{suffix}"
    common = TOOLS_DIR / "zotero-workflow-common.zsh"
    command = (
        f'source "{common}"\n'
        f'workflow_start "cleanup-test" "{REPO_ROOT}" "{run_dir}"\n'
        f'workflow_remove_success_logs "{REPO_ROOT}" "{run_dir}"\n'
    )
    try:
        result = subprocess.run(
            ["zsh", "-c", command], cwd=REPO_ROOT, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT
        )
        assert result.returncode == 0, result.stderr
        assert not run_dir.exists()
    finally:
        if run_dir.exists():
            shutil.rmtree(run_dir)


def test_success_cleanup_keeps_a_preexisting_run_dir() -> None:
    suffix = uuid.uuid4().hex[:8]
    run_dir = REPO_ROOT / "log" / f"tool-test-pre-{suffix}"
    run_dir.mkdir(parents=True)
    marker = run_dir / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    common = TOOLS_DIR / "zotero-workflow-common.zsh"
    command = (
        f'source "{common}"\n'
        f'workflow_start "cleanup-test" "{REPO_ROOT}" "{run_dir}"\n'
        f'workflow_remove_success_logs "{REPO_ROOT}" "{run_dir}"\n'
    )
    try:
        result = subprocess.run(
            ["zsh", "-c", command], cwd=REPO_ROOT, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT
        )
        assert result.returncode == 0, result.stderr
        assert "Kept pre-existing run directory" in result.stdout
        assert marker.exists()
    finally:
        if run_dir.exists():
            shutil.rmtree(run_dir)


def test_powershell_workflow_scripts_are_removed() -> None:
    assert list(TOOLS_DIR.glob("*.ps1")) == []
