param(
    [string]$WorkspaceName = "rag-workspace",
    [string]$Collections = "30_PROJECT,40_TOPIC",
    [string]$Extractor = "mineru",
    [int]$ScanLimit = 100000,
    [int]$ProgressEvery = 100,
    [int]$EmbedBatchSize = 10,
    [int]$EmbedLimit = 0,
    [int]$EmbedMaxRetries = 8,
    [double]$EmbedRetrySleep = 10.0,
    [double]$EmbedHeartbeatSeconds = 15.0,
    [string]$OutputDir = "",
    [switch]$DryRun,
    [switch]$NoIndex,
    [switch]$NoEmbed,
    [switch]$EmbedOnly,
    [switch]$ForceRebuild,
    [switch]$KeepInventory,
    [switch]$StopOnError,
    [switch]$KeepLog,
    [switch]$HideProgressWatchCommands,
    [switch]$RunInCurrentWindow
)

$ErrorActionPreference = "Stop"

$commonScript = Join-Path $PSScriptRoot "zotero-workflow-common.ps1"
. $commonScript

if (-not $RunInCurrentWindow) {
    $windowRoot = Get-ZoteroRepoRootPath -ScriptPath $PSCommandPath
    Start-WorkflowInNewWindow -ScriptPath $PSCommandPath -WorkingDirectory $windowRoot -BoundParameters $PSBoundParameters -DisplayName "Workspace RAG Index"
    return
}

Disable-WorkflowConsoleQuickEdit

function Initialize-RagTextDisplay {
    try {
        $utf8 = [System.Text.UTF8Encoding]::new($false)
        [Console]::InputEncoding = $utf8
        [Console]::OutputEncoding = $utf8
        $script:OutputEncoding = $utf8
        if ($PSVersionTable.PSVersion.Major -ge 7) {
            $PSStyle.OutputRendering = "PlainText"
        }
    }
    catch {
        # Display setup is best effort only; the workflow can still run without it.
    }
}

function Write-RunSection([string]$Title) {
    Write-Host ""
    Write-Host ("[{0}]" -f $Title) -ForegroundColor Cyan
}

function Write-RunSetting([string]$Name, [object]$Value) {
    Write-Host ("  {0,-30} {1}" -f ($Name + ":"), $Value)
}

function Format-RunNumber([object]$Value) {
    try {
        return ("{0:N0}" -f [int64]$Value)
    }
    catch {
        return "$Value"
    }
}

function Format-RunCommand([string[]]$Command) {
    $parts = foreach ($part in $Command) {
        if ($part -match '[\s"]') {
            '"' + $part.Replace('"', '\"') + '"'
        }
        else {
            $part
        }
    }
    return ($parts -join " ")
}

function Write-LoggedOutputLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LogPath,
        [Parameter(Mandatory = $true)]
        [object]$Line
    )

    $text = ([string]$Line).Replace("`r", "").TrimEnd()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return
    }

    Write-Host $text
    Add-Content -LiteralPath $LogPath -Encoding UTF8 -Value $text
}

Initialize-RagTextDisplay

