#!/usr/bin/env zsh

set -eu
setopt pipefail

SCRIPT_PATH="${0:A}"
SCRIPT_DIR="${SCRIPT_PATH:h}"
source "$SCRIPT_DIR/zotero-workflow-common.zsh"

workspace_name="${PAPERRAG_WORKSPACE:-00_收件箱}"
collections_csv="${PAPERRAG_COLLECTIONS:-00 | 收件箱}"
profile_name=""
scan_limit=""
progress_every=""
embed_batch_size=""
embed_limit=""
embed_max_retries=""
embed_retry_sleep=""
embed_heartbeat_seconds=""
output_dir=""
dry_run=false
no_index=false
no_embed=false
embed_only=false
force_rebuild=false
keep_inventory=false
keep_log=false
hide_diagnostics=false

usage() {
  cat <<'EOF'
Usage: tool/paperrag/run-rag-workspace.zsh [options]

增量清点、索引并嵌入 Zotero PDF RAG 工作区。

Options:
  --pdf-rag, --workspace NAME  工作区名称 (default: 00_收件箱)
  --collections CSV           分类名称或 key 列表 (default: 00 | 收件箱)
  --profile NAME               配置 profile 名称
  --scan-limit N               最多扫描的条目数
  --progress-every N           清点进度提示间隔
  --embed-batch-size N         嵌入批处理大小
  --embed-limit N              嵌入块最大处理数量；0 为全部
  --embed-max-retries N        每批嵌入最大重试次数
  --embed-retry-sleep SECONDS  重试等待秒数
  --embed-heartbeat SECONDS    心跳间隔秒数
  --output-dir PATH            运行日志目录（默认在 .zot/log/ 下）
  --dry-run                    仅执行清点，不修改工作区与索引
  --no-index                   仅更新清点与工作区条目列表
  --no-embed                   仅建立词条索引，跳过向量嵌入
  --embed-only                 仅对已有未嵌入 chunk 补充向量
  --force-rebuild              强制完全重建索引
  --keep-inventory             成功后保留清点数据与目录
  --keep-log                   成功后保留运行日志
  --hide-diagnostics           不输出调试查看建议
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
    --workspace|--pdf-rag) need_value "$@"; workspace_name="$2"; shift 2 ;;
    --collections) need_value "$@"; collections_csv="$2"; shift 2 ;;
    --profile) need_value "$@"; profile_name="$2"; shift 2 ;;
    --scan-limit) need_value "$@"; scan_limit="$2"; shift 2 ;;
    --progress-every) need_value "$@"; progress_every="$2"; shift 2 ;;
    --embed-batch-size) need_value "$@"; embed_batch_size="$2"; shift 2 ;;
    --embed-limit) need_value "$@"; embed_limit="$2"; shift 2 ;;
    --embed-max-retries) need_value "$@"; embed_max_retries="$2"; shift 2 ;;
    --embed-retry-sleep) need_value "$@"; embed_retry_sleep="$2"; shift 2 ;;
    --embed-heartbeat) need_value "$@"; embed_heartbeat_seconds="$2"; shift 2 ;;
    --output-dir) need_value "$@"; output_dir="$2"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --no-index) no_index=true; shift ;;
    --no-embed) no_embed=true; shift ;;
    --embed-only) embed_only=true; shift ;;
    --force-rebuild) force_rebuild=true; shift ;;
    --keep-inventory) keep_inventory=true; shift ;;
    --keep-log) keep_log=true; shift ;;
    --hide-diagnostics) hide_diagnostics=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) print -u2 -- "Unknown option: $1"; usage >&2; exit 2 ;;
  esac
done

if [[ "$embed_only" == true && "$no_embed" == true ]]; then
  print -u2 -- "--embed-only and --no-embed cannot be combined"
  exit 2
fi
for numeric in "$scan_limit" "$progress_every" "$embed_batch_size"; do
  [[ -z "$numeric" ]] && continue
  [[ "$numeric" == <-> && "$numeric" -gt 0 ]] || { print -u2 -- "Positive integer expected: $numeric"; exit 2; }
done
for numeric in "$embed_limit" "$embed_max_retries"; do
  [[ -z "$numeric" ]] && continue
  [[ "$numeric" == <-> ]] || { print -u2 -- "Non-negative integer expected: $numeric"; exit 2; }
done

repo_root="$(workflow_repo_root "$SCRIPT_PATH")"
run_dir="$(workflow_output_dir "$repo_root" "$output_dir" "rag-workspace")"
workflow_assert_safe_output_dir "$repo_root" "$run_dir"
workflow_start "rag-workspace" "$repo_root" "$run_dir"
inventory_path="$run_dir/inventory.json"

workflow_section "Workspace RAG Incremental Index"
workflow_setting "repo" "$repo_root"
workflow_setting "workspace" "$workspace_name"
workflow_setting "collections" "$collections_csv"
workflow_setting "output" "$run_dir"
workflow_setting "dry_run" "$dry_run"
workflow_setting "no_index" "$no_index"
workflow_setting "no_embed" "$no_embed"
workflow_setting "embed_only" "$embed_only"
workflow_setting "force_rebuild" "$force_rebuild"

if [[ "$hide_diagnostics" != true ]]; then
  workflow_section "Optional Diagnostics"
  print -r -- "  tail -f ${(q)run_dir}/run.log"
  print -r -- "  tail -f ${(q)run_dir}/progress.jsonl"
  print -r -- "  tail -f ${(q)run_dir}/logs/index.log"
fi

inventory_cmd=(uv run python -u "$SCRIPT_DIR/workspace_inventory.py"
  --workspace "$workspace_name"
  --output "$inventory_path")
