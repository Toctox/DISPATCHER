[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$dispatcherPath = Join-Path $projectPath 'dispatcher.py'
$configPath = Join-Path $projectPath 'config.json'

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

$dispatcherArguments = @($dispatcherPath, '--tick', '--config', $configPath)
if ($DryRun) {
    $dispatcherArguments += '--dry-run'
}

Push-Location -LiteralPath $projectPath
try {
    Write-ChatGptBridgeDiagnostic -ProjectPath $projectPath
    & $pythonPath @dispatcherArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
