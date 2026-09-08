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
$ErrorActionPreference = 'Stop'
$configPath = Join-Path $env:LOCALAPPDATA 'FactoryBridge\config.json'
$logPath = $null
$taskName = 'FactoryBridge Supervisor'
try {
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $bridgeRoot = [string]$config.bridgeRoot
    $statusDir = Join-Path $bridgeRoot '00_STATUS'
    if (-not (Test-Path -LiteralPath $statusDir -PathType Container)) {
        New-Item -ItemType Directory -Path $statusDir -Force | Out-Null
    }
    $logPath = Join-Path $statusDir 'factory-bridge-update.apply.log'
    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' apply-start')

    Start-Sleep -Seconds 5

    # Prevent Task Scheduler from racing the binary swap by immediately restarting
    # the supervisor after we stop it.
    $scheduledTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $scheduledTask) {
        Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' stopping-scheduled-supervisor')
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }

    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' stopping-bridge-processes')
    Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force
    for ($i = 0; $i -lt 20; $i++) {
        if ($null -eq (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($null -ne (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) {
        throw 'FactoryBridge processes did not stop before binary swap.'
    }

    $current = Join-Path $bridgeRoot 'FactoryBridge.exe'
    $next = Join-Path $bridgeRoot 'FactoryBridge.next.exe'
    $previous = Join-Path $bridgeRoot 'FactoryBridge.prev.exe'
    if (-not (Test-Path -LiteralPath $next -PathType Leaf)) {
        throw "Staged binary missing: $next"
    }

    $applied = $false
    for ($attempt = 1; $attempt -le 30 -and -not $applied; $attempt++) {
        try {
            if (Test-Path -LiteralPath $previous) {
                Remove-Item -LiteralPath $previous -Force
            }
            if (Test-Path -LiteralPath $current) {
                Move-Item -LiteralPath $current -Destination $previous -Force
            }
            Move-Item -LiteralPath $next -Destination $current -Force
            $applied = $true
        }
        catch {
            Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + " swap-retry-$attempt: " + $_.Exception.Message)
            if ($attempt -ge 30) { throw }
            Start-Sleep -Seconds 1
        }
    }

    Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' binary-swap-success')

    # A postgres install request is only converted into a real queue command after
    # the new binary is already in place. This prevents v0.11 from consuming it.
    $postgresRequest = Join-Path $statusDir 'postgres-install.request.json'
    if (Test-Path -LiteralPath $postgresRequest -PathType Leaf) {
        $commandsDir = Join-Path $bridgeRoot '01_COMMANDS'
        if (-not (Test-Path -LiteralPath $commandsDir -PathType Container)) {
            New-Item -ItemType Directory -Path $commandsDir -Force | Out-Null
        }
        $postgresCommandPath = Join-Path $commandsDir 'COMMAND__POSTGRES-INSTALL-LOCAL-001.json'
        $postgresResultPath = Join-Path (Join-Path $bridgeRoot '02_RESULTS') 'RESULT__POSTGRES-INSTALL-LOCAL-001.json'
        if (-not (Test-Path -LiteralPath $postgresCommandPath) -and -not (Test-Path -LiteralPath $postgresResultPath)) {
            $command = [ordered]@{ id = 'POSTGRES-INSTALL-LOCAL-001'; action = 'postgres.install' }
            [IO.File]::WriteAllText($postgresCommandPath, ($command | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
            Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' postgres-install-command-enqueued')
        }
        Remove-Item -LiteralPath $postgresRequest -Force
    }

    if ($null -ne $scheduledTask) {
        Start-ScheduledTask -TaskName $taskName
        Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' scheduled-supervisor-started')
    }
    else {
        Start-Process -FilePath $current -ArgumentList '--mode','supervisor' -WorkingDirectory $bridgeRoot
        Add-Content -LiteralPath $logPath -Value ((Get-Date).ToString('o') + ' direct-supervisor-started')
    }
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

# Start-Process joins ArgumentList items without preserving quotes around paths with spaces.
# Build one explicit command line and make the generated apply script discover bridgeRoot from local config.
$launchArgs = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$applyScript`""
Start-Process -FilePath 'powershell.exe' -ArgumentList $launchArgs -WindowStyle Hidden

$payload = [ordered]@{
    kind = 'FACTORY_BRIDGE_UPDATE'
    status = 'STAGED'
    source = $bridgeSource
    nextExe = $nextExe
    applyScript = $applyScript
}
Write-Output ($payload | ConvertTo-Json -Compress)