if ($EmbedOnly -and $NoEmbed) {
    throw "-EmbedOnly and -NoEmbed cannot be used together."
}
if ($EmbedBatchSize -le 0) {
    throw "-EmbedBatchSize must be greater than 0."
}
if ($EmbedLimit -lt 0) {
    throw "-EmbedLimit must be 0 or greater."
}
if ($EmbedMaxRetries -lt 0) {
    throw "-EmbedMaxRetries must be 0 or greater."
}
if ($EmbedRetrySleep -lt 0) {
    throw "-EmbedRetrySleep must be 0 or greater."
}
if ($EmbedHeartbeatSeconds -lt 0) {
    throw "-EmbedHeartbeatSeconds must be 0 or greater."
}

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function New-RunOutputDir([string]$RepoRoot, [string]$RequestedOutputDir) {
    if ($RequestedOutputDir) {
        if ([System.IO.Path]::IsPathRooted($RequestedOutputDir)) {
            return $RequestedOutputDir
        }
        return Join-Path $RepoRoot $RequestedOutputDir
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    return Join-Path $RepoRoot "log\rag-workspace-$stamp"
}

function Remove-EmptyLogRoot([string]$RunOutputDir) {
    $parent = Split-Path -Parent $RunOutputDir
    if ((Split-Path -Leaf $parent) -ne "log") {
        return
    }
    if (-not (Test-Path -LiteralPath $parent)) {
        return
    }
    $children = @(Get-ChildItem -LiteralPath $parent -Force -ErrorAction SilentlyContinue)
    if ($children.Count -eq 0) {
        Remove-Item -LiteralPath $parent -Force
        Write-Host "Removed empty log directory: $parent"
    }
}

function Remove-RunOutputDirWithRetry([string]$RunOutputDir) {
    for ($attempt = 0; $attempt -lt 5; $attempt += 1) {
        try {
            Remove-Item -LiteralPath $RunOutputDir -Recurse -Force
            Write-Host "Removed run log directory: $RunOutputDir"
            return
        }
        catch [System.IO.IOException] {
            if ($attempt -eq 4) {
                throw
            }
            Start-Sleep -Milliseconds (250 * [math]::Pow(2, $attempt))
        }
    }
}

function Write-ProgressWatchCommands([string]$RunOutputDir) {
    $inventoryPath = Join-Path $RunOutputDir "inventory.json"
    $logsDir = Join-Path $RunOutputDir "logs"
    Write-RunSection "Optional Diagnostics"
    Write-Host "  Primary progress is this window plus run.log/progress.jsonl."
    Write-Host "  Use these checks only when diagnosing a stalled run."
    Write-Host "  Processes:"
    Write-Host '    Get-CimInstance Win32_Process | ? CommandLine -match "run-rag-workspace|workspace index|workspace embed|mineru|inventory_full_pdf_workspace" | select ProcessId,Name,CommandLine'
    Write-Host "  Latest child logs:"
    Write-Host ("    Get-ChildItem -LiteralPath '{0}' -File | sort LastWriteTime -desc | select -first 5 FullName,Length,LastWriteTime" -f $logsDir)
    Write-Host "  Embedding tail:"
    Write-Host ("    Get-Content -Tail 20 -LiteralPath '{0}'" -f (Join-Path $logsDir "embed.log"))
    Write-Host "  Inventory JSON:"
    Write-Host ("    Get-Content -Raw -LiteralPath '{0}'" -f $inventoryPath)
}

function Invoke-LoggedCommand {
    param(
        [string]$RepoRoot,
        [string]$LogPath,
        [string[]]$Command
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogPath) | Out-Null

    $exe = $Command[0]
    $cmdArgs = if ($Command.Count -gt 1) { $Command[1..($Command.Count - 1)] } else { @() }

    Write-RunSection "Command"
    Write-RunSetting "command" (Format-RunCommand -Command $Command)
    Write-RunSetting "log" $LogPath

    Push-Location $RepoRoot
    try {
        & $exe @cmdArgs 2>&1 | ForEach-Object {
            Write-LoggedOutputLine -LogPath $LogPath -Line $_
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    if ($exitCode -ne 0) {
        throw "Command failed with exit code $exitCode. See $LogPath"
    }
}

function Get-JsonInt64 {
    param(
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object.PSObject.Properties[$Name]) {
        return [int64]0
    }
    if ($null -eq $Object.$Name) {
        return [int64]0
    }
    return [int64]$Object.$Name
}

function Get-TomlSectionValue {
    param(
        [string]$Path,
        [string]$Section,
        [string]$Name
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }

    $inSection = $false
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\[(.+)\]$') {
            $inSection = ($Matches[1] -eq $Section)
            continue
        }
        if (-not $inSection) {
            continue
        }
        if ($trimmed -match ("^{0}\s*=\s*(.+)$" -f [regex]::Escape($Name))) {
            $value = $Matches[1].Trim()
            $commentIndex = $value.IndexOf("#")
            if ($commentIndex -ge 0) {
                $value = $value.Substring(0, $commentIndex).Trim()
            }
            return $value.Trim('"').Trim("'")
        }
    }

    return ""
}

function Get-EffectiveEmbeddingSetting {
    param(
        [string]$RepoRoot,
        [string]$Name
    )

    if (($Name -eq "model") -and $env:ZOT_EMBEDDING_MODEL) {
        return $env:ZOT_EMBEDDING_MODEL
    }
    if (($Name -eq "provider") -and $env:ZOT_EMBEDDING_PROVIDER) {
        return $env:ZOT_EMBEDDING_PROVIDER
    }

    Push-Location $RepoRoot
    try {
        $pythonCode = "from zotero_cli.config import load_embedding_config; cfg = load_embedding_config(apply_env_overrides=True); print(getattr(cfg, '$Name', '') or '')"
        $pythonValue = & uv run python -c $pythonCode 2>$null
        if (($LASTEXITCODE -eq 0) -and $pythonValue) {
            return ([string]$pythonValue).Trim()
        }
    }
    finally {
        Pop-Location
    }

    $configPath = Join-Path $RepoRoot ".zot\config.toml"
    return Get-TomlSectionValue -Path $configPath -Section "embedding" -Name $Name
}

function Invoke-RagEmbeddingBackfill {
    param(
        [string]$RepoRoot,
        [string]$LogPath,
        [string]$WorkspaceName,
        [int]$BatchSize,
        [int]$Limit,
        [int]$MaxRetries,
        [double]$RetrySleep,
        [double]$HeartbeatSeconds
    )

    $effectiveProvider = Get-EffectiveEmbeddingSetting -RepoRoot $RepoRoot -Name "provider"
    $effectiveModel = Get-EffectiveEmbeddingSetting -RepoRoot $RepoRoot -Name "model"

    $embedCmd = @(
        "uv", "run", "zot", "workspace", "embed", $WorkspaceName,
        "--batch-size", "$BatchSize",
        "--max-retries", "$MaxRetries",
        "--retry-sleep", "$RetrySleep",
        "--heartbeat-seconds", "$HeartbeatSeconds",
        "--progress-lines"
    )
    if ($Limit -gt 0) {
        $embedCmd += @("--limit", "$Limit")
    }

    Write-RunSection "Embedding Backfill"
    Write-RunSetting "progress log" $LogPath
    Write-RunSetting "embedding provider" $effectiveProvider
    Write-RunSetting "embedding model" $effectiveModel
    Write-RunSetting "embed batch size" $BatchSize
    Write-RunSetting "provider batch size override" $BatchSize
    Write-RunSetting "heartbeat seconds" $HeartbeatSeconds
    $previousProviderBatchSize = [Environment]::GetEnvironmentVariable("ZOT_EMBEDDING_BATCH_SIZE", "Process")
    [Environment]::SetEnvironmentVariable("ZOT_EMBEDDING_BATCH_SIZE", "$BatchSize", "Process")
    try {
        Invoke-LoggedCommand -RepoRoot $RepoRoot -LogPath $LogPath -Command $embedCmd
    }
    finally {
        [Environment]::SetEnvironmentVariable("ZOT_EMBEDDING_BATCH_SIZE", $previousProviderBatchSize, "Process")
    }
}

function Write-InventoryScript([string]$ScriptPath) {
    @'
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from zotero_cli.config import get_data_dir, get_prefs_js_path, load_config, load_vector_store_config, resolve_library_id
from zotero_cli.core.reader import ZoteroReader
from zotero_cli.core.rag_index import RagIndex
from zotero_cli.core.semantic_search import QdrantVectorStore, resolve_vector_store_path
from zotero_cli.core.workspace import Workspace, load_workspace, save_workspace, workspace_exists, workspace_index_path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scan-limit", type=int, required=True)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collection", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_config()
    data_dir = get_data_dir(cfg)
    db_path = data_dir / "zotero.sqlite"
    library_ctx = {"library_type": "user", "group_id": None}
    library_id = resolve_library_id(db_path, library_ctx)
    reader = ZoteroReader(db_path, library_id=library_id, prefs_js_path=get_prefs_js_path(cfg))

    try:
        print(f"[inventory] reading local Zotero DB {db_path}", flush=True)
        collection_item_counts: dict[str, int] = {}
        if args.collection:
            item_by_key = {}
            for collection in args.collection:
                print(f"[inventory] scanning collection {collection}", flush=True)
                result = reader.search("", collection=collection, limit=args.scan_limit)
                collection_item_counts[collection] = result.total
                for item in result.items:
                    item_by_key.setdefault(item.key, item)
            items = list(item_by_key.values())
            print(
                "[inventory] "
                f"collection_scope={','.join(args.collection)} unique_items={len(items)}",
                flush=True,
            )
        else:
            items = reader.search("", limit=args.scan_limit).items
        total = len(items)
        pdf_items: list[dict[str, object]] = []
        skipped_no_local_pdf = 0

        for idx, item in enumerate(items, 1):
            pdfs = reader.get_pdf_attachments(item.key)
            local_pdfs = [
                {
                    "key": att.key,
                    "filename": att.filename,
                    "path": str(att.path) if att.path else "",
                }
                for att in pdfs
                if att.path is not None and att.path.exists()
            ]
            if local_pdfs:
                pdf_items.append(
                    {
                        "key": item.key,
                        "title": item.title,
                        "item_type": item.item_type,
                        "pdf_count": len(local_pdfs),
                        "pdfs": local_pdfs,
                    }
                )
            elif pdfs:
                skipped_no_local_pdf += 1

            if idx % args.progress_every == 0 or idx == total:
                print(
                    "[inventory] "
                    f"scanned={idx}/{total} local_pdf_items={len(pdf_items)} "
                    f"pdf_but_missing={skipped_no_local_pdf}",
                    flush=True,
                )

        existing_keys: set[str] = set()
        indexed_keys: set[str] = set()
        indexed_chunk_count = 0
        chunks_with_embeddings = 0
        chunks_missing_embeddings = 0
        embedding_meta: dict[str, str] = {}
        added = 0
        workspace_created = False

        if workspace_exists(args.workspace):
            ws = load_workspace(args.workspace)
            existing_keys = {entry.key for entry in ws.items}
        else:
            ws = Workspace(
                name=args.workspace,
                created=utc_now(),
                description=(
                    "Auto-maintained workspace containing selected Zotero collection parent items with existing PDF attachments."
                    if args.collection
                    else "Auto-maintained workspace containing all local Zotero parent items with existing PDF attachments."
                ),
            )
            workspace_created = True

        for row in pdf_items:
            key = str(row["key"])
            if key not in existing_keys:
                existing_keys.add(key)
                added += 1
                if not args.dry_run:
                    ws.add_item(key, str(row.get("title") or ""))

        if not args.dry_run:
            save_workspace(ws)

        idx_path = workspace_index_path(args.workspace)
        if idx_path.exists():
            idx = RagIndex(idx_path)
            try:
                indexed_keys = idx.get_indexed_keys()
                indexed_chunk_count = len(idx.get_all_chunks())
            finally:
                idx.close()

            vector_store_cfg = load_vector_store_config()
            vector_store = QdrantVectorStore(resolve_vector_store_path(vector_store_cfg), f"ws_{args.workspace}")
            try:
                chunks_with_embeddings = vector_store.count()
            finally:
                vector_store.close()
            chunks_missing_embeddings = max(indexed_chunk_count - chunks_with_embeddings, 0)
            embedding_meta = {
                "provider": vector_store_cfg.provider,
                "collection": f"ws_{args.workspace}",
            }

        pdf_keys = {str(row["key"]) for row in pdf_items}
        pending_index = sorted(pdf_keys - indexed_keys)
        payload = {
            "created_at": utc_now(),
            "workspace": args.workspace,
            "collections": args.collection,
            "collection_item_counts": collection_item_counts,
            "dry_run": args.dry_run,
            "db_path": str(db_path),
            "scanned_items": total,
            "local_pdf_items": len(pdf_items),
            "pdf_but_missing_local_file": skipped_no_local_pdf,
            "workspace_created": workspace_created,
            "workspace_existing_items": len(existing_keys) - added,
            "workspace_added_items": added,
            "indexed_items": len(indexed_keys),
            "indexed_chunk_count": indexed_chunk_count,
            "chunks_with_embeddings": chunks_with_embeddings,
            "chunks_missing_embeddings": chunks_missing_embeddings,
            "embedding_meta": embedding_meta,
            "pending_index_items": len(pending_index),
            "pending_index_keys": pending_index,
            "items": pdf_items,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            "[inventory-summary] "
            f"local_pdf_items={len(pdf_items)} added_to_workspace={added} "
            f"indexed={len(indexed_keys)} pending_index={len(pending_index)} "
            f"chunks={indexed_chunk_count} embedding_missing={chunks_missing_embeddings} "
            f"output={args.output}",
            flush=True,
        )
    finally:
        reader.close()


if __name__ == "__main__":
    main()
'@ | Set-Content -LiteralPath $ScriptPath -Encoding UTF8
}

$repoRoot = Get-RepoRoot
$runOutputDir = New-RunOutputDir -RepoRoot $repoRoot -RequestedOutputDir $OutputDir
Assert-WorkflowOutputDirSafe -RepoRoot $repoRoot -RunOutputDir $runOutputDir
$logsDir = Join-Path $runOutputDir "logs"
$inventoryPath = Join-Path $runOutputDir "inventory.json"
$inventoryScript = Join-Path $runOutputDir "inventory_full_pdf_workspace.py"
$collectionNames = @()
if ($Collections) {
    $collectionNames = @(
        $Collections -split "," |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
}

New-Item -ItemType Directory -Force -Path $runOutputDir | Out-Null
Write-InventoryScript -ScriptPath $inventoryScript
Start-WorkflowRunLog -RunDirectory $runOutputDir -WorkflowName "rag-workspace" -RepoRoot $repoRoot

Write-RunSection "Workspace RAG Incremental Index"
Write-RunSetting "repo" $repoRoot
Write-RunSetting "workspace" $WorkspaceName
if ($collectionNames.Count -gt 0) {
    Write-RunSetting "collections" ($collectionNames -join ", ")
}
Write-RunSetting "extractor" $Extractor
Write-RunSetting "output" $runOutputDir
Write-RunSetting "dry_run" $DryRun
Write-RunSetting "no_index" $NoIndex
Write-RunSetting "no_embed" $NoEmbed
Write-RunSetting "index_stage_embed" "disabled; workspace embed runs after all indexing"
Write-RunSetting "embed_only" $EmbedOnly
Write-RunSetting "force_rebuild" $ForceRebuild
Write-RunSetting "keep_log" $KeepLog
Write-RunSetting "embed_batch_size" $EmbedBatchSize
Write-RunSetting "embed_heartbeat_seconds" $EmbedHeartbeatSeconds
if (-not $HideProgressWatchCommands) {
    Write-ProgressWatchCommands -RunOutputDir $runOutputDir
}

$inventoryCmd = @(
    "uv", "run", "python", "-u", $inventoryScript,
    "--workspace", $WorkspaceName,
    "--scan-limit", "$ScanLimit",
    "--progress-every", "$ProgressEvery",
    "--output", $inventoryPath
)
if ($DryRun) {
    $inventoryCmd += "--dry-run"
}
foreach ($collectionName in $collectionNames) {
    $inventoryCmd += @("--collection", $collectionName)
}

$completed = $false
try {
    Invoke-LoggedCommand -RepoRoot $repoRoot -LogPath (Join-Path $logsDir "inventory.log") -Command $inventoryCmd

    $inventory = Get-Content -LiteralPath $inventoryPath -Raw | ConvertFrom-Json
    $pendingIndexItems = Get-JsonInt64 -Object $inventory -Name "pending_index_items"
    $localPdfItems = Get-JsonInt64 -Object $inventory -Name "local_pdf_items"
    $indexedChunkCount = Get-JsonInt64 -Object $inventory -Name "indexed_chunk_count"
    $chunksWithEmbeddings = Get-JsonInt64 -Object $inventory -Name "chunks_with_embeddings"
    $chunksMissingEmbeddings = Get-JsonInt64 -Object $inventory -Name "chunks_missing_embeddings"

    Write-RunSection "Inventory Summary"
    $inventoryCollections = @($inventory.collections)
    if ($inventoryCollections.Count -gt 0) {
        Write-RunSetting "collections" ($inventoryCollections -join ", ")
    }
    Write-RunSetting "scanned_items" (Format-RunNumber $inventory.scanned_items)
    Write-RunSetting "local_pdf_items" (Format-RunNumber $inventory.local_pdf_items)
    Write-RunSetting "pdf_but_missing_local_file" (Format-RunNumber $inventory.pdf_but_missing_local_file)
    Write-RunSetting "workspace_added_items" (Format-RunNumber $inventory.workspace_added_items)
    Write-RunSetting "indexed_items" (Format-RunNumber $inventory.indexed_items)
    Write-RunSetting "pending_index_items" (Format-RunNumber $pendingIndexItems)
    Write-RunSetting "indexed_chunk_count" (Format-RunNumber $indexedChunkCount)
    Write-RunSetting "chunks_with_embeddings" (Format-RunNumber $chunksWithEmbeddings)
    Write-RunSetting "chunks_missing_embeddings" (Format-RunNumber $chunksMissingEmbeddings)

    if ($DryRun) {
        Write-Host "Dry-run complete. No workspace, RAG index, or embedding changes were made."
        $completed = $true
        return
    }

    $embedLogPath = Join-Path $logsDir "embed.log"
    $hasExistingMissingEmbeddings = ($chunksMissingEmbeddings -gt 0)

    if ($EmbedOnly) {
        if ($hasExistingMissingEmbeddings) {
            Invoke-RagEmbeddingBackfill `
                -RepoRoot $repoRoot `
                -LogPath $embedLogPath `
                -WorkspaceName $WorkspaceName `
                -BatchSize $EmbedBatchSize `
                -Limit $EmbedLimit `
                -MaxRetries $EmbedMaxRetries `
                -RetrySleep $EmbedRetrySleep `
                -HeartbeatSeconds $EmbedHeartbeatSeconds
        }
        else {
            Write-Host "No missing embeddings found for workspace '$WorkspaceName'."
        }
        $completed = $true
        return
    }

    if ($NoIndex) {
        Write-Host "Workspace inventory updated. Skipped RAG indexing and embedding backfill because -NoIndex was set."
        $completed = $true
        return
    }

    if ($hasExistingMissingEmbeddings -and (-not $ForceRebuild)) {
        Write-Host (
            "Existing RAG index has {0} chunks without embeddings; backfill will run after item indexing." -f
            (Format-RunNumber $chunksMissingEmbeddings)
        )
    }
    elseif ($hasExistingMissingEmbeddings -and $ForceRebuild) {
        Write-Host "Existing RAG index has missing embeddings, but -ForceRebuild was set; old chunks will be rebuilt before embedding."
    }
    elseif ($indexedChunkCount -gt 0) {
        Write-Host "Existing RAG index has no missing embeddings before item indexing."
    }

    if (($localPdfItems -eq 0) -and (-not $ForceRebuild)) {
        Write-Host "No local PDF items found. Nothing to index."
        $completed = $true
        return
    }

    $ranIndex = $false
    if (($pendingIndexItems -eq 0) -and (-not $ForceRebuild)) {
        Write-Host "RAG index is already up to date for workspace '$WorkspaceName'."
    }
    else {
        $indexCmd = @("uv", "run", "zot", "workspace", "index", $WorkspaceName, "--extractor", $Extractor, "--progress-lines", "--item-progress", "--no-embed")
        if ($ForceRebuild) {
            $indexCmd += "--force"
        }

        Write-RunSection "Item Index"
        Write-Host "  Starting RAG index with embeddings disabled. This may take a long time for MinerU extraction."
        Write-RunSetting "progress log" (Join-Path $logsDir "index.log")
        Invoke-LoggedCommand -RepoRoot $repoRoot -LogPath (Join-Path $logsDir "index.log") -Command $indexCmd
        $ranIndex = $true
    }

    if ($NoEmbed) {
        Write-Host "Skipped embedding backfill because -NoEmbed was set."
    }
    elseif ($ranIndex -or ($hasExistingMissingEmbeddings -and (-not $ForceRebuild))) {
        Write-RunSection "Embedding Backfill"
        if ($ranIndex) {
            Write-Host "  Backfilling embeddings after all pending item indexing completed."
        }
        else {
            Write-Host "  RAG index was already up to date; backfilling existing missing embeddings."
        }
        Invoke-RagEmbeddingBackfill `
            -RepoRoot $repoRoot `
            -LogPath $embedLogPath `
            -WorkspaceName $WorkspaceName `
            -BatchSize $EmbedBatchSize `
            -Limit $EmbedLimit `
            -MaxRetries $EmbedMaxRetries `
            -RetrySleep $EmbedRetrySleep `
            -HeartbeatSeconds $EmbedHeartbeatSeconds
    }
    else {
        Write-Host "RAG embeddings are already complete for workspace '$WorkspaceName'."
    }

    $completed = $true
}
catch {
    Write-Error $_
    if ($StopOnError) {
        throw
    }
    exit 1
}
finally {
    if (-not $KeepInventory) {
        Remove-Item -LiteralPath $inventoryScript -Force -ErrorAction SilentlyContinue
    }
    if ($completed -and -not $KeepLog -and -not $KeepInventory) {
        Complete-WorkflowRunLog -Status "completed"
        Remove-RunOutputDirWithRetry -RunOutputDir $runOutputDir
        Remove-EmptyLogRoot -RunOutputDir $runOutputDir
    }
    elseif ($completed) {
        Complete-WorkflowRunLog -Status "completed"
    }
}

Write-RunSection "Complete"
Write-RunSetting "workspace/index" ".workspace\$WorkspaceName"
Write-RunSetting "pdf cache" ".zot\state\pdf_cache.sqlite"
