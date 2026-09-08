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
$applyScript = Join-Path $bridgeRoot 'APPLY_FACTORY_BRIDGE_UPDATE.ps1'

Push-Location -LiteralPath $bridgeSource
try {
    & $go.Source test ./...
    if ($LASTEXITCODE -ne 0) { throw "FactoryBridge tests failed with exit code $LASTEXITCODE." }
    & $go.Source build -trimpath -ldflags '-s -w' -o $nextExe .
    if ($LASTEXITCODE -ne 0) { throw "FactoryBridge build failed with exit code $LASTEXITCODE." }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $nextExe -PathType Leaf)) {
    throw "Staged FactoryBridge binary was not created: $nextExe"
}

$apply = @'
$ErrorActionPreference = 'Stop'
$configPath = Join-Path $env:LOCALAPPDATA 'FactoryBridge\config.json'
$logPath = $null
try {
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $bridgeRoot = [string]$config.bridgeRoot
    $statusDir = Join-Path $bridgeRoot '00_STATUS'
    if (-not (Test-Path -LiteralPath $statusDir -PathType Container)) {
        New-Item -ItemType Directory -Path $statusDir -Force | Out-Null
    }
    $logPath = Join-Path $statusDir 'factory-bridge-update.apply.log'
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' apply-start-user-level')

    # Let dispatcher.tick finish writing its RESULT before terminating Bridge processes.
    Start-Sleep -Seconds 5

    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' stopping-bridge-processes')
    Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if ($null -eq (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($null -ne (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) {
        throw 'FactoryBridge processes did not stop before binary swap.'
    }

    $current = Join-Path $bridgeRoot 'FactoryBridge.exe'
    $next = Join-Path $bridgeRoot 'FactoryBridge.next.exe'
    $previous = Join-Path $bridgeRoot 'FactoryBridge.prev.exe'
    if (-not (Test-Path -LiteralPath $next -PathType Leaf)) { throw "Staged binary missing: $next" }

    if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $current) { Move-Item -LiteralPath $current -Destination $previous -Force }
    try {
        Move-Item -LiteralPath $next -Destination $current -Force
    }
    catch {
        if ((Test-Path -LiteralPath $previous) -and -not (Test-Path -LiteralPath $current)) {
            Move-Item -LiteralPath $previous -Destination $current -Force -ErrorAction SilentlyContinue
        }
        throw
    }
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' binary-swap-success')

    # Keep the task/autostart target synchronized, but do not require permission to control the task itself.
    $localBinDir = Join-Path $env:LOCALAPPDATA 'FactoryBridge\bin'
    $localExe = Join-Path $localBinDir 'FactoryBridge.exe'
    if (-not (Test-Path -LiteralPath $localBinDir -PathType Container)) {
        New-Item -ItemType Directory -Path $localBinDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $current -Destination $localExe -Force
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' autostart-binary-updated')

    # Start the promoted binary directly at user level. The existing scheduled task, if any,
    # points to the same local binary and remains a recovery mechanism for future logons/restarts.
    Start-Process -FilePath $current -ArgumentList '--mode','supervisor' -WorkingDirectory $bridgeRoot
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' direct-supervisor-started')
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' apply-success')
}
catch {
    if ($null -ne $logPath) {
        Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' apply-failed: ' + $_.Exception.Message)
    }
    exit 1
}
'@
Set-Content -LiteralPath $applyScript -Value $apply -Encoding UTF8

$launchArgs = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$applyScript`""
Start-Process -FilePath 'powershell.exe' -ArgumentList $launchArgs -WindowStyle Hidden

$payload = [ordered]@{
    kind = 'FACTORY_BRIDGE_UPDATE'
    status = 'STAGED'
    source = $bridgeSource
    nextExe = $nextExe
    applyScript = $applyScript
    promotion = 'user-level-no-task-control'
}
Write-Output ($payload | ConvertTo-Json -Compress)
