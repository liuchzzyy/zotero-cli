#!/usr/bin/env zsh

set -eu
setopt pipefail

SCRIPT_PATH="${0:A}"
SCRIPT_DIR="${SCRIPT_PATH:h}"
source "$SCRIPT_DIR/zotero-workflow-common.zsh"

question=""
workspace_name="${PAPERRAG_WORKSPACE:-rag-workspace}"
profile_name=""
mode="auto"
pdf_kind="any"
top_k=""
rerank_top_n=""
output_dir=""
json_output=false
no_rerank=false
keep_log=false

usage() {
  cat <<'EOF'
Usage: tool/paperrag/run-rag-evidence-search.zsh --question TEXT [options]

查询 Zotero PDF RAG 工作区并记录结构化运行日志。

Options:
  --question TEXT              检索或证据提问文本 (必填)
  --pdf-rag, --workspace NAME  工作区名称 (default: rag-workspace)
  --profile NAME               配置 profile 名称
  --mode MODE                  auto, bm25, semantic, 或 hybrid
  --pdf-kind KIND              any, main, 或 supplementary
  --top-k N                    返回结果条数 (default: 8)
  --rerank-top-n N             参与重排的候选条目数 (default: 50)
  --output-dir PATH            运行日志输出目录（默认在 .zot/log/ 下）
  --json                       输出 JSON 格式检索结果
  --no-rerank                  跳过重排模型计算
  --keep-log                   成功后保留运行日志
  -h, --help                   显示本帮助
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
    --workspace|--pdf-rag) need_value "$@"; workspace_name="$2"; shift 2 ;;
    --profile) need_value "$@"; profile_name="$2"; shift 2 ;;
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
[[ -z "$top_k" || ( "$top_k" == <-> && "$top_k" -gt 0 ) ]] || { print -u2 -- "--top-k must be greater than 0"; exit 2; }
[[ -z "$rerank_top_n" || ( "$rerank_top_n" == <-> && "$rerank_top_n" -gt 0 ) ]] || { print -u2 -- "--rerank-top-n must be greater than 0"; exit 2; }

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
[[ -n "$profile_name" ]] && query_cmd+=(--profile "$profile_name")
if [[ "$json_output" == true ]]; then query_cmd+=(--json); else query_cmd+=(--no-json); fi
query_cmd+=(pdf-rag query "$question" --pdf-rag "$workspace_name" --mode "$mode" --pdf-kind "$pdf_kind")
[[ -n "$top_k" ]] && query_cmd+=(--top-k "$top_k")
if [[ "$no_rerank" != true ]]; then
  query_cmd+=(--rerank)
  [[ -n "$rerank_top_n" ]] && query_cmd+=(--rerank-top-n "$rerank_top_n")
fi

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
