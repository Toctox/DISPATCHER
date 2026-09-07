[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$dispatcherPath = Join-Path $projectPath 'dispatcher.py'
$configPath = Join-Path $projectPath 'config.json'
$bridgeRuntimeRevision = 'bridge-resilience-v2'

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

    $isPython = $actualName -match '^python(?:[0-9.]+)?\.exe$'
    $hasExactModule = $commandLine -match '(?i)-m\s+factory_dispatcher\.chatgpt_extension_bridge(?:_runtime)?(?:\s|$)'
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
        -ArgumentList @('-m','factory_dispatcher.chatgpt_extension_bridge_runtime','--config',$configArgument) `
        -WorkingDirectory $projectPath `
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

function Get-ChatGptBridgeReservation {
    param([string]$ProjectPath)
    $reservationPath = Join-Path $ProjectPath 'state\chatgpt-bridge\reservation.json'
    if (-not (Test-Path -LiteralPath $reservationPath -PathType Leaf)) { return $null }
    $reservation = Get-Content -LiteralPath $reservationPath -Raw | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace([string]$reservation.reservationId)) { return $null }
    return $reservation
}

function Get-LatestChatGptWorkerStatus {
    param([string]$ProjectPath)
    $runsRoot = Join-Path $ProjectPath 'state\chatgpt-runs'
    if (-not (Test-Path -LiteralPath $runsRoot -PathType Container)) { return $null }
    $latest = Get-ChildItem -LiteralPath $runsRoot -Filter 'status.json' -File -Recurse |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($null -eq $latest) { return $null }
    return Get-Content -LiteralPath $latest.FullName -Raw | ConvertFrom-Json
}

function Write-ChatGptBridgeDiagnostic {
    param([string]$ProjectPath)
    try {
        $reservation = Get-ChatGptBridgeReservation -ProjectPath $ProjectPath
        if ($null -eq $reservation) {
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
        $status = Get-LatestChatGptWorkerStatus -ProjectPath $ProjectPath
        if ($null -eq $status) {
            [Console]::Error.WriteLine('{"kind":"CHATGPT_WORKER_DIAGNOSTIC","worker":"NONE"}')
            return
        }
        $payload = [ordered]@{
            kind = 'CHATGPT_WORKER_DIAGNOSTIC'
            worker = 'LATEST'
            dispatchId = [string]$status.dispatchId
            attemptId = [string]$status.attemptId
            state = [string]$status.state
            errorCode = [string]$status.errorCode
            sendAttempted = [bool]$status.sendAttempted
            needsReconciliation = [bool]$status.needsReconciliation
        }
        [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
    }
    catch {
        [Console]::Error.WriteLine('{"kind":"CHATGPT_WORKER_DIAGNOSTIC","worker":"UNKNOWN"}')
    }
}

function Invoke-SafeLatestPreSendRecovery {
    param([string]$ProjectPath,[string]$PythonPath,[string]$ConfigPath)
    $reservation = Get-ChatGptBridgeReservation -ProjectPath $ProjectPath
    $status = Get-LatestChatGptWorkerStatus -ProjectPath $ProjectPath
    if ($null -eq $reservation -or $null -eq $status) { return }
    if ([string]$status.state -ne 'STOPPED' -or [bool]$status.sendAttempted) { return }

    $phase = [string]$reservation.phase
    $errorCode = [string]$status.errorCode
    $recoverable = ($phase -eq 'NEW_CHAT_ATTEMPTED' -and
        $errorCode -in @('CHATGPT_NEW_CHAT_FAILED','CHATGPT_BINDING_CHANGED','CHATGPT_BRIDGE_UNAVAILABLE')) -or
        ($phase -eq 'INSERT_BOOTSTRAP_ATTEMPTED' -and
        $errorCode -eq 'CHATGPT_BOOTSTRAP_MISMATCH')
    if (-not $recoverable) { return }

    $dispatchId = [string]$status.dispatchId
    $attemptId = [string]$status.attemptId
    & $PythonPath -m factory_dispatcher.chatgpt_extension_recover `
        --config $ConfigPath `
        --dispatch-id $dispatchId `
        --attempt-id $attemptId
    if ($LASTEXITCODE -ne 0) {
        throw "Evidence-gated recovery for $attemptId was refused or uncertain."
    }
    $payload = [ordered]@{
        kind = 'CHATGPT_PRE_SEND_RECOVERY'
        dispatchId = $dispatchId
        attemptId = $attemptId
        outcome = 'RELEASED_PRE_SEND'
    }
    [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
}

$dispatcherArguments = @($dispatcherPath, '--tick', '--config', $configPath)
if ($DryRun) {
    $dispatcherArguments += '--dry-run'
}

Push-Location -LiteralPath $projectPath
try {
    Ensure-ChatGptExtensionBridge -ProjectPath $projectPath -PythonPath $pythonPath -ConfigPath $configPath -Revision $bridgeRuntimeRevision
    if ($DryRun) {
        Invoke-SafeLatestPreSendRecovery -ProjectPath $projectPath -PythonPath $pythonPath -ConfigPath $configPath
    }
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
