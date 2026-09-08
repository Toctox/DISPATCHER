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

# Build and apply artifacts stay on the local disk. Google Drive is transport
# and observability only; sync locks must not be able to break compilation.
$stagingDir = Join-Path $env:LOCALAPPDATA 'FactoryBridge\staging'
if (-not (Test-Path -LiteralPath $stagingDir -PathType Container)) {
    New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
}
$nextExe = Join-Path $stagingDir 'FactoryBridge.next.exe'
$applyScript = Join-Path $stagingDir 'APPLY_FACTORY_BRIDGE_UPDATE.ps1'
if (Test-Path -LiteralPath $nextExe) {
    Remove-Item -LiteralPath $nextExe -Force -ErrorAction SilentlyContinue
}

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
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' apply-start-side-by-side')

    Start-Sleep -Seconds 5

    # A scheduled supervisor and a directly started supervisor must never run
    # at the same time: they would spawn competing executors that overwrite
    # executor.json with different PIDs and trigger false stale-heartbeat kills.
    $taskName = 'FactoryBridge Supervisor'
    $scheduledTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $scheduledTask) {
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' scheduled-supervisor-stopped')
    }

    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' stopping-bridge-processes')
    Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if ($null -eq (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($null -ne (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) {
        throw 'FactoryBridge processes did not stop before local deployment.'
    }
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' bridge-processes-stopped')

    $staged = Join-Path $env:LOCALAPPDATA 'FactoryBridge\staging\FactoryBridge.next.exe'
    if (-not (Test-Path -LiteralPath $staged -PathType Leaf)) { throw "Staged binary missing: $staged" }

    $localBinDir = Join-Path $env:LOCALAPPDATA 'FactoryBridge\bin'
    $localExe = Join-Path $localBinDir 'FactoryBridge.exe'
    $localPrevious = Join-Path $localBinDir 'FactoryBridge.prev.exe'
    if (-not (Test-Path -LiteralPath $localBinDir -PathType Container)) {
        New-Item -ItemType Directory -Path $localBinDir -Force | Out-Null
    }
    if (Test-Path -LiteralPath $localPrevious) { Remove-Item -LiteralPath $localPrevious -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $localExe) { Copy-Item -LiteralPath $localExe -Destination $localPrevious -Force }
    Copy-Item -LiteralPath $staged -Destination $localExe -Force
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' local-runtime-promoted')

    # Prefer the canonical scheduled supervisor when installed. Heal a disabled
    # task before starting it; if Task Scheduler still cannot launch it, fall
    # back to one direct supervisor so the Bridge never remains offline.
    $scheduledTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    $scheduledStarted = $false
    if ($null -ne $scheduledTask) {
        try {
            if ([string]$scheduledTask.State -eq 'Disabled') {
                Enable-ScheduledTask -TaskName $taskName | Out-Null
                Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' scheduled-supervisor-enabled')
            }
            Start-ScheduledTask -TaskName $taskName
            $scheduledStarted = $true
            Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' scheduled-supervisor-started')
        }
        catch {
            Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' scheduled-supervisor-start-failed: ' + $_.Exception.Message)
        }
    }
    if (-not $scheduledStarted) {
        Start-Process -FilePath $localExe -ArgumentList '--mode','supervisor' -WorkingDirectory $localBinDir
        Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' local-supervisor-started-fallback')
    }
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' apply-success-side-by-side')
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
    promotion = 'side-by-side-local-runtime'
    staging = 'localappdata'
}
Write-Output ($payload | ConvertTo-Json -Compress)
