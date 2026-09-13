param(
    [Parameter(Mandatory = $true)]
    [string]$Question,
    [string]$WorkspaceName = "rag-workspace",
    [ValidateSet("auto", "bm25", "semantic", "hybrid")]
    [string]$Mode = "auto",
    [ValidateSet("any", "main", "supplementary")]
    [string]$PdfKind = "any",
    [int]$TopK = 8,
    [int]$RerankTopN = 50,
    [string]$OutputDir = "",
    [switch]$Json,
    [switch]$NoRerank,
    [switch]$KeepLog,
    [switch]$RunInCurrentWindow
)

$ErrorActionPreference = "Stop"

$commonScript = Join-Path $PSScriptRoot "zotero-workflow-common.ps1"
. $commonScript

if (-not $RunInCurrentWindow) {
    $windowRoot = Get-ZoteroRepoRootPath -ScriptPath $PSCommandPath
    Start-WorkflowInNewWindow -ScriptPath $PSCommandPath -WorkingDirectory $windowRoot -BoundParameters $PSBoundParameters -DisplayName "RAG Evidence Search"
    return
}

Disable-WorkflowConsoleQuickEdit

function Initialize-RagEvidenceTextDisplay {
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
        # Display setup is best effort only.
    }
}

function Write-RunSection([string]$Title) {
    Write-Host ""
    Write-Host ("[{0}]" -f $Title) -ForegroundColor Cyan
}

function Write-RunSetting([string]$Name, [object]$Value) {
    Write-Host ("  {0,-24} {1}" -f ($Name + ":"), $Value)
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

function New-RunOutputDir([string]$RepoRoot, [string]$RequestedOutputDir) {
    if ($RequestedOutputDir) {
        if ([System.IO.Path]::IsPathRooted($RequestedOutputDir)) {
            return $RequestedOutputDir
        }
        return Join-Path $RepoRoot $RequestedOutputDir
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    return Join-Path $RepoRoot "log\rag-evidence-search-$stamp"
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

Initialize-RagEvidenceTextDisplay

if ([string]::IsNullOrWhiteSpace($Question)) {
    throw "-Question cannot be empty."
}
if ($TopK -le 0) {
    throw "-TopK must be greater than 0."
}
if ($RerankTopN -le 0) {
    throw "-RerankTopN must be greater than 0."
}

$repoRoot = Get-ZoteroRepoRootPath -ScriptPath $PSCommandPath
$runOutputDir = New-RunOutputDir -RepoRoot $repoRoot -RequestedOutputDir $OutputDir
Assert-WorkflowOutputDirSafe -RepoRoot $repoRoot -RunOutputDir $runOutputDir
$logsDir = Join-Path $runOutputDir "logs"
$queryLogPath = Join-Path $logsDir "query.log"

New-Item -ItemType Directory -Force -Path $runOutputDir | Out-Null
Start-WorkflowRunLog -RunDirectory $runOutputDir -WorkflowName "rag-evidence-search" -RepoRoot $repoRoot

Write-RunSection "RAG Evidence Search"
Write-RunSetting "repo" $repoRoot
Write-RunSetting "workspace" $WorkspaceName
Write-RunSetting "question" $Question
Write-RunSetting "mode" $Mode
Write-RunSetting "pdf_kind" $PdfKind
Write-RunSetting "top_k" $TopK
Write-RunSetting "rerank" (-not $NoRerank)
Write-RunSetting "rerank_top_n" $RerankTopN
Write-RunSetting "json" $Json
Write-RunSetting "output" $runOutputDir
Write-RunSetting "keep_log" $KeepLog

$queryCmd = @("uv", "run", "zot")
if ($Json) {
    $queryCmd += "--json"
}
else {
    $queryCmd += "--no-json"
}
$queryCmd += @(
    "workspace", "query", $Question,
    "--workspace", $WorkspaceName,
    "--mode", $Mode,
    "--top-k", "$TopK",
    "--pdf-kind", $PdfKind
)
if (-not $NoRerank) {
    $queryCmd += @("--rerank", "--rerank-top-n", "$RerankTopN")
}

$completed = $false
$failed = $false
try {
    Invoke-LoggedCommand -RepoRoot $repoRoot -LogPath $queryLogPath -Command $queryCmd
    $completed = $true
}
catch {
    $failed = $true
    Write-Error $_
}
finally {
    if ($completed -and -not $KeepLog) {
        Complete-WorkflowRunLog -Status "completed"
        Remove-RunOutputDirWithRetry -RunOutputDir $runOutputDir
        Remove-EmptyLogRoot -RunOutputDir $runOutputDir
    }
    elseif ($completed) {
        Complete-WorkflowRunLog -Status "completed"
        Write-Host "Kept run log directory: $runOutputDir"
    }
    else {
        Complete-WorkflowRunLog -Status "failed"
        Write-Host "Kept failed run log directory: $runOutputDir"
    }
}

if ($failed) {
    exit 1
}
