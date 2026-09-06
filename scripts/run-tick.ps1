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

$dispatcherArguments = @($dispatcherPath, '--tick', '--config', $configPath)
if ($DryRun) {
    $dispatcherArguments += '--dry-run'
}

Push-Location -LiteralPath $projectPath
try {
    & $pythonPath @dispatcherArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
