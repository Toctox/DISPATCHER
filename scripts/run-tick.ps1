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

function Invoke-FactoryBridgeUpdateIfRequested {
    param([string]$ProjectPath)

    $bridgeConfigPath = Join-Path $env:LOCALAPPDATA 'FactoryBridge\config.json'
    if (-not (Test-Path -LiteralPath $bridgeConfigPath -PathType Leaf)) {
        return $false
    }
    $bridgeConfig = Get-Content -LiteralPath $bridgeConfigPath -Raw | ConvertFrom-Json
    $bridgeRoot = [string]$bridgeConfig.bridgeRoot
    if ([string]::IsNullOrWhiteSpace($bridgeRoot)) {
        return $false
    }
    $request = Join-Path $bridgeRoot '00_STATUS\factory-bridge-update.request.json'
    if (-not (Test-Path -LiteralPath $request -PathType Leaf)) {
        return $false
    }

    $updater = Join-Path $ProjectPath 'scripts\factory-bridge-update.ps1'
    if (-not (Test-Path -LiteralPath $updater -PathType Leaf)) {
        throw "FactoryBridge updater not found: $updater"
    }

    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $updater
    if ($LASTEXITCODE -ne 0) {
        throw "FactoryBridge updater failed with exit code $LASTEXITCODE."
    }
    Remove-Item -LiteralPath $request -Force
    [Console]::Error.WriteLine('{"kind":"FACTORY_BRIDGE_UPDATE","status":"STAGED_AND_APPLY_SCHEDULED"}')
    return $true
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

function Write-ChatGptComposerFocusDiagnostic {
    param([string]$ProjectPath)
    $helper = Join-Path $ProjectPath 'scripts\chatgpt-focus-composer.ps1'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) {
        [Console]::Error.WriteLine('{"kind":"CHATGPT_DESKTOP_COMPOSER_FOCUS_DIAGNOSTIC","status":"SCRIPT_NOT_FOUND"}')
        return
    }

    try {
        $process = Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 } |
            Sort-Object StartTime -Descending |
            Select-Object -First 1
        if ($null -eq $process) {
            [Console]::Error.WriteLine('{"kind":"CHATGPT_DESKTOP_COMPOSER_FOCUS_DIAGNOSTIC","status":"WINDOW_NOT_FOUND"}')
            return
        }

        $output = & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $helper -Hwnd ([Int64]$process.MainWindowHandle) 2>&1
        if ($LASTEXITCODE -eq 0) {
            $payload = [ordered]@{
                kind = 'CHATGPT_DESKTOP_COMPOSER_FOCUS_DIAGNOSTIC'
                status = 'OK'
                hwnd = [Int64]$process.MainWindowHandle
                helper = ($output -join "`n")
            }
            [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
        }
        else {
            $payload = [ordered]@{
                kind = 'CHATGPT_DESKTOP_COMPOSER_FOCUS_DIAGNOSTIC'
                status = 'FAILED'
                hwnd = [Int64]$process.MainWindowHandle
                detail = ($output -join "`n")
            }
            [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
        }
    }
    catch {
        $payload = [ordered]@{
            kind = 'CHATGPT_DESKTOP_COMPOSER_FOCUS_DIAGNOSTIC'
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
        if (Invoke-FactoryBridgeUpdateIfRequested -ProjectPath $projectPath) {
            exit 0
        }
        Ensure-DispatcherScheduledTask -ProjectPath $projectPath
    }
    else {
        Write-ChatGptDesktopCaptureDiagnostic -ProjectPath $projectPath
        Write-ChatGptComposerFocusDiagnostic -ProjectPath $projectPath
    }

    & $pythonPath @dispatcherArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
