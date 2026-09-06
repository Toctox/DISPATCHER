[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$dispatcherPath = Join-Path $projectPath 'dispatcher.py'
$configPath = Join-Path $projectPath 'config.json'
$domProbePath = Join-Path $projectPath 'scripts\probe_chatgpt_dom.py'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Virtual environment not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Configuration not found: $configPath"
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

function Write-ChatGptDomDiagnostic {
    param([string]$PythonPath,[string]$ProbePath,[string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ProbePath -PathType Leaf)) {
        [Console]::Error.WriteLine('{"kind":"CHATGPT_DOM_DIAGNOSTIC","outcome":"PROBE_MISSING"}')
        return
    }
    try {
        $lines = & $PythonPath $ProbePath --config $ConfigPath 2>&1
        foreach ($line in $lines) {
            [Console]::Error.WriteLine([string]$line)
        }
    }
    catch {
        [Console]::Error.WriteLine('{"kind":"CHATGPT_DOM_DIAGNOSTIC","outcome":"PROBE_FAILED"}')
    }
}

$dispatcherArguments = @($dispatcherPath, '--tick', '--config', $configPath)
if ($DryRun) {
    $dispatcherArguments += '--dry-run'
}

Push-Location -LiteralPath $projectPath
try {
    Write-ChatGptBridgeDiagnostic -ProjectPath $projectPath
    if ($DryRun) {
        Write-ChatGptDomDiagnostic -PythonPath $pythonPath -ProbePath $domProbePath -ConfigPath $configPath
    }
    & $pythonPath @dispatcherArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
