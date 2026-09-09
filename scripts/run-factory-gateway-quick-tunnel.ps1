param(
    [string]$CloudflaredPath = '',
    [int]$WaitSeconds = 45
)

$ErrorActionPreference = 'Stop'

$runtimeRoot = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$gatewayRoot = Join-Path $runtimeRoot 'gateway'
$configPath = Join-Path $runtimeRoot 'config.json'
$stdoutPath = Join-Path $gatewayRoot 'cloudflared.stdout.log'
$stderrPath = Join-Path $gatewayRoot 'cloudflared.stderr.log'
$pidPath = Join-Path $gatewayRoot 'cloudflared.pid'
$urlPath = Join-Path $gatewayRoot 'public-url.txt'

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

function Refresh-ProcessPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = @($machine, $user) -join ';'
}

function Find-Cloudflared {
    param([string]$Preferred = '')

    if (-not [string]::IsNullOrWhiteSpace($Preferred) -and (Test-Path -LiteralPath $Preferred)) {
        return (Resolve-Path -LiteralPath $Preferred).Path
    }

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

$cloudflared = Find-Cloudflared -Preferred $CloudflaredPath
if (-not $cloudflared) {
    throw 'cloudflared.exe not found. Run SETUP_FACTORY_GATEWAY.cmd first.'
}
Write-Host "cloudflared=$cloudflared"

# If our previously launched tunnel is still alive, just republish its URL.
if (Test-Path -LiteralPath $pidPath) {
    $existingPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $pidPath -Raw).Trim(), [ref]$existingPid)
    if ($existingPid -gt 0) {
        $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
        if ($existing) {
            if (Test-Path -LiteralPath $urlPath) {
                $url = (Get-Content -LiteralPath $urlPath -Raw).Trim()
                if ($url) {
                    [System.IO.File]::WriteAllText((Join-Path $brainDir 'GATEWAY_URL.txt'), $url + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
                    Write-Host "GATEWAY_URL=$url"
                }
            }
            exit 0
        }
    }
}

Remove-Item -LiteralPath $stdoutPath,$stderrPath -Force -ErrorAction SilentlyContinue

$proc = Start-Process -FilePath $cloudflared `
    -ArgumentList @('tunnel','--url','http://127.0.0.1:8787','--no-autoupdate') `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

[System.IO.File]::WriteAllText($pidPath, [string]$proc.Id + "`r`n", (New-Object System.Text.UTF8Encoding($false)))

$deadline = (Get-Date).AddSeconds($WaitSeconds)
$url = $null
$regex = 'https://[a-z0-9-]+\.trycloudflare\.com'
while ((Get-Date) -lt $deadline -and -not $url) {
    Start-Sleep -Milliseconds 500
    foreach ($log in @($stdoutPath, $stderrPath)) {
        if (-not (Test-Path -LiteralPath $log)) { continue }
        $text = Get-Content -LiteralPath $log -Raw -ErrorAction SilentlyContinue
        if ($text -match $regex) {
            $url = $Matches[0]
            break
        }
    }
    if ($proc.HasExited) {
        break
    }
}

if (-not $url) {
    $tail = ''
    if (Test-Path -LiteralPath $stderrPath) {
        $tail = (Get-Content -LiteralPath $stderrPath -Tail 30 -ErrorAction SilentlyContinue) -join "`n"
    }
    throw "Cloudflare quick tunnel did not publish a URL. stderr tail:`n$tail"
}

[System.IO.File]::WriteAllText($urlPath, $url + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText((Join-Path $brainDir 'GATEWAY_URL.txt'), $url + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "GATEWAY_URL=$url"
Write-Host "Tunnel PID=$($proc.Id)"
