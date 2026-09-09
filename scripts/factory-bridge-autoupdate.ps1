[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$stateDir = Join-Path $root 'state'
$stagingDir = Join-Path $root 'staging'
$sourceDir = Join-Path $root 'source\DISPATCHER'
$binDir = Join-Path $root 'bin'
$adminDir = Join-Path $root 'admin'
$installedExe = Join-Path $binDir 'FactoryBridge.exe'
$previousExe = Join-Path $binDir 'FactoryBridge.prev.exe'
$nextExe = Join-Path $stagingDir 'FactoryBridge.next.exe'
$requestPath = Join-Path $stateDir 'self-update-request.json'
$resultPath = Join-Path $stateDir 'self-update-result.json'
$installedStatePath = Join-Path $stateDir 'installed-runtime.json'
$knownGoodStatePath = Join-Path $stateDir 'last-known-good-runtime.json'
$repo = 'https://github.com/Toctox/DISPATCHER.git'
$supervisorTask = 'FactoryBridge Supervisor'

New-Item -ItemType Directory -Path $stateDir,$stagingDir,$binDir,$adminDir,(Split-Path $sourceDir -Parent) -Force | Out-Null

function Write-JsonAtomic([string]$Path, $Value) {
    $tmp = $Path + '.tmp'
    [System.IO.File]::WriteAllText($tmp, ($Value | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Write-Result([string]$State, [string]$TargetCommit, [string]$Summary, [bool]$Healthy, [bool]$AdminAssetsSynced = $false) {
    $payload = [ordered]@{
        state = $State
        targetCommit = $TargetCommit
        summary = $Summary
        healthy = $Healthy
        adminAssetsSynced = $AdminAssetsSynced
        observedAt = (Get-Date).ToUniversalTime().ToString('o')
    }
    Write-JsonAtomic -Path $resultPath -Value $payload
    return $payload
}

function Sync-AdminAssets([string]$SourceRoot) {
    $assetMap = @(
        @{ source = (Join-Path $SourceRoot 'scripts\factory-bridge-autoupdate.ps1'); destination = (Join-Path $adminDir 'factory-bridge-autoupdate.ps1') },
        @{ source = (Join-Path $SourceRoot 'scripts\factory-bridge-admin-poller.ps1'); destination = (Join-Path $adminDir 'factory-bridge-admin-poller.ps1') }
    )
    foreach ($asset in $assetMap) {
        if (-not (Test-Path -LiteralPath $asset.source -PathType Leaf)) {
            throw ('required admin asset missing from approved source: ' + $asset.source)
        }
        $tmp = $asset.destination + '.next'
        Copy-Item -LiteralPath $asset.source -Destination $tmp -Force
        Move-Item -LiteralPath $tmp -Destination $asset.destination -Force
    }
}

if (-not (Test-Path -LiteralPath $requestPath -PathType Leaf)) {
    Write-Result -State 'BLOCKED' -TargetCommit '' -Summary 'self-update request is missing' -Healthy $false | Out-Null
    exit 2
}
$request = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
$target = ([string]$request.targetCommit).Trim().ToLowerInvariant()
if ($target -notmatch '^[0-9a-f]{40}$') {
    Write-Result -State 'BLOCKED' -TargetCommit $target -Summary 'targetCommit is not a full hexadecimal SHA' -Healthy $false | Out-Null
    exit 2
}

$git = Get-Command git.exe -ErrorAction SilentlyContinue
$go = Get-Command go.exe -ErrorAction SilentlyContinue
if ($null -eq $git -or $null -eq $go) {
    Write-Result -State 'BLOCKED' -TargetCommit $target -Summary 'git.exe and go.exe are required' -Healthy $false | Out-Null
    exit 3
}

$priorInstalledCommit = ''
try {
    if (Test-Path -LiteralPath (Join-Path $sourceDir '.git') -PathType Container) {
        & $git.Source -C $sourceDir fetch --prune origin main
        if ($LASTEXITCODE -ne 0) { throw 'git fetch origin main failed' }
    }
    else {
        if (Test-Path -LiteralPath $sourceDir) { Remove-Item -LiteralPath $sourceDir -Recurse -Force }
        & $git.Source clone $repo $sourceDir
        if ($LASTEXITCODE -ne 0) { throw 'git clone failed' }
        & $git.Source -C $sourceDir fetch --prune origin main
        if ($LASTEXITCODE -ne 0) { throw 'git fetch origin main failed after clone' }
    }

    $originMain = ((& $git.Source -C $sourceDir rev-parse origin/main) -join '').Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $originMain -ne $target) {
        throw "approved commit no longer equals origin/main: approved=$target origin/main=$originMain"
    }

    if (Test-Path -LiteralPath $installedStatePath -PathType Leaf) {
        try {
            $installedState = Get-Content -LiteralPath $installedStatePath -Raw | ConvertFrom-Json
            $priorInstalledCommit = ([string]$installedState.sourceCommit).Trim().ToLowerInvariant()
            if ($priorInstalledCommit -eq $target) {
                Sync-AdminAssets -SourceRoot $sourceDir
                Write-Result -State 'NOOP' -TargetCommit $target -Summary 'approved runtime is already installed; admin control assets synchronized' -Healthy $true -AdminAssetsSynced $true | Out-Null
                exit 0
            }
        } catch { }
    }

    & $git.Source -C $sourceDir checkout -f --detach $target
    if ($LASTEXITCODE -ne 0) { throw 'git checkout approved commit failed' }
    & $git.Source -C $sourceDir reset --hard $target
    if ($LASTEXITCODE -ne 0) { throw 'git reset approved commit failed' }

    Push-Location (Join-Path $sourceDir 'factory_bridge')
    try {
        & $go.Source test ./...
        if ($LASTEXITCODE -ne 0) { throw 'go test ./... failed; existing runtime was not touched' }
        if (Test-Path -LiteralPath $nextExe) { Remove-Item -LiteralPath $nextExe -Force }
        & $go.Source build -trimpath -ldflags '-s -w' -o $nextExe .
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $nextExe -PathType Leaf)) {
            throw 'go build failed; existing runtime was not touched'
        }
    }
    finally {
        Pop-Location
    }

    $task = Get-ScheduledTask -TaskName $supervisorTask -ErrorAction SilentlyContinue
    if ($null -ne $task) { Stop-ScheduledTask -TaskName $supervisorTask -ErrorAction SilentlyContinue }
    Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if ($null -eq (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 500
    }
    if ($null -ne (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) {
        throw 'FactoryBridge processes did not stop before promotion'
    }

    if (Test-Path -LiteralPath $previousExe) { Remove-Item -LiteralPath $previousExe -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $installedExe) { Copy-Item -LiteralPath $installedExe -Destination $previousExe -Force }
    Copy-Item -LiteralPath $nextExe -Destination $installedExe -Force

    $started = $false
    try {
        $task = Get-ScheduledTask -TaskName $supervisorTask -ErrorAction SilentlyContinue
        if ($null -ne $task) {
            if ([string]$task.State -eq 'Disabled') { Enable-ScheduledTask -TaskName $supervisorTask | Out-Null }
            Start-ScheduledTask -TaskName $supervisorTask
            $started = $true
        }
    } catch { }
    if (-not $started) {
        Start-Process -FilePath $installedExe -ArgumentList '--mode','supervisor' -WorkingDirectory $binDir
    }

    $healthy = $false
    $healthBody = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $healthBody = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/public/health' -TimeoutSec 2
            if ($healthBody.ok -eq $true -and $healthBody.executorOnline -eq $true) {
                $healthy = $true
                break
            }
        } catch { }
    }

    if (-not $healthy) {
        Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $previousExe -PathType Leaf) {
            Copy-Item -LiteralPath $previousExe -Destination $installedExe -Force
            $task = Get-ScheduledTask -TaskName $supervisorTask -ErrorAction SilentlyContinue
            if ($null -ne $task) { Start-ScheduledTask -TaskName $supervisorTask -ErrorAction SilentlyContinue }
            else { Start-Process -FilePath $installedExe -ArgumentList '--mode','supervisor' -WorkingDirectory $binDir }
        }
        Write-Result -State 'ROLLED_BACK' -TargetCommit $target -Summary 'new runtime failed health validation and previous binary was restored' -Healthy $false | Out-Null
        exit 4
    }

    $adminAssetsSynced = $false
    try {
        Sync-AdminAssets -SourceRoot $sourceDir
        $adminAssetsSynced = $true
    }
    catch {
        # Runtime promotion remains valid; record degraded admin-asset sync explicitly.
        $adminAssetsSynced = $false
    }

    if ($priorInstalledCommit -match '^[0-9a-f]{40}$') {
        $knownGood = [ordered]@{
            sourceCommit = $priorInstalledCommit
            recordedAt = (Get-Date).ToUniversalTime().ToString('o')
            reason = 'previous installed runtime preserved before successful promotion'
        }
        Write-JsonAtomic -Path $knownGoodStatePath -Value $knownGood
    }

    $installedState = [ordered]@{
        sourceCommit = $target
        installedAt = (Get-Date).ToUniversalTime().ToString('o')
        adminAssetsSynced = $adminAssetsSynced
        health = [ordered]@{ ok = $true; executorOnline = $true }
    }
    Write-JsonAtomic -Path $installedStatePath -Value $installedState

    if ($adminAssetsSynced) {
        Write-Result -State 'DONE' -TargetCommit $target -Summary 'tests, build, promotion, post-update health and admin control asset synchronization passed' -Healthy $true -AdminAssetsSynced $true | Out-Null
    }
    else {
        Write-Result -State 'DONE' -TargetCommit $target -Summary 'runtime promotion and health passed; admin control asset synchronization needs attention' -Healthy $true -AdminAssetsSynced $false | Out-Null
    }
    exit 0
}
catch {
    Write-Result -State 'FAILED' -TargetCommit $target -Summary $_.Exception.Message -Healthy $false | Out-Null
    exit 1
}
