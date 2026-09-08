[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$bridgeSource = Join-Path $repoRoot 'factory_bridge'
$configPath = Join-Path $env:LOCALAPPDATA 'FactoryBridge\config.json'

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "FactoryBridge config not found: $configPath"
}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$bridgeRoot = [string]$config.bridgeRoot
if ([string]::IsNullOrWhiteSpace($bridgeRoot) -or -not (Test-Path -LiteralPath $bridgeRoot -PathType Container)) {
    throw "FactoryBridge bridgeRoot is unavailable: $bridgeRoot"
}

$go = Get-Command go.exe -ErrorAction SilentlyContinue
if ($null -eq $go) {
    throw 'go.exe is required for FactoryBridge self-update and was not found on PATH.'
}

$nextExe = Join-Path $bridgeRoot 'FactoryBridge.next.exe'
$currentExe = Join-Path $bridgeRoot 'FactoryBridge.exe'
$previousExe = Join-Path $bridgeRoot 'FactoryBridge.prev.exe'
$applyScript = Join-Path $bridgeRoot 'APPLY_FACTORY_BRIDGE_UPDATE.ps1'

Push-Location -LiteralPath $bridgeSource
try {
    & $go.Source test ./...
    if ($LASTEXITCODE -ne 0) {
        throw "FactoryBridge tests failed with exit code $LASTEXITCODE."
    }
    & $go.Source build -trimpath -ldflags '-s -w' -o $nextExe .
    if ($LASTEXITCODE -ne 0) {
        throw "FactoryBridge build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $nextExe -PathType Leaf)) {
    throw "Staged FactoryBridge binary was not created: $nextExe"
}

$apply = @'
param(
    [Parameter(Mandatory=$true)][string]$BridgeRoot
)
$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds 5
Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
$current = Join-Path $BridgeRoot 'FactoryBridge.exe'
$next = Join-Path $BridgeRoot 'FactoryBridge.next.exe'
$previous = Join-Path $BridgeRoot 'FactoryBridge.prev.exe'
if (-not (Test-Path -LiteralPath $next -PathType Leaf)) { throw "Staged binary missing: $next" }
if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous -Force }
if (Test-Path -LiteralPath $current) { Move-Item -LiteralPath $current -Destination $previous -Force }
Move-Item -LiteralPath $next -Destination $current -Force
Start-Process -FilePath $current -ArgumentList '--mode','supervisor' -WorkingDirectory $BridgeRoot
'@
Set-Content -LiteralPath $applyScript -Value $apply -Encoding UTF8

Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoLogo',
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy', 'Bypass',
    '-File', $applyScript,
    '-BridgeRoot', $bridgeRoot
) -WindowStyle Hidden

$payload = [ordered]@{
    kind = 'FACTORY_BRIDGE_UPDATE'
    status = 'STAGED'
    source = $bridgeSource
    nextExe = $nextExe
    applyScript = $applyScript
}
Write-Output ($payload | ConvertTo-Json -Compress)
