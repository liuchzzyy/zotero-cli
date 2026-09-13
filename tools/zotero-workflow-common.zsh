#!/bin/zsh

set -eu
setopt pipefail

workflow_repo_root() {
  local start_dir="${1:A:h}"
  local candidate="$start_dir"
  while [[ "$candidate" != "/" ]]; do
    if [[ -f "$candidate/pyproject.toml" && -d "$candidate/src/zotero_cli" ]]; then
      print -r -- "$candidate"
      return 0
    fi
    candidate="${candidate:h}"
  done
  print -u2 -- "Could not locate zotero-cli repository root from $1"
  return 1
}

workflow_output_dir() {
  local repo_root="$1"
  local requested="$2"
  local prefix="$3"
  if [[ -n "$requested" ]]; then
    if [[ "$requested" = /* ]]; then
      print -r -- "${requested:A}"
    else
      print -r -- "${repo_root:A}/${requested}"
    fi
    return
  fi
  print -r -- "${repo_root:A}/log/${prefix}-$(date '+%Y%m%d-%H%M%S')"
}

workflow_assert_safe_output_dir() {
  local repo_root="${1:A}"
  local run_dir="${2:A}"
  local log_root="${repo_root}/log"
  if [[ "$run_dir" == "$repo_root" || "$run_dir" == "$log_root" || "$run_dir" != "$log_root"/* ]]; then
    print -u2 -- "Workflow output directory must be a child of $log_root: $run_dir"
    return 1
  fi
}

workflow_start() {
  local workflow_name="$1"
  local repo_root="$2"
  local run_dir="$3"
  mkdir -p "$run_dir/logs"
  typeset -g WORKFLOW_NAME="$workflow_name"
  typeset -g WORKFLOW_RUN_DIR="$run_dir"
  typeset -g WORKFLOW_RUN_LOG="$run_dir/run.log"
  typeset -g WORKFLOW_PROGRESS_LOG="$run_dir/progress.jsonl"
  typeset -g WORKFLOW_STARTED_AT="$(date +%s)"
  local timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  print -r -- "[$timestamp] $workflow_name started" | tee -a "$WORKFLOW_RUN_LOG"
  print -r -- "{\"timestamp\":\"$timestamp\",\"event\":\"run_started\",\"workflow\":\"$workflow_name\",\"repo_root\":\"$repo_root\",\"run_dir\":\"$run_dir\"}" >> "$WORKFLOW_PROGRESS_LOG"
}

workflow_section() {
  print -r -- ""
  print -r -- "[$1]" | tee -a "$WORKFLOW_RUN_LOG"
}

workflow_setting() {
  printf '  %-28s %s\n' "$1:" "$2" | tee -a "$WORKFLOW_RUN_LOG"
}

workflow_run_logged() {
  local log_path="$1"
  shift
  local -a quoted_command
  quoted_command=("${(@q)@}")
  mkdir -p "${log_path:h}"
  workflow_section "Command"
  workflow_setting "command" "${(j: :)quoted_command}"
  workflow_setting "log" "$log_path"
  set +e
  "$@" 2>&1 | tee -a "$log_path" "$WORKFLOW_RUN_LOG"
  local command_status=${pipestatus[1]}
  set -e
  if (( command_status != 0 )); then
    print -u2 -- "Command failed with exit code $command_status. See $log_path"
    return "$command_status"
  fi
}

workflow_finish() {
  local finish_status="${1:-completed}"
  local finished_at="$(date +%s)"
  local elapsed=$(( finished_at - WORKFLOW_STARTED_AT ))
  local timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  print -r -- "{\"timestamp\":\"$timestamp\",\"event\":\"run_finished\",\"workflow\":\"$WORKFLOW_NAME\",\"status\":\"$finish_status\",\"elapsed_seconds\":$elapsed}" >> "$WORKFLOW_PROGRESS_LOG"
  print -r -- "[$timestamp] Finished with status=$finish_status; elapsed=${elapsed}s" | tee -a "$WORKFLOW_RUN_LOG"
}

workflow_remove_success_logs() {
  local repo_root="$1"
  local run_dir="$2"
  workflow_assert_safe_output_dir "$repo_root" "$run_dir"
  rm -rf -- "$run_dir"
  local log_root="${repo_root:A}/log"
  if [[ -d "$log_root" && -z "$(find "$log_root" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    rmdir "$log_root"
  fi
}
