#!/usr/bin/env zsh
set -e

repo_dir=${0:A:h:h:h}
cd "$repo_dir"
uv run python -m zotero_cli.ai_notes2.batch "$@"
