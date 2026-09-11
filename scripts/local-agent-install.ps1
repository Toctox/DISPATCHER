$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $repoRoot 'local_agent\agent.py'
if (-not (Test-Path -LiteralPath $source)) { throw "local_agent/agent.py not found in exact-commit workspace" }

$base = Join-Path $env:LOCALAPPDATA 'FactoryNode\local-agent'
$logs = Join-Path $base 'logs'
$taskName = 'FactoryNode Local Agent'
$agentPort = 18765
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

# Stop only our previously registered task/process.
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
}
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

$logPath = Join-Path $logs 'agent.log'
$runner = Join-Path $base 'run-agent.ps1'
$escapedPython = $pythonPath.Replace("'", "''")
$escapedAgent = $agentPath.Replace("'", "''")
$escapedBase = $base.Replace("'", "''")
$escapedLog = $logPath.Replace("'", "''")
$runnerBody = @"
`$ErrorActionPreference='Continue'
`$env:FACTORY_LOCAL_AGENT_PORT='$agentPort'
Set-Location -LiteralPath '$escapedBase'
"LOCAL_AGENT_START `$([DateTime]::UtcNow.ToString('o')) python=$escapedPython port=$agentPort" | Out-File -LiteralPath '$escapedLog' -Append -Encoding utf8
& '$escapedPython' '$escapedAgent' *>> '$escapedLog'
`$code=`$LASTEXITCODE
"LOCAL_AGENT_EXIT `$([DateTime]::UtcNow.ToString('o')) code=`$code" | Out-File -LiteralPath '$escapedLog' -Append -Encoding utf8
exit `$code
"@
Set-Content -LiteralPath $runner -Value $runnerBody -Encoding UTF8

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

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
    $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    $tail = ''
    if (Test-Path -LiteralPath $logPath) {
        $tail = (Get-Content -LiteralPath $logPath -Tail 30 -ErrorAction SilentlyContinue) -join "`n"
    }
    throw "Local agent did not become healthy on 127.0.0.1:$agentPort. LastTaskResult=$($taskInfo.LastTaskResult). Log=$logPath`n$tail"
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
    task = $taskName
    log = $logPath
    python = $pythonPath
} | ConvertTo-Json -Compress
