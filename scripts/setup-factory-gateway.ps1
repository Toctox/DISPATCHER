param()

$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$gatewayRoot = Join-Path $runtimeRoot 'gateway'
$configPath = Join-Path $runtimeRoot 'config.json'
$repoRoot = Split-Path -Parent $PSScriptRoot
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

function Find-Cloudflared {
    $cmd = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\cloudflared.exe'),
        (Join-Path $env:ProgramFiles 'cloudflared\cloudflared.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'cloudflared\cloudflared.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if ($candidates.Count -gt 0) { return $candidates[0] }
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
    $cloudflared = Find-Cloudflared
    if (-not $cloudflared) {
        throw 'cloudflared installation completed but cloudflared.exe was not found in known locations.'
    }
}

if (-not (Test-Path -LiteralPath $runnerSource)) {
    throw "Tunnel runner not found: $runnerSource"
}
Copy-Item -LiteralPath $runnerSource -Destination $runnerLocal -Force

# Register a per-user logon task. The quick-tunnel URL may change after reboot;
# the runner republishes the current URL into Drive/00_BRAIN/GATEWAY_URL.txt.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $runnerLocal + '"')
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
& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $runnerLocal
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
