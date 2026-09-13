#!/bin/zsh

set -eu
setopt pipefail

SCRIPT_PATH="${0:A}"
SCRIPT_DIR="${SCRIPT_PATH:h}"
source "$SCRIPT_DIR/zotero-workflow-common.zsh"

question=""
workspace_name="rag-workspace"
mode="auto"
pdf_kind="any"
top_k=8
rerank_top_n=50
output_dir=""
json_output=false
no_rerank=false
keep_log=false

usage() {
  cat <<'EOF'
Usage: tools/run-rag-evidence-search.zsh --question TEXT [options]

Query a zotero-cli RAG workspace and keep structured run logs.

Options:
  --question TEXT          Required evidence-search question
  --workspace NAME        Workspace name (default: rag-workspace)
  --mode MODE             auto, bm25, semantic, or hybrid
  --pdf-kind KIND         any, main, or supplementary
  --top-k N               Result count (default: 8)
  --rerank-top-n N        Candidate count for reranking
  --output-dir PATH       Run directory under repository log/
  --json                  Emit JSON query output
  --no-rerank             Skip online reranking
  --keep-log              Keep run logs after success
  -h, --help              Show this help
EOF
}

need_value() {
  if (( $# < 2 )); then
    print -u2 -- "Missing value for $1"
    exit 2
  fi
}

while (( $# )); do
  case "$1" in
    --question) need_value "$@"; question="$2"; shift 2 ;;
    --workspace) need_value "$@"; workspace_name="$2"; shift 2 ;;
    --mode) need_value "$@"; mode="$2"; shift 2 ;;
    --pdf-kind) need_value "$@"; pdf_kind="$2"; shift 2 ;;
    --top-k) need_value "$@"; top_k="$2"; shift 2 ;;
    --rerank-top-n) need_value "$@"; rerank_top_n="$2"; shift 2 ;;
    --output-dir) need_value "$@"; output_dir="$2"; shift 2 ;;
    --json) json_output=true; shift ;;
    --no-rerank) no_rerank=true; shift ;;
    --keep-log) keep_log=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) print -u2 -- "Unknown option: $1"; usage >&2; exit 2 ;;
  esac
done

[[ -n "$question" ]] || { print -u2 -- "--question is required"; exit 2; }
[[ "$mode" == (auto|bm25|semantic|hybrid) ]] || { print -u2 -- "Invalid --mode: $mode"; exit 2; }
[[ "$pdf_kind" == (any|main|supplementary) ]] || { print -u2 -- "Invalid --pdf-kind: $pdf_kind"; exit 2; }
[[ "$top_k" == <-> && "$top_k" -gt 0 ]] || { print -u2 -- "--top-k must be greater than 0"; exit 2; }
[[ "$rerank_top_n" == <-> && "$rerank_top_n" -gt 0 ]] || { print -u2 -- "--rerank-top-n must be greater than 0"; exit 2; }

repo_root="$(workflow_repo_root "$SCRIPT_PATH")"
run_dir="$(workflow_output_dir "$repo_root" "$output_dir" "rag-evidence-search")"
workflow_assert_safe_output_dir "$repo_root" "$run_dir"
workflow_start "rag-evidence-search" "$repo_root" "$run_dir"

workflow_section "RAG Evidence Search"
workflow_setting "repo" "$repo_root"
workflow_setting "workspace" "$workspace_name"
workflow_setting "question" "$question"
workflow_setting "mode" "$mode"
workflow_setting "pdf_kind" "$pdf_kind"
workflow_setting "top_k" "$top_k"
workflow_setting "rerank" "$([[ "$no_rerank" == true ]] && print false || print true)"
workflow_setting "output" "$run_dir"

query_cmd=(uv run zot)
if [[ "$json_output" == true ]]; then query_cmd+=(--json); else query_cmd+=(--no-json); fi
query_cmd+=(workspace query "$question" --workspace "$workspace_name" --mode "$mode" --top-k "$top_k" --pdf-kind "$pdf_kind")
if [[ "$no_rerank" != true ]]; then query_cmd+=(--rerank --rerank-top-n "$rerank_top_n"); fi

workflow_status="failed"
trap 'workflow_finish "$workflow_status"' EXIT
cd "$repo_root"
workflow_run_logged "$run_dir/logs/query.log" "${query_cmd[@]}"
workflow_status="completed"
trap - EXIT
workflow_finish "$workflow_status"

if [[ "$keep_log" == true ]]; then
  print -r -- "Kept run directory: $run_dir"
else
  workflow_remove_success_logs "$repo_root" "$run_dir"
fi
