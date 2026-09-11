$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $repoRoot 'local_agent\agent.py'
if (-not (Test-Path -LiteralPath $source)) { throw "local_agent/agent.py not found in exact-commit workspace" }

$base = Join-Path $env:LOCALAPPDATA 'FactoryNode\local-agent'
$logs = Join-Path $base 'logs'
New-Item -ItemType Directory -Path $base -Force | Out-Null
New-Item -ItemType Directory -Path $logs -Force | Out-Null

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) { throw 'python.exe was not found on PATH' }
$pythonPath = $python.Source

# Stop a previous instance installed by this agent only.
$pidFile = Join-Path $base 'agent.pid'
if (Test-Path -LiteralPath $pidFile) {
    $oldPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($oldPidText -match '^\d+$') {
        $oldPid = [int]$oldPidText
        $p = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($p) {
            Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
        }
    }
}

Copy-Item -LiteralPath $source -Destination (Join-Path $base 'agent.py') -Force

$startCmd = Join-Path $base 'start.cmd'
$startBody = @"
@echo off
cd /d "$base"
start "Factory Local Agent" /min "$pythonPath" "$base\agent.py"
"@
Set-Content -LiteralPath $startCmd -Value $startBody -Encoding ASCII

$stopPs1 = Join-Path $base 'stop.ps1'
$stopBody = @"
`$ErrorActionPreference='SilentlyContinue'
`$pidFile='$($pidFile.Replace("'", "''"))'
if(Test-Path -LiteralPath `$pidFile){
  `$v=(Get-Content -LiteralPath `$pidFile -Raw).Trim()
  if(`$v -match '^\d+`$'){
    taskkill.exe /PID `$v /T /F | Out-Null
  }
  Remove-Item -LiteralPath `$pidFile -Force -ErrorAction SilentlyContinue
}
"@
Set-Content -LiteralPath $stopPs1 -Value $stopBody -Encoding UTF8

$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
New-Item -ItemType Directory -Path $startup -Force | Out-Null
$startupCmd = Join-Path $startup 'FactoryNode-Local-Agent.cmd'
$startupBody = "@echo off`r`ncall `"$startCmd`"`r`n"
Set-Content -LiteralPath $startupCmd -Value $startupBody -Encoding ASCII

Start-Process -FilePath $pythonPath -ArgumentList @((Join-Path $base 'agent.py')) -WorkingDirectory $base -WindowStyle Hidden

$health = $null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8765/health' -TimeoutSec 2
        if ($health.ok) { break }
    } catch { }
}
if (-not $health -or -not $health.ok) { throw 'Local agent did not become healthy on 127.0.0.1:8765' }

[ordered]@{
    status = 'INSTALLED'
    service = $health.service
    version = $health.version
    pid = $health.pid
    bind = '127.0.0.1:8765'
    mode = $health.mode
    installRoot = $base
    tokenPath = (Join-Path $base 'token.txt')
    startup = $startupCmd
    python = $pythonPath
} | ConvertTo-Json -Compress
