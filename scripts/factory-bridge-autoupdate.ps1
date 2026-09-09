[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$stateDir = Join-Path $root 'state'
$stagingDir = Join-Path $root 'staging'
$sourceDir = Join-Path $root 'source\DISPATCHER'
$binDir = Join-Path $root 'bin'
$installedExe = Join-Path $binDir 'FactoryBridge.exe'
$previousExe = Join-Path $binDir 'FactoryBridge.prev.exe'
$nextExe = Join-Path $stagingDir 'FactoryBridge.next.exe'
$requestPath = Join-Path $stateDir 'self-update-request.json'
$resultPath = Join-Path $stateDir 'self-update-result.json'
$installedStatePath = Join-Path $stateDir 'installed-runtime.json'
$repo = 'https://github.com/Toctox/DISPATCHER.git'
$supervisorTask = 'FactoryBridge Supervisor'

New-Item -ItemType Directory -Path $stateDir,$stagingDir,$binDir,(Split-Path $sourceDir -Parent) -Force | Out-Null

function Write-Result([string]$State, [string]$TargetCommit, [string]$Summary, [bool]$Healthy) {
    $payload = [ordered]@{
        state = $State
        targetCommit = $TargetCommit
        summary = $Summary
        healthy = $Healthy
        observedAt = (Get-Date).ToUniversalTime().ToString('o')
    }
    [System.IO.File]::WriteAllText($resultPath, ($payload | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
    return $payload
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
            if (([string]$installedState.sourceCommit).Trim().ToLowerInvariant() -eq $target) {
                Write-Result -State 'NOOP' -TargetCommit $target -Summary 'approved commit is already installed' -Healthy $true | Out-Null
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

    $installedState = [ordered]@{
        sourceCommit = $target
        installedAt = (Get-Date).ToUniversalTime().ToString('o')
        health = [ordered]@{ ok = $true; executorOnline = $true }
    }
    [System.IO.File]::WriteAllText($installedStatePath, ($installedState | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
    Write-Result -State 'DONE' -TargetCommit $target -Summary 'tests, build, promotion and post-update health validation passed' -Healthy $true | Out-Null
    exit 0
}
catch {
    Write-Result -State 'FAILED' -TargetCommit $target -Summary $_.Exception.Message -Healthy $false | Out-Null
    exit 1
}
