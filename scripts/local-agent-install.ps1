$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $repoRoot 'local_agent\agent.py'
if (-not (Test-Path -LiteralPath $source)) { throw "local_agent/agent.py not found in exact-commit workspace" }

$base = Join-Path $env:LOCALAPPDATA 'FactoryNode\local-agent'
$logs = Join-Path $base 'logs'
$taskName = 'FactoryNode Local Agent'
$agentPort = 18765
$startupDir = [Environment]::GetFolderPath('Startup')
$startupVbs = Join-Path $startupDir 'FactoryNode-Local-Agent.vbs'
$legacyStartupCmd = Join-Path $startupDir 'FactoryNode-Local-Agent.cmd'
New-Item -ItemType Directory -Path $base -Force | Out-Null
New-Item -ItemType Directory -Path $logs -Force | Out-Null

# Resolve a real CPython interpreter. Windows App Execution Alias may expose a
# python.exe shim that exists but cannot execute Python, so every candidate is
# actively qualified before use.
$pythonPath = $null
$candidates = @()
foreach ($name in @('python.exe', 'python3.exe')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $candidates += $cmd.Source }
}
$py = Get-Command py.exe -ErrorAction SilentlyContinue
if ($py -and $py.Source) {
    try {
        $resolved = (& $py.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1).Trim()
        if ($resolved) { $candidates = @($resolved) + $candidates }
    } catch { }
}
foreach ($candidate in ($candidates | Select-Object -Unique)) {
    try {
        $probe = & $candidate -c "import sys; print(sys.version_info[0]); print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe.Count -ge 2 -and $probe[0].Trim() -eq '3') {
            $pythonPath = $probe[1].Trim()
            break
        }
    } catch { }
}
if (-not $pythonPath) {
    throw 'No usable Python 3 interpreter was found. Disable the Microsoft Store python alias or install Python 3.'
}

$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) {
    throw "pythonw.exe was not found next to qualified interpreter: $pythonPath"
}

# Retire the legacy scheduled-task launcher and any old visible Startup CMD.
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}
if (Test-Path -LiteralPath $legacyStartupCmd) {
    Remove-Item -LiteralPath $legacyStartupCmd -Force -ErrorAction SilentlyContinue
}

# Stop only the previously installed Local Agent process recorded by its PID file.
$pidFile = Join-Path $base 'agent.pid'
if (Test-Path -LiteralPath $pidFile) {
    $oldPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($oldPidText -match '^\d+$') {
        taskkill.exe /PID ([int]$oldPidText) /T /F 2>$null | Out-Null
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

Copy-Item -LiteralPath $source -Destination (Join-Path $base 'agent.py') -Force
$agentPath = Join-Path $base 'agent.py'
& $pythonPath -m py_compile $agentPath
if ($LASTEXITCODE -ne 0) { throw 'Local agent Python compile validation failed' }

# Persist a completely silent per-user launcher. pythonw.exe prevents a console
# window and the VBS launcher starts hidden at logon without PowerShell/CMD pop-ups.
$escapedBaseVbs = $base.Replace('"', '""')
$escapedPythonwVbs = $pythonwPath.Replace('"', '""')
$escapedAgentVbs = $agentPath.Replace('"', '""')
$vbsBody = @"
Set sh = CreateObject("WScript.Shell")
sh.Environment("PROCESS")("FACTORY_LOCAL_AGENT_PORT") = "$agentPort"
base = "$escapedBaseVbs"
pyw = "$escapedPythonwVbs"
agent = "$escapedAgentVbs"
cmd = Chr(34) & pyw & Chr(34) & " " & Chr(34) & agent & Chr(34)
sh.CurrentDirectory = base
sh.Run cmd, 0, False
"@
[System.IO.File]::WriteAllText($startupVbs, $vbsBody, (New-Object System.Text.UTF8Encoding($false)))

Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $startupVbs + '"') -WindowStyle Hidden

$health = $null
$healthUri = "http://127.0.0.1:$agentPort/health"
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 250
    try {
        $health = Invoke-RestMethod -Method Get -Uri $healthUri -TimeoutSec 2
        if ($health.ok) { break }
    } catch { }
}
if (-not $health -or -not $health.ok) {
    throw "Local agent did not become healthy on 127.0.0.1:$agentPort after launching $startupVbs"
}

Set-Content -LiteralPath (Join-Path $base 'port.txt') -Value ([string]$agentPort) -Encoding ASCII

[ordered]@{
    status = 'INSTALLED'
    service = $health.service
    version = $health.version
    pid = $health.pid
    bind = "127.0.0.1:$agentPort"
    mode = $health.mode
    installRoot = $base
    tokenPath = (Join-Path $base 'token.txt')
    portPath = (Join-Path $base 'port.txt')
    startup = $startupVbs
    python = $pythonPath
    pythonw = $pythonwPath
} | ConvertTo-Json -Compress
