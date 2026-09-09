param()

$ErrorActionPreference = 'Stop'

$factoryRoot = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$configPath = Join-Path $factoryRoot 'config.json'
$stagingDir = Join-Path $factoryRoot 'staging'
$newBridgeRoot = Join-Path $factoryRoot 'mailbox'
$taskName = 'FactoryBridge Supervisor'

if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "FactoryBridge config not found: $configPath"
}

$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$oldBridgeRoot = [string]$config.bridgeRoot
if ([string]::IsNullOrWhiteSpace($oldBridgeRoot)) {
    throw 'FactoryBridge bridgeRoot is empty.'
}

if ([System.IO.Path]::GetFullPath($oldBridgeRoot).TrimEnd('\\') -eq [System.IO.Path]::GetFullPath($newBridgeRoot).TrimEnd('\\')) {
    Write-Host 'FACTORY_DRIVE_MIGRATION=ALREADY_LOCAL'
    exit 0
}

if (-not (Test-Path -LiteralPath $oldBridgeRoot -PathType Container)) {
    throw "Legacy Drive bridge root not found: $oldBridgeRoot"
}
if ((Split-Path -Leaf $oldBridgeRoot) -ne 'FACTORY_BRIDGE') {
    throw "Refusing migration from unexpected bridge root: $oldBridgeRoot"
}

$requiredLegacyMarkers = @('00_STATUS','01_COMMANDS','02_RESULTS')
foreach ($marker in $requiredLegacyMarkers) {
    if (-not (Test-Path -LiteralPath (Join-Path $oldBridgeRoot $marker))) {
        throw "Refusing migration: expected legacy folder is missing: $marker"
    }
}

foreach ($name in @('00_BRAIN','00_STATUS','01_COMMANDS','01_INBOX','02_OUTBOX','02_RESULTS','03_ARCHIVE','03_DOCS')) {
    New-Item -ItemType Directory -Path (Join-Path $newBridgeRoot $name) -Force | Out-Null
}
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

# Preserve only the compact brain snapshot locally if one exists. Heavy history stays
# neither in the new mailbox nor in the Drive brain.
$legacyCurrent = Join-Path $oldBridgeRoot '00_BRAIN\CURRENT_STATE.json'
if (Test-Path -LiteralPath $legacyCurrent -PathType Leaf) {
    Copy-Item -LiteralPath $legacyCurrent -Destination (Join-Path $newBridgeRoot '00_BRAIN\CURRENT_STATE.json') -Force
}

$config.bridgeRoot = $newBridgeRoot
$json = $config | ConvertTo-Json -Depth 20
[System.IO.File]::WriteAllText($configPath, $json, (New-Object System.Text.UTF8Encoding($false)))

$manifest = [ordered]@{
    protocol = 'FACTORY_BUS_V1'
    migratedAt = (Get-Date).ToUniversalTime().ToString('o')
    oldBridgeRoot = $oldBridgeRoot
    newBridgeRoot = $newBridgeRoot
    primaryBus = 'GitHub Issue #7'
    dashboard = 'Tailscale Funnel'
    driveRole = 'documentation_and_emergency_fallback_only'
    preserved = @('00_BRAIN','03_DOCS','INSTALL_FACTORY_BRIDGE_CLEAN.cmd','RECOVER_FACTORY_BRIDGE.cmd','SETUP_FACTORY_GATEWAY_STABLE.cmd')
}
foreach ($target in @(
    (Join-Path $oldBridgeRoot '00_BRAIN\DRIVE_MIGRATION_COMPLETE.json'),
    (Join-Path $newBridgeRoot '00_BRAIN\DRIVE_MIGRATION_COMPLETE.json')
)) {
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    [System.IO.File]::WriteAllText($target, ($manifest | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
}

$completionScript = Join-Path $stagingDir 'COMPLETE_DRIVE_MIGRATION.ps1'
$oldEsc = $oldBridgeRoot.Replace("'", "''")
$newEsc = $newBridgeRoot.Replace("'", "''")
$completion = @"
`$ErrorActionPreference = 'Continue'
`$oldRoot = '$oldEsc'
`$newRoot = '$newEsc'
`$taskName = '$taskName'

Start-Sleep -Seconds 8
try { Stop-ScheduledTask -TaskName `$taskName -ErrorAction SilentlyContinue } catch {}
try { Get-Process -Name 'FactoryBridge' -ErrorAction SilentlyContinue | Stop-Process -Force } catch {}
Start-Sleep -Seconds 2
try {
    `$task = Get-ScheduledTask -TaskName `$taskName -ErrorAction Stop
    if ([string]`$task.State -eq 'Disabled') { Enable-ScheduledTask -TaskName `$taskName | Out-Null }
    Start-ScheduledTask -TaskName `$taskName
} catch {}

# Give the restarted runtime time to open its LOCALAPPDATA mailbox before deleting
# the old synced transport tree.
Start-Sleep -Seconds 10

function Remove-LegacyPath([string]`$path) {
    for (`$i = 0; `$i -lt 4; `$i++) {
        if (-not (Test-Path -LiteralPath `$path)) { return }
        try {
            Remove-Item -LiteralPath `$path -Recurse -Force -ErrorAction Stop
            return
        } catch {
            Start-Sleep -Seconds 3
        }
    }
}

foreach (`$name in @('00_STATUS','01_COMMANDS','01_INBOX','02_OUTBOX','02_RESULTS','03_ARCHIVE')) {
    Remove-LegacyPath (Join-Path `$oldRoot `$name)
}

# Runtime/build packages belong on the notebook. Keep only explicit recovery and
# human bootstrap files at the Drive root.
`$legacyPatterns = @(
    'FactoryBridge.exe',
    'FactoryBridge-*.exe',
    'FactoryBridge-*.zip',
    'FactoryBridge-src-*.zip',
    'FactoryBridge.exe.*.bak',
    'APPLY_FACTORY_BRIDGE_UPDATE.ps1',
    'INSTALL_FACTORY_BRIDGE_AUTOSTART.cmd',
    'START_FACTORY_BRIDGE*.cmd',
    'SETUP_FACTORY_GATEWAY.cmd',
    'FACTORY_BRIDGE_README.txt'
)
foreach (`$pattern in `$legacyPatterns) {
    Get-ChildItem -LiteralPath `$oldRoot -Filter `$pattern -File -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-LegacyPath `$_.FullName
    }
}

`$done = [ordered]@{
    completedAt = (Get-Date).ToUniversalTime().ToString('o')
    newBridgeRoot = `$newRoot
    status = 'DONE'
}
`$donePath = Join-Path `$oldRoot '00_BRAIN\DRIVE_CLEANUP_DONE.json'
New-Item -ItemType Directory -Path (Split-Path -Parent `$donePath) -Force | Out-Null
[System.IO.File]::WriteAllText(`$donePath, (`$done | ConvertTo-Json), (New-Object System.Text.UTF8Encoding(`$false)))
"@
[System.IO.File]::WriteAllText($completionScript, $completion, (New-Object System.Text.UTF8Encoding($false)))

Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
    '-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',$completionScript
) | Out-Null

Write-Host 'FACTORY_DRIVE_MIGRATION=SCHEDULED'
Write-Host "OLD_BRIDGE_ROOT=$oldBridgeRoot"
Write-Host "NEW_BRIDGE_ROOT=$newBridgeRoot"
Write-Host 'PRIMARY_BUS=GitHub Issue #7'
