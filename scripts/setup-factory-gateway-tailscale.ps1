param()

$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$configPath = Join-Path $runtimeRoot 'config.json'
$gatewayRoot = Join-Path $runtimeRoot 'gateway'
$stableUrlPath = Join-Path $gatewayRoot 'stable-public-url.txt'

New-Item -ItemType Directory -Force -Path $gatewayRoot | Out-Null

if (-not (Test-Path -LiteralPath $configPath)) {
    throw "FactoryBridge config not found: $configPath"
}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$bridgeRoot = [string]$config.bridgeRoot
if ([string]::IsNullOrWhiteSpace($bridgeRoot)) {
    throw 'bridgeRoot is missing from FactoryBridge config'
}
$brainDir = Join-Path $bridgeRoot '00_BRAIN'
New-Item -ItemType Directory -Force -Path $brainDir | Out-Null

# Gateway must already be healthy locally.
$localHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/public/health' -TimeoutSec 5
if (-not $localHealth) {
    throw 'FactoryBridge gateway is not responding on 127.0.0.1:8787'
}

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = @($machine, $user) -join ';'
}

function Find-Tailscale {
    Refresh-ProcessPath
    $cmd = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($cmd -and (Test-Path -LiteralPath $cmd.Source)) { return $cmd.Source }
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'),
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} 'Tailscale\tailscale.exe' }),
        (Join-Path $env:LOCALAPPDATA 'Tailscale\tailscale.exe')
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    if ($candidates.Count -gt 0) { return (Resolve-Path -LiteralPath $candidates[0]).Path }
    return $null
}

$tailscale = Find-Tailscale
if (-not $tailscale) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw 'Tailscale is not installed and winget.exe is unavailable.'
    }
    Write-Host 'Installing Tailscale...'
    & $winget.Source install --id Tailscale.Tailscale -e --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) {
        throw "winget Tailscale install failed with exit code $LASTEXITCODE"
    }
    Start-Sleep -Seconds 2
    $tailscale = Find-Tailscale
    if (-not $tailscale) {
        throw 'Tailscale installation completed but tailscale.exe was not found.'
    }
}
Write-Host "tailscale resolved: $tailscale"

function Get-TailscaleState {
    try {
        $raw = & $tailscale status --json 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
        return ($raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

$state = Get-TailscaleState
if ($null -eq $state -or [string]$state.BackendState -ne 'Running') {
    Write-Host ''
    Write-Host 'TAILSCALE_AUTH_REQUIRED'
    Write-Host 'A browser window may open. Sign in to Tailscale and approve this Windows PC.'
    Write-Host ''
    & $tailscale up
    if ($LASTEXITCODE -ne 0) {
        throw "tailscale up failed with exit code $LASTEXITCODE"
    }
    Start-Sleep -Seconds 2
    $state = Get-TailscaleState
    if ($null -eq $state -or [string]$state.BackendState -ne 'Running') {
        throw 'Tailscale is not authenticated/running after tailscale up.'
    }
}

$dnsName = [string]$state.Self.DNSName
if ([string]::IsNullOrWhiteSpace($dnsName)) {
    throw 'Tailscale DNSName is unavailable. MagicDNS may not be enabled.'
}
$dnsName = $dnsName.TrimEnd('.')

Write-Host ''
Write-Host 'Enabling persistent public Funnel to FactoryBridge gateway...'
& $tailscale funnel --bg --yes http://127.0.0.1:8787
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'FUNNEL_APPROVAL_MAY_BE_REQUIRED'
    Write-Host 'Tailscale may open a browser page asking you to enable Funnel for this tailnet.'
    & $tailscale funnel --bg http://127.0.0.1:8787
    if ($LASTEXITCODE -ne 0) {
        throw "tailscale funnel failed with exit code $LASTEXITCODE"
    }
}

$url = 'https://' + $dnsName
[System.IO.File]::WriteAllText($stableUrlPath, $url + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText((Join-Path $brainDir 'GATEWAY_STABLE_URL.txt'), $url + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

# External self-probe through the public Funnel with hard curl timeouts.
$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl) {
    throw 'curl.exe is required for the external Funnel probe.'
}
$probe = [ordered]@{
    url = $url
    checkedAt = (Get-Date).ToString('o')
    health = $false
    checkpoint = $false
    showcase = $false
    privateUnauthorized = $false
}

function Curl-Status([string]$Target) {
    $code = & $curl.Source -sS --connect-timeout 5 --max-time 10 -o NUL -w '%{http_code}' $Target
    if ($LASTEXITCODE -ne 0) { return 0 }
    $parsed = 0
    [void][int]::TryParse(($code | Out-String).Trim(), [ref]$parsed)
    return $parsed
}

$probe.health = (Curl-Status ($url + '/public/health')) -eq 200
$probe.checkpoint = (Curl-Status ($url + '/public/checkpoint')) -eq 200
$probe.showcase = (Curl-Status ($url + '/public/showcase')) -eq 200
$probe.privateUnauthorized = (Curl-Status ($url + '/api/runtime/status')) -eq 401
$probe.ok = $probe.health -and $probe.checkpoint -and $probe.showcase -and $probe.privateUnauthorized

$probeJson = $probe | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText((Join-Path $gatewayRoot 'stable-probe.json'), $probeJson + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText((Join-Path $brainDir 'GATEWAY_STABLE_PROBE.json'), $probeJson + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

if (-not $probe.ok) {
    throw 'Stable Funnel started, but one or more external probe checks failed.'
}

Write-Host ''
Write-Host 'FACTORY_GATEWAY_STABLE_READY'
Write-Host "URL=$url"
Write-Host 'External probe: PASS'
Write-Host 'Funnel is persistent across reboot while Tailscale remains configured.'
