param()

$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$gatewayRoot = Join-Path $runtimeRoot 'gateway'
$configPath = Join-Path $runtimeRoot 'config.json'
$runnerSource = Join-Path $PSScriptRoot 'run-factory-gateway-quick-tunnel.ps1'
$runnerLocal = Join-Path $gatewayRoot 'run-factory-gateway-quick-tunnel.ps1'
$taskName = 'FactoryBridge Gateway Tunnel'

New-Item -ItemType Directory -Force -Path $gatewayRoot | Out-Null

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "FactoryBridge config not found: $configPath"
}

# The gateway must already be served by the newly installed FactoryBridge.
$healthOk = $false
for ($i = 0; $i -lt 12; $i++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/public/health' -TimeoutSec 2
        if ($health) { $healthOk = $true; break }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $healthOk) {
    throw 'FactoryBridge HTTP gateway is not responding on 127.0.0.1:8787. Install/update FactoryBridge first.'
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = @($machine, $user) -join ';'
}

function Find-Cloudflared {
    Refresh-ProcessPath
    $cmd = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }

    $directCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\cloudflared.exe'),
        (Join-Path $env:ProgramFiles 'cloudflared\cloudflared.exe'),
        (Join-Path $env:ProgramFiles 'Cloudflare\cloudflared.exe'),
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} 'cloudflared\cloudflared.exe' })
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if ($directCandidates.Count -gt 0) { return (Resolve-Path -LiteralPath $directCandidates[0]).Path }

    $packagesRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path -LiteralPath $packagesRoot) {
        $packageExe = Get-ChildItem -LiteralPath $packagesRoot -Filter 'cloudflared.exe' -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'Cloudflare\.cloudflared' } |
            Select-Object -First 1
        if ($packageExe) { return $packageExe.FullName }
    }

    return $null
}

$cloudflared = Find-Cloudflared
if (-not $cloudflared) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'cloudflared is not installed and winget.exe is unavailable.'
    }
    Write-Host 'Installing Cloudflare cloudflared...'
    & $winget.Source install --id Cloudflare.cloudflared -e --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget cloudflared install failed with exit code $LASTEXITCODE"
    }
    Start-Sleep -Seconds 1
    $cloudflared = Find-Cloudflared
    if (-not $cloudflared) {
        throw 'cloudflared installation completed but cloudflared.exe was not found in PATH, WinGet Links, package storage, or Program Files.'
    }
}
Write-Host "cloudflared resolved: $cloudflared"

if (-not (Test-Path -LiteralPath $runnerSource)) {
    throw "Tunnel runner not found: $runnerSource"
}
Copy-Item -LiteralPath $runnerSource -Destination $runnerLocal -Force

# Register a per-user logon task. Use the absolute cloudflared path so autostart
# never depends on whether WinGet PATH updates are visible in a given shell.
$escapedRunner = $runnerLocal.Replace('"','\"')
$escapedCloudflared = $cloudflared.Replace('"','\"')
$taskArgs = '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $escapedRunner + '" -CloudflaredPath "' + $escapedCloudflared + '"'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
    Enable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
} catch {
    Write-Warning "Autostart task could not be registered: $($_.Exception.Message)"
}

Write-Host 'Starting secure outbound quick tunnel...'
& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $runnerLocal -CloudflaredPath $cloudflared
if ($LASTEXITCODE -ne 0) {
    throw "quick tunnel launcher failed with exit code $LASTEXITCODE"
}

$urlPath = Join-Path $gatewayRoot 'public-url.txt'
if (-not (Test-Path -LiteralPath $urlPath)) {
    throw 'Gateway public URL file was not created.'
}
$url = (Get-Content -LiteralPath $urlPath -Raw).Trim()
Write-Host ''
Write-Host 'FACTORY_GATEWAY_READY'
Write-Host "URL=$url"
Write-Host 'Public endpoints:'
Write-Host "  $url/public/health"
Write-Host "  $url/public/attention"
Write-Host "  $url/public/checkpoint"
Write-Host "  $url/public/showcase"
