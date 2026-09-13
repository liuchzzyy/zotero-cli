Set-StrictMode -Version Latest

function Get-ZoteroRepoRootPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath
    )

    $current = Resolve-Path (Split-Path -Parent $ScriptPath)
    while ($true) {
        $candidate = $current.Path
        if ((Test-Path (Join-Path $candidate "pyproject.toml")) -and (Test-Path (Join-Path $candidate "src\zotero_cli"))) {
            return $candidate
        }

        $parent = Split-Path -Parent $candidate
        if (-not $parent -or $parent -eq $candidate) {
            throw "Could not locate Zotero repository root from $ScriptPath."
        }

        $current = Resolve-Path $parent
    }
}

function ConvertTo-WorkflowArgument {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    if ($Value -notmatch '[\s"]') {
        return $Value
    }

    return '"' + $Value.Replace('"', '\"') + '"'
}

function Get-ForwardedWorkflowArguments {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$BoundParameters
    )

    $arguments = [System.Collections.Generic.List[string]]::new()
    $arguments.Add("-RunInCurrentWindow")

    foreach ($item in $BoundParameters.GetEnumerator() | Sort-Object Name) {
        if ($item.Key -eq "RunInCurrentWindow") {
            continue
        }

        if ($item.Value -is [System.Management.Automation.SwitchParameter]) {
            if ($item.Value.IsPresent) {
                $arguments.Add("-$($item.Key)")
            }
            continue
        }

        if ($null -ne $item.Value -and "$($item.Value)" -ne "") {
            if ($item.Value -is [array]) {
                $arguments.Add("-$($item.Key)")
                $values = @($item.Value) | ForEach-Object {
                    ConvertTo-WorkflowArgument -Value ([string]$_)
                }
                $arguments.Add(($values -join ","))
                continue
            }

            $arguments.Add("-$($item.Key)")
            $arguments.Add([string]$item.Value)
        }
    }

    return @($arguments)
}

function Get-WorkflowPowerShellPath {
    if ($env:ZOT_WORKFLOW_POWERSHELL) {
        if (Test-Path -LiteralPath $env:ZOT_WORKFLOW_POWERSHELL) {
            return (Resolve-Path -LiteralPath $env:ZOT_WORKFLOW_POWERSHELL).Path
        }
        return $env:ZOT_WORKFLOW_POWERSHELL
    }

    $candidates = [System.Collections.Generic.List[string]]::new()
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles "PowerShell\7\pwsh.exe"))
    }
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA "Microsoft\powershell\7\pwsh.exe"))
    }

    $pwshCommand = Get-Command "pwsh.exe" -ErrorAction SilentlyContinue
    if ($pwshCommand) {
        $candidates.Add($pwshCommand.Source)
    }

    foreach ($candidate in @($candidates | Select-Object -Unique)) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return "powershell.exe"
}

function Start-WorkflowInNewWindow {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [hashtable]$BoundParameters,
        [Parameter(Mandatory = $true)]
        [string]$DisplayName
    )

    $forwardedArguments = Get-ForwardedWorkflowArguments -BoundParameters $BoundParameters
    $powerShellExe = Get-WorkflowPowerShellPath
    $processArguments = [System.Collections.Generic.List[string]]::new()
    $processArguments.Add("-NoLogo")
    $processArguments.Add("-NoProfile")
    $processArguments.Add("-ExecutionPolicy")
    $processArguments.Add("Bypass")
    $processArguments.Add("-NoExit")
    $processArguments.Add("-File")
    $processArguments.Add((ConvertTo-WorkflowArgument -Value $ScriptPath))
    foreach ($argument in $forwardedArguments) {
        $processArguments.Add((ConvertTo-WorkflowArgument -Value $argument))
    }

    $process = Start-Process `
        -FilePath $powerShellExe `
        -ArgumentList @($processArguments) `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Normal `
        -PassThru

    Write-Host ("Started {0} in a new PowerShell window. PID: {1}" -f $DisplayName, $process.Id) -ForegroundColor Cyan
    Write-Host ("PowerShell host: {0}" -f $powerShellExe) -ForegroundColor DarkGray
    Write-Host "Close the new window only after the workflow finishes." -ForegroundColor DarkGray
}

function Assert-WorkflowOutputDirSafe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$RunOutputDir
    )

    $resolvedRepoRoot = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\', '/')
    $resolvedRunDir = [System.IO.Path]::GetFullPath($RunOutputDir).TrimEnd('\', '/')
    $resolvedLogRoot = [System.IO.Path]::GetFullPath((Join-Path $resolvedRepoRoot "log")).TrimEnd('\', '/')
    $comparison = [System.StringComparison]::OrdinalIgnoreCase

    if ($resolvedRunDir -eq $resolvedRepoRoot -or $resolvedRunDir -eq $resolvedLogRoot) {
        throw "Refusing unsafe workflow output directory: $resolvedRunDir"
    }

    if (-not $resolvedRunDir.StartsWith($resolvedLogRoot + [System.IO.Path]::DirectorySeparatorChar, $comparison)) {
        throw "Workflow output directory must be under '$resolvedLogRoot': $resolvedRunDir"
    }
}

function Disable-WorkflowConsoleQuickEdit {
    try {
        if (-not ([System.Management.Automation.PSTypeName]'WorkflowConsoleModeNative').Type) {
            Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class WorkflowConsoleModeNative {
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern IntPtr GetStdHandle(int nStdHandle);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out int lpMode);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetConsoleMode(IntPtr hConsoleHandle, int dwMode);
}
"@
        }

        $stdInputHandle = [WorkflowConsoleModeNative]::GetStdHandle(-10)
        if ($stdInputHandle -eq [IntPtr]::Zero -or $stdInputHandle.ToInt64() -eq -1) {
            return
        }

        [int]$mode = 0
        if (-not [WorkflowConsoleModeNative]::GetConsoleMode($stdInputHandle, [ref]$mode)) {
            return
        }

        $enableQuickEditMode = 0x0040
        $enableExtendedFlags = 0x0080
        $newMode = ($mode -bor $enableExtendedFlags) -band (-bnot $enableQuickEditMode)
        [void][WorkflowConsoleModeNative]::SetConsoleMode($stdInputHandle, $newMode)
    }
    catch {
        # Best effort only; console mode is not available in every host.
    }
}

