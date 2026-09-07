[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$dispatcherPath = Join-Path $projectPath 'dispatcher.py'
$configPath = Join-Path $projectPath 'config.json'

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Virtual environment not found: $pythonPath"
}
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Configuration not found: $configPath"
}

function Ensure-DispatcherScheduledTask {
    param([string]$ProjectPath)
    $taskName = 'ProjectFactory Dispatcher Tick'
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        if ([string]$task.State -eq 'Disabled') {
            Enable-ScheduledTask -TaskName $taskName | Out-Null
        }
        return
    }

    $installer = Join-Path $ProjectPath 'scripts\install-task-scheduler.ps1'
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
        throw "Dispatcher scheduler installer not found: $installer"
    }
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) {
        throw 'Failed to install ProjectFactory Dispatcher Tick scheduled task.'
    }
    [Console]::Error.WriteLine('{"kind":"DISPATCHER_SCHEDULER","status":"INSTALLED","intervalSeconds":60}')
}

function Write-ChatGptDesktopCaptureDiagnostic {
    param([string]$ProjectPath)
    $capture = Join-Path $ProjectPath 'scripts\chatgpt-desktop-capture.ps1'
    if (-not (Test-Path -LiteralPath $capture -PathType Leaf)) {
        [Console]::Error.WriteLine('{"kind":"CHATGPT_DESKTOP_CAPTURE","status":"SCRIPT_NOT_FOUND"}')
        return
    }
    try {
        & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $capture
        if ($LASTEXITCODE -ne 0) {
            [Console]::Error.WriteLine('{"kind":"CHATGPT_DESKTOP_CAPTURE","status":"FAILED"}')
        }
    }
    catch {
        $payload = [ordered]@{
            kind = 'CHATGPT_DESKTOP_CAPTURE'
            status = 'FAILED'
            error = $_.Exception.Message
        }
        [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
    }
}

$dispatcherArguments = @($dispatcherPath, '--tick', '--config', $configPath)
if ($DryRun) {
    $dispatcherArguments += '--dry-run'
}

Push-Location -LiteralPath $projectPath
try {
    if (-not $DryRun) {
        Ensure-DispatcherScheduledTask -ProjectPath $projectPath
    }
    else {
        Write-ChatGptDesktopCaptureDiagnostic -ProjectPath $projectPath
    }

    & $pythonPath @dispatcherArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
