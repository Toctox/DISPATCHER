[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$stateDir = Join-Path $root 'state'
$stagingDir = Join-Path $root 'staging'
$sourceDir = Join-Path $root 'source\DISPATCHER'
$binDir = Join-Path $root 'bin'
$adminDir = Join-Path $root 'admin'
$goldenDir = Join-Path $root 'golden'
$installedExe = Join-Path $binDir 'FactoryBridge.exe'
$previousExe = Join-Path $binDir 'FactoryBridge.prev.exe'
$nextExe = Join-Path $stagingDir 'FactoryBridge.next.exe'
$goldenExe = Join-Path $goldenDir 'FactoryBridge.golden.exe'
$goldenHashPath = Join-Path $goldenDir 'FactoryBridge.golden.sha256'
$goldenStatePath = Join-Path $goldenDir 'golden-runtime.json'
$requestPath = Join-Path $stateDir 'self-update-request.json'
$resultPath = Join-Path $stateDir 'self-update-result.json'
$installedStatePath = Join-Path $stateDir 'installed-runtime.json'
$knownGoodStatePath = Join-Path $stateDir 'last-known-good-runtime.json'
$promotionCertificatePath = Join-Path $stateDir 'promotion-certificate.json'
$repo = 'https://github.com/Toctox/DISPATCHER.git'
$supervisorTask = 'FactoryBridge Supervisor'

New-Item -ItemType Directory -Path $stateDir,$stagingDir,$binDir,$adminDir,$goldenDir,(Split-Path $sourceDir -Parent) -Force | Out-Null

function Write-JsonAtomic([string]$Path, $Value) {
    $tmp = $Path + '.tmp'
    [System.IO.File]::WriteAllText($tmp, ($Value | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Write-TextAtomic([string]$Path, [string]$Value) {
    $tmp = $Path + '.tmp'
    [System.IO.File]::WriteAllText($tmp, $Value, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
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

function Save-GoldenSnapshot([string]$SourceCommit) {
    if (-not (Test-Path -LiteralPath $installedExe -PathType Leaf)) { return $false }
    if ($SourceCommit -notmatch '^[0-9a-f]{40}$') { return $false }
    $tmp = $goldenExe + '.next'
    Copy-Item -LiteralPath $installedExe -Destination $tmp -Force
    $hash = Get-Sha256 -Path $tmp
    Move-Item -LiteralPath $tmp -Destination $goldenExe -Force
    Write-TextAtomic -Path $goldenHashPath -Value ($hash + [Environment]::NewLine)
    Write-JsonAtomic -Path $goldenStatePath -Value ([ordered]@{
        sourceCommit = $SourceCommit
        binarySha256 = $hash
        recordedAt = (Get-Date).ToUniversalTime().ToString('o')
        reason = 'last known-good installed runtime preserved before promotion'
    })
    return $true
}

function Start-FactoryBridge {
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
}

function Stop-FactoryBridge {
    $task = Get-ScheduledTask -TaskName $supervisorTask -ErrorAction SilentlyContinue
    if ($null -ne $task) { Stop-ScheduledTask -TaskName $supervisorTask -ErrorAction SilentlyContinue }
    Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 30; $i++) {
        if ($null -eq (Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 500
    }
    throw 'FactoryBridge processes did not stop before promotion'
}

function Wait-ExactRuntimeHealth([string]$TargetCommit, [string]$ExpectedBinaryHash) {
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/public/health' -TimeoutSec 2
            if ($health.ok -eq $true -and $health.executorOnline -eq $true -and
                ([string]$health.sourceCommit).ToLowerInvariant() -eq $TargetCommit -and
                ([string]$health.binarySha256).ToLowerInvariant() -eq $ExpectedBinaryHash -and
                [string]$health.protocolVersion -eq 'FACTORY_BUS_V2' -and
                -not [string]::IsNullOrWhiteSpace([string]$health.policyVersion)) {
                return $health
            }
        } catch { }
    }
    return $null
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
    Write-Result -State 'BLOCKED' -TargetCommit $target -Summary 'git.exe and go.exe are required for promotion; offline recovery does not require them' -Healthy $false | Out-Null
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
    if ($LASTEXITCODE -ne 0 -or $originMain -notmatch '^[0-9a-f]{40}$') { throw 'origin/main could not be resolved' }
    & $git.Source -C $sourceDir cat-file -e ($target + '^{commit}') 2>$null
    if ($LASTEXITCODE -ne 0) { throw "approved target commit is unavailable locally after fetch: $target" }
    & $git.Source -C $sourceDir merge-base --is-ancestor $target origin/main
    if ($LASTEXITCODE -ne 0) {
        throw "approved target is no longer reachable from reviewed origin/main: approved=$target origin/main=$originMain"
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
        & $go.Source build -trimpath -ldflags ("-s -w -X main.buildSourceCommit=$target") -o $nextExe .
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $nextExe -PathType Leaf)) {
            throw 'go build failed; existing runtime was not touched'
        }
    }
    finally { Pop-Location }

    $nextHash = Get-Sha256 -Path $nextExe
    if ($priorInstalledCommit -match '^[0-9a-f]{40}$') {
        [void](Save-GoldenSnapshot -SourceCommit $priorInstalledCommit)
    }

    Stop-FactoryBridge
    if (Test-Path -LiteralPath $previousExe) { Remove-Item -LiteralPath $previousExe -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $installedExe) { Copy-Item -LiteralPath $installedExe -Destination $previousExe -Force }
    Copy-Item -LiteralPath $nextExe -Destination $installedExe -Force
    if ((Get-Sha256 -Path $installedExe) -ne $nextHash) { throw 'promoted binary hash differs from staged binary hash' }

    Start-FactoryBridge
    $healthBody = Wait-ExactRuntimeHealth -TargetCommit $target -ExpectedBinaryHash $nextHash
    if ($null -eq $healthBody) {
        Stop-FactoryBridge
        if (Test-Path -LiteralPath $previousExe -PathType Leaf) {
            Copy-Item -LiteralPath $previousExe -Destination $installedExe -Force
            Start-FactoryBridge
        }
        Write-Result -State 'ROLLED_BACK' -TargetCommit $target -Summary 'new runtime failed exact identity/health validation and previous binary was restored' -Healthy $false | Out-Null
        exit 4
    }

    $adminAssetsSynced = $false
    try {
        Sync-AdminAssets -SourceRoot $sourceDir
        $adminAssetsSynced = $true
    } catch { $adminAssetsSynced = $false }

    if ($priorInstalledCommit -match '^[0-9a-f]{40}$') {
        Write-JsonAtomic -Path $knownGoodStatePath -Value ([ordered]@{
            sourceCommit = $priorInstalledCommit
            recordedAt = (Get-Date).ToUniversalTime().ToString('o')
            reason = 'previous installed runtime preserved before successful promotion'
        })
    } elseif (-not (Test-Path -LiteralPath $goldenExe -PathType Leaf)) {
        [void](Save-GoldenSnapshot -SourceCommit $target)
    }

    $installedState = [ordered]@{
        sourceCommit = $target
        binarySha256 = $nextHash
        protocolVersion = [string]$healthBody.protocolVersion
        policyVersion = [string]$healthBody.policyVersion
        bridgeVersion = [string]$healthBody.bridgeVersion
        installedAt = (Get-Date).ToUniversalTime().ToString('o')
        adminAssetsSynced = $adminAssetsSynced
        health = [ordered]@{ ok = $true; executorOnline = $true }
    }
    Write-JsonAtomic -Path $installedStatePath -Value $installedState

    Write-JsonAtomic -Path $promotionCertificatePath -Value ([ordered]@{
        state = 'PASSED'
        targetCommit = $target
        binarySha256 = $nextHash
        protocolVersion = [string]$healthBody.protocolVersion
        policyVersion = [string]$healthBody.policyVersion
        bridgeVersion = [string]$healthBody.bridgeVersion
        executorOnline = $true
        healthObservedAt = [string]$healthBody.observedAt
        certifiedAt = (Get-Date).ToUniversalTime().ToString('o')
    })

    if ($adminAssetsSynced) {
        Write-Result -State 'DONE' -TargetCommit $target -Summary 'tests, exact-commit build, binary hash match, exact runtime identity, promotion certificate, post-update health and admin asset sync passed' -Healthy $true -AdminAssetsSynced $true | Out-Null
    }
    else {
        Write-Result -State 'DONE' -TargetCommit $target -Summary 'promotion certificate and exact runtime identity passed; admin control asset synchronization needs attention' -Healthy $true -AdminAssetsSynced $false | Out-Null
    }
    exit 0
}
catch {
    Write-Result -State 'FAILED' -TargetCommit $target -Summary $_.Exception.Message -Healthy $false | Out-Null
    exit 1
}