[[ -n "$scan_limit" ]] && inventory_cmd+=(--scan-limit "$scan_limit")
[[ -n "$progress_every" ]] && inventory_cmd+=(--progress-every "$progress_every")
[[ -n "$profile_name" ]] && inventory_cmd+=(--profile "$profile_name")
if [[ "$dry_run" == true ]]; then
  inventory_cmd+=(--dry-run)
fi
if [[ -n "$collections_csv" ]]; then
  for collection in ${(s:,:)collections_csv}; do
    collection="${collection#${collection%%[![:space:]]*}}"
    collection="${collection%${collection##*[![:space:]]}}"
    [[ -n "$collection" ]] && inventory_cmd+=(--collection "$collection")
  done
fi

workflow_status="failed"
trap 'workflow_finish "$workflow_status"' EXIT
cd "$repo_root"
workflow_run_logged "$run_dir/logs/inventory.log" "${inventory_cmd[@]}"

inventory_values=("${(@f)$(uv run python - "$inventory_path" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("pending_index_items", "local_pdf_items", "indexed_chunk_count", "chunks_with_embeddings", "chunks_missing_embeddings"):
    print(int(data.get(key, 0) or 0))
PY
)}")
pending_index_items="${inventory_values[1]:-0}"
local_pdf_items="${inventory_values[2]:-0}"
indexed_chunk_count="${inventory_values[3]:-0}"
chunks_with_embeddings="${inventory_values[4]:-0}"
chunks_missing_embeddings="${inventory_values[5]:-0}"

workflow_section "Inventory Summary"
workflow_setting "local_pdf_items" "$local_pdf_items"
workflow_setting "pending_index_items" "$pending_index_items"
workflow_setting "indexed_chunk_count" "$indexed_chunk_count"
workflow_setting "chunks_with_embeddings" "$chunks_with_embeddings"
workflow_setting "chunks_missing_embeddings" "$chunks_missing_embeddings"

run_embed() {
  local embed_log="$run_dir/logs/embed.log"
  local previous_batch_size="${ZOT_EMBEDDING_BATCH_SIZE-}"
  local had_previous_batch_size=${+ZOT_EMBEDDING_BATCH_SIZE}
  local embed_cmd=(uv run zot)
  [[ -n "$profile_name" ]] && embed_cmd+=(--profile "$profile_name")
  embed_cmd+=(pdf-rag embed "$workspace_name" --progress-lines)
  [[ -n "$embed_batch_size" ]] && embed_cmd+=(--batch-size "$embed_batch_size")
  [[ -n "$embed_max_retries" ]] && embed_cmd+=(--max-retries "$embed_max_retries")
  [[ -n "$embed_retry_sleep" ]] && embed_cmd+=(--retry-sleep "$embed_retry_sleep")
  [[ -n "$embed_heartbeat_seconds" ]] && embed_cmd+=(--heartbeat-seconds "$embed_heartbeat_seconds")
  [[ -n "$embed_limit" ]] && embed_cmd+=(--limit "$embed_limit")
  workflow_section "Embedding Backfill"
  [[ -n "$embed_batch_size" ]] && export ZOT_EMBEDDING_BATCH_SIZE="$embed_batch_size"
  local embed_status=0
  if workflow_run_logged "$embed_log" "${embed_cmd[@]}"; then
    embed_status=0
  else
    embed_status=$?
  fi
  if (( had_previous_batch_size )); then
    export ZOT_EMBEDDING_BATCH_SIZE="$previous_batch_size"
  else
    unset ZOT_EMBEDDING_BATCH_SIZE
  fi
  return "$embed_status"
}

if [[ "$dry_run" == true ]]; then
  print -r -- "Dry-run complete. No workspace, index, or embedding changes were made."
elif [[ "$embed_only" == true ]]; then
  if (( chunks_missing_embeddings > 0 )); then run_embed; else print -r -- "No missing embeddings found."; fi
elif [[ "$no_index" == true ]]; then
  print -r -- "Inventory/workspace update complete; indexing skipped."
elif (( local_pdf_items == 0 )) && [[ "$force_rebuild" != true ]]; then
  print -r -- "No local PDF items found. Nothing to index."
else
  ran_index=false
  if (( pending_index_items == 0 )) && [[ "$force_rebuild" != true ]]; then
    print -r -- "RAG index is already up to date for '$workspace_name'."
  else
    index_cmd=(uv run zot)
    [[ -n "$profile_name" ]] && index_cmd+=(--profile "$profile_name")
    index_cmd+=(pdf-rag index "$workspace_name" --progress-lines --item-progress --no-embed)
    [[ "$force_rebuild" == true ]] && index_cmd+=(--force)
    workflow_section "Item Index"
    workflow_run_logged "$run_dir/logs/index.log" "${index_cmd[@]}"
    ran_index=true
  fi
  if [[ "$no_embed" == true ]]; then
    print -r -- "Embedding backfill skipped."
  elif [[ "$ran_index" == true ]] || (( chunks_missing_embeddings > 0 )); then
    run_embed
  else
    print -r -- "RAG embeddings are already complete."
  fi
fi

workflow_status="completed"
trap - EXIT
workflow_finish "$workflow_status"
workflow_section "Complete"
workflow_setting "pdf_rag/index" ".zot/pdf_rag/$workspace_name"
workflow_setting "MinerU parse cache" ".zot/state/mineru"
if [[ "$keep_log" != true && "$keep_inventory" != true ]]; then
  workflow_remove_success_logs "$repo_root" "$run_dir"
else
  print -r -- "Kept run directory: $run_dir"
fi
