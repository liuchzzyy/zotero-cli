#!/bin/zsh

set -eu
setopt pipefail

SCRIPT_PATH="${0:A}"
SCRIPT_DIR="${SCRIPT_PATH:h}"
source "$SCRIPT_DIR/zotero-workflow-common.zsh"

workspace_name="00_收件箱"
collections_csv="00_收件箱"
scan_limit=100000
progress_every=100
embed_batch_size=10
embed_limit=0
embed_max_retries=8
embed_retry_sleep=10
embed_heartbeat_seconds=15
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
Usage: tools/run-rag-workspace.zsh [options]

Incrementally inventory, index, and embed a Zotero workspace.

Options:
  --workspace NAME             Workspace name (default: 00_收件箱)
  --collections CSV           Collection names/keys, comma-separated
  --scan-limit N               Maximum Zotero items to scan
  --progress-every N           Inventory progress interval
  --embed-batch-size N         Embedding provider batch size
  --embed-limit N              Maximum missing chunks; 0 means all
  --embed-max-retries N        Retries per embedding batch
  --embed-retry-sleep SECONDS  Delay between retries
  --embed-heartbeat SECONDS    Progress heartbeat interval
  --output-dir PATH            Run directory under repository log/
  --dry-run                    Inventory only; do not change workspace/index
  --no-index                   Update inventory/workspace only
  --no-embed                   Build term index without embeddings
  --embed-only                 Backfill existing missing embeddings only
  --force-rebuild              Rebuild the workspace index
  --keep-inventory             Keep inventory and run directory
  --keep-log                   Keep run logs after success
  --hide-diagnostics           Do not print optional watch commands
  -h, --help                   Show this help
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
    --workspace) need_value "$@"; workspace_name="$2"; shift 2 ;;
    --collections) need_value "$@"; collections_csv="$2"; shift 2 ;;
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
  [[ "$numeric" == <-> && "$numeric" -gt 0 ]] || { print -u2 -- "Positive integer expected: $numeric"; exit 2; }
done
for numeric in "$embed_limit" "$embed_max_retries"; do
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

inventory_cmd=(uv run python -u "$SCRIPT_DIR/workspace-inventory.py"
  --workspace "$workspace_name"
  --scan-limit "$scan_limit"
  --progress-every "$progress_every"
  --output "$inventory_path")
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
pending_index_items="${inventory_values[1]}"
local_pdf_items="${inventory_values[2]}"
indexed_chunk_count="${inventory_values[3]}"
chunks_with_embeddings="${inventory_values[4]}"
chunks_missing_embeddings="${inventory_values[5]}"

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
  local embed_cmd=(uv run zot workspace embed "$workspace_name"
    --batch-size "$embed_batch_size"
    --max-retries "$embed_max_retries"
    --retry-sleep "$embed_retry_sleep"
    --heartbeat-seconds "$embed_heartbeat_seconds"
    --progress-lines)
  (( embed_limit > 0 )) && embed_cmd+=(--limit "$embed_limit")
  workflow_section "Embedding Backfill"
  export ZOT_EMBEDDING_BATCH_SIZE="$embed_batch_size"
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
    index_cmd=(uv run zot workspace index "$workspace_name" --progress-lines --item-progress --no-embed)
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
workflow_setting "workspace/index" ".workspace/$workspace_name"
workflow_setting "MinerU parse cache" ".zot/state/mineru"
if [[ "$keep_log" != true && "$keep_inventory" != true ]]; then
  workflow_remove_success_logs "$repo_root" "$run_dir"
else
  print -r -- "Kept run directory: $run_dir"
fi
