[CmdletBinding()]
param(
    [string]$TaskName = 'ProjectFactory Dispatcher Tick',
    [switch]$Replace
)

$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$dispatcherPath = Join-Path $projectPath 'dispatcher.py'
$configPath = Join-Path $projectPath 'config.json'

foreach ($requiredPath in @($pythonPath, $dispatcherPath, $configPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file not found: $requiredPath"
    }
}

$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask -and -not $Replace) {
    throw "Task '$TaskName' already exists. Re-run with -Replace to overwrite it."
}

$quotedDispatcher = '"' + $dispatcherPath + '"'
$quotedConfig = '"' + $configPath + '"'
$actionArguments = "$quotedDispatcher --tick --config $quotedConfig"
$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument $actionArguments `
    -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1)
$trigger.Repetition.StopAtDurationEnd = $false
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'One short mechanical PROJECT FACTORY dispatcher tick per minute.'

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