function New-SharedUtf8AppendWriter {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $directory = Split-Path -Parent $Path
    if ($directory) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }

    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write, [System.IO.FileShare]::ReadWrite)
    $writer = [System.IO.StreamWriter]::new($stream, [System.Text.UTF8Encoding]::new($false))
    $writer.AutoFlush = $true
    return $writer
}

function Write-SharedFileLine {
    param(
        [System.IO.StreamWriter]$Writer,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $attempt = 0
    while ($true) {
        try {
            if ($Writer) {
                $Writer.WriteLine($Message)
            } else {
                Add-Content -LiteralPath $Path -Encoding UTF8 -Value $Message
            }
            return
        } catch [System.IO.IOException] {
            if ($attempt -ge 4) {
                throw
            }
            Start-Sleep -Milliseconds (100 * [math]::Pow(2, $attempt))
            $attempt += 1
        }
    }
}

function Start-WorkflowRunLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RunDirectory,
        [Parameter(Mandatory = $true)]
        [string]$WorkflowName,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    New-Item -ItemType Directory -Force -Path $RunDirectory | Out-Null
    $script:WorkflowRunLog = Join-Path $RunDirectory "run.log"
    $script:WorkflowProgressJsonl = Join-Path $RunDirectory "progress.jsonl"
    $script:WorkflowRunWriter = New-SharedUtf8AppendWriter -Path $script:WorkflowRunLog
    $script:WorkflowProgressWriter = New-SharedUtf8AppendWriter -Path $script:WorkflowProgressJsonl
    $script:WorkflowStartedAt = Get-Date
    $script:WorkflowName = $WorkflowName

    Write-SharedFileLine -Writer $script:WorkflowRunWriter -Path $script:WorkflowRunLog -Message ("[{0}] {1} started" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $WorkflowName)
    Write-WorkflowEvent -EventData ([ordered]@{
        event = "run_started"
        workflow = $WorkflowName
        repo_root = $RepoRoot
        run_dir = $RunDirectory
    })

    Write-WorkflowLine -Message ("Run mode: new-window workflow wrapper with built-in logs") -Color Cyan
    Write-WorkflowLine -Message ("Run logs: {0}" -f $RunDirectory) -Color DarkGray
    Write-WorkflowLine -Message ("Summary log: {0}" -f $script:WorkflowRunLog) -Color DarkGray
    Write-WorkflowLine -Message ("Progress JSONL: {0}" -f $script:WorkflowProgressJsonl) -Color DarkGray
}

function Write-WorkflowLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,
        [ConsoleColor]$Color = [ConsoleColor]::White
    )

    Write-Host $Message -ForegroundColor $Color
    if ($script:WorkflowRunLog) {
        Write-SharedFileLine -Writer $script:WorkflowRunWriter -Path $script:WorkflowRunLog -Message ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
    }
}

function Write-WorkflowEvent {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$EventData
    )

    if (-not $script:WorkflowProgressJsonl) {
        return
    }

    $payload = [ordered]@{
        timestamp = (Get-Date).ToString("o")
    }
    foreach ($key in $EventData.Keys) {
        $payload[$key] = $EventData[$key]
    }
    Write-SharedFileLine -Writer $script:WorkflowProgressWriter -Path $script:WorkflowProgressJsonl -Message ($payload | ConvertTo-Json -Compress -Depth 8)
}

function Complete-WorkflowRunLog {
    param(
        [string]$Status = "completed"
    )

    try {
        $finishedAt = Get-Date
        $elapsedSeconds = if ($script:WorkflowStartedAt) { [int](($finishedAt - $script:WorkflowStartedAt).TotalSeconds) } else { 0 }
        Write-WorkflowEvent -EventData ([ordered]@{
            event = "run_finished"
            workflow = $script:WorkflowName
            status = $Status
            elapsed_seconds = $elapsedSeconds
        })
        Write-WorkflowLine -Message ("Finished with status={0}; elapsed={1}" -f $Status, ([TimeSpan]::FromSeconds($elapsedSeconds).ToString("hh\:mm\:ss"))) -Color Green
    }
    finally {
        foreach ($writerName in @("WorkflowRunWriter", "WorkflowProgressWriter")) {
            $writerVar = Get-Variable -Scope Script -Name $writerName -ErrorAction SilentlyContinue
            if ($writerVar -and $writerVar.Value) {
                $writerVar.Value.Dispose()
                Set-Variable -Scope Script -Name $writerName -Value $null
            }
        }
    }
}
