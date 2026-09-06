[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$dispatcherPath = Join-Path $projectPath 'dispatcher.py'
$configPath = Join-Path $projectPath 'config.json'
$bridgeRuntimeRevision = 'bridge-resilience-v1'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Virtual environment not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Configuration not found: $configPath"
}

function Test-LoopbackPort {
    param([int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(350)) { return $false }
        $client.EndConnect($pending)
        return $true
    }
    catch { return $false }
    finally { $client.Close() }
}

function Stop-ExactChatGptBridgeListener {
    param([int]$Port,[string]$PythonPath,[string]$ConfigPath)
    $listener = Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $listener) { return }

    $process = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $listener.OwningProcess) -ErrorAction Stop
    $expectedConfig = [IO.Path]::GetFullPath($ConfigPath)
    $actualExe = if ($process.ExecutablePath) { [IO.Path]::GetFullPath([string]$process.ExecutablePath) } else { '' }
    $commandLine = [string]$process.CommandLine
    $actualName = if ($actualExe) { [IO.Path]::GetFileName($actualExe) } else { '' }

    # Legacy manual launches may use relative .venv/config paths and Windows can
    # report the base interpreter in ExecutablePath. Prove identity using the
    # loopback listener plus the exact Python module and its config argument.
    $isPython = $actualName -match '^python(?:[0-9.]+)?\.exe$'
    $hasExactModule = $commandLine.IndexOf('-m factory_dispatcher.chatgpt_extension_bridge', [StringComparison]::OrdinalIgnoreCase) -ge 0
    $hasConfigSwitch = $commandLine.IndexOf('--config', [StringComparison]::OrdinalIgnoreCase) -ge 0
    $hasConfigValue = $commandLine.IndexOf($expectedConfig, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
        $commandLine -match '(?i)--config\s+["'']?\.?[\\/]?config\.json["'']?(?:\s|$)'
    if (-not ($isPython -and $hasExactModule -and $hasConfigSwitch -and $hasConfigValue)) {
        throw 'Refusing to stop a process that cannot be proven to be the FactoryDispatcher extension bridge.'
    }

    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction Stop
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 100
        if (-not (Test-LoopbackPort -Port $Port)) { return }
    }
    throw 'Previous ChatGPT extension bridge did not stop.'
}

function Ensure-ChatGptExtensionBridge {
    param([string]$ProjectPath,[string]$PythonPath,[string]$ConfigPath,[string]$Revision)
    $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if ([string]$config.chatGptBrowserMode -ne 'EXTENSION_BRIDGE' -or
        -not [bool]$config.chatGptExtensionBridgeEnabled) { return }
    $port = [int]$config.chatGptExtensionBridgePort
    if ($port -lt 1 -or $port -gt 65535) { throw 'Invalid ChatGPT extension bridge port.' }

    $revisionPath = Join-Path $ProjectPath 'state\chatgpt-bridge\runtime-revision.txt'
    $currentRevision = ''
    if (Test-Path -LiteralPath $revisionPath -PathType Leaf) {
        $currentRevision = (Get-Content -LiteralPath $revisionPath -Raw).Trim()
    }
    if ((Test-LoopbackPort -Port $port) -and $currentRevision -eq $Revision) { return }
    if (Test-LoopbackPort -Port $port) {
        Stop-ExactChatGptBridgeListener -Port $port -PythonPath $PythonPath -ConfigPath $ConfigPath
    }

    $configArgument = '"' + $ConfigPath + '"'
    Start-Process -FilePath $PythonPath `
        -ArgumentList @('-m','factory_dispatcher.chatgpt_extension_bridge','--config',$configArgument) `
        -WorkingDirectory $ProjectPath `
        -WindowStyle Hidden | Out-Null

    for ($attempt = 0; $attempt -lt 24; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-LoopbackPort -Port $port) {
            $directory = Split-Path -Parent $revisionPath
            if (-not (Test-Path -LiteralPath $directory -PathType Container)) {
                New-Item -ItemType Directory -Path $directory -Force | Out-Null
            }
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [IO.File]::WriteAllText($revisionPath, $Revision, $utf8NoBom)
            return
        }
    }
    throw 'ChatGPT extension bridge did not become available.'
}

function Write-ChatGptBridgeDiagnostic {
    param([string]$ProjectPath)
    try {
        $reservationPath = Join-Path $ProjectPath 'state\chatgpt-bridge\reservation.json'
        if (-not (Test-Path -LiteralPath $reservationPath -PathType Leaf)) {
            [Console]::Error.WriteLine('{"kind":"CHATGPT_BRIDGE_DIAGNOSTIC","reservation":"NONE"}')
            return
        }
        $reservation = Get-Content -LiteralPath $reservationPath -Raw | ConvertFrom-Json
        if ([string]::IsNullOrWhiteSpace([string]$reservation.reservationId)) {
            [Console]::Error.WriteLine('{"kind":"CHATGPT_BRIDGE_DIAGNOSTIC","reservation":"NONE"}')
            return
        }
        $payload = [ordered]@{
            kind = 'CHATGPT_BRIDGE_DIAGNOSTIC'
            reservation = 'ACTIVE'
            reservationId = [string]$reservation.reservationId
            phase = [string]$reservation.phase
        }
        [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
    }
    catch {
        [Console]::Error.WriteLine('{"kind":"CHATGPT_BRIDGE_DIAGNOSTIC","reservation":"UNKNOWN"}')
    }
}

function Write-LatestChatGptWorkerDiagnostic {
    param([string]$ProjectPath)
    try {
        $runsRoot = Join-Path $ProjectPath 'state\chatgpt-runs'
        if (-not (Test-Path -LiteralPath $runsRoot -PathType Container)) {
            [Console]::Error.WriteLine('{"kind":"CHATGPT_WORKER_DIAGNOSTIC","worker":"NONE"}')
            return
        }
        $latest = Get-ChildItem -LiteralPath $runsRoot -Filter 'status.json' -File -Recurse |
            Sort-Object LastWriteTimeUtc -Descending |
            Select-Object -First 1
        if ($null -eq $latest) {
            [Console]::Error.WriteLine('{"kind":"CHATGPT_WORKER_DIAGNOSTIC","worker":"NONE"}')
            return
        }
        $status = Get-Content -LiteralPath $latest.FullName -Raw | ConvertFrom-Json
        $payload = [ordered]@{
            kind = 'CHATGPT_WORKER_DIAGNOSTIC'
            worker = 'LATEST'
            dispatchId = [string]$status.dispatchId
            attemptId = [string]$status.attemptId
            state = [string]$status.state
            errorCode = [string]$status.errorCode
            sendAttempted = [bool]$status.sendAttempted
            needsReconciliation = [bool]$status.needsReconciliation
            statusPath = $latest.FullName.Substring($ProjectPath.Length).TrimStart('\\')
        }
        [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
    }
    catch {
        [Console]::Error.WriteLine('{"kind":"CHATGPT_WORKER_DIAGNOSTIC","worker":"UNKNOWN"}')
    }
}

$dispatcherArguments = @($dispatcherPath, '--tick', '--config', $configPath)
if ($DryRun) {
    $dispatcherArguments += '--dry-run'
}

Push-Location -LiteralPath $projectPath
try {
    Ensure-ChatGptExtensionBridge -ProjectPath $projectPath -PythonPath $pythonPath -ConfigPath $configPath -Revision $bridgeRuntimeRevision
    Write-ChatGptBridgeDiagnostic -ProjectPath $projectPath
    if ($DryRun) {
        Write-LatestChatGptWorkerDiagnostic -ProjectPath $projectPath
    }
    & $pythonPath @dispatcherArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
