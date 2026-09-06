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

function Write-ChatGptBridgeDiagnostic {
    param([string]$ProjectPath,[string]$ConfigPath)
    try {
        $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
        $stateSetting = [string]$config.stateDirectory
        if ([string]::IsNullOrWhiteSpace($stateSetting)) { $stateSetting = 'state' }
        if ([System.IO.Path]::IsPathRooted($stateSetting)) { $statePath = [System.IO.Path]::GetFullPath($stateSetting) }
        else { $statePath = [System.IO.Path]::GetFullPath((Join-Path $ProjectPath $stateSetting)) }
        $reservationPath = Join-Path $ProjectPath 'state\chatgpt-bridge\reservation.json'
        if (-not (Test-Path -LiteralPath $reservationPath -PathType Leaf)) {
            [Console]::Error.WriteLine((([ordered]@{kind='CHATGPT_BRIDGE_DIAGNOSTIC';reservation='NONE'}) | ConvertTo-Json -Compress)); return
        }
        $reservation = Get-Content -LiteralPath $reservationPath -Raw | ConvertFrom-Json
        $reservationId = [string]$reservation.reservationId
        if ([string]::IsNullOrWhiteSpace($reservationId)) {
            [Console]::Error.WriteLine((([ordered]@{kind='CHATGPT_BRIDGE_DIAGNOSTIC';reservation='NONE'}) | ConvertTo-Json -Compress)); return
        }
        $dispatchId=$null; $attemptId=$null; $workerState=$null; $errorCode=$null
        $sendAttempted=$null; $operationalSuccess=$null; $needsReconciliation=$null; $launchMatched=$false
        $runsPath = Join-Path $statePath 'chatgpt-runs'
        if (Test-Path -LiteralPath $runsPath -PathType Container) {
            foreach ($launchPath in Get-ChildItem -LiteralPath $runsPath -Filter 'launch.json' -File -Recurse -ErrorAction SilentlyContinue) {
                try {
                    $launch = Get-Content -LiteralPath $launchPath.FullName -Raw | ConvertFrom-Json
                    if ([string]$launch.reservationId -ne $reservationId) { continue }
                    $launchMatched=$true; $dispatchId=[string]$launch.execution.dispatchId; $attemptId=[string]$launch.execution.attemptId
                    $statusPath=Join-Path $launchPath.Directory.FullName 'status.json'
                    if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
                        $status=Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
                        $workerState=[string]$status.state; $errorCode=[string]$status.errorCode
                        if ($null -ne $status.sendAttempted) {$sendAttempted=[bool]$status.sendAttempted}
                        if ($null -ne $status.operationalSuccess) {$operationalSuccess=[bool]$status.operationalSuccess}
                        if ($null -ne $status.needsReconciliation) {$needsReconciliation=[bool]$status.needsReconciliation}
                    }
                    break
                } catch {}
            }
        }
        $tabId=$null; if ($null -ne $reservation.binding -and $null -ne $reservation.binding.tabId) {$tabId=$reservation.binding.tabId}
        $payload=[ordered]@{kind='CHATGPT_BRIDGE_DIAGNOSTIC';reservation='ACTIVE';reservationId=$reservationId;phase=[string]$reservation.phase;tabId=$tabId;launchMatched=$launchMatched;dispatchId=$dispatchId;attemptId=$attemptId;workerState=$workerState;errorCode=$errorCode;sendAttempted=$sendAttempted;operationalSuccess=$operationalSuccess;needsReconciliation=$needsReconciliation}
        [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
    } catch {
        [Console]::Error.WriteLine((([ordered]@{kind='CHATGPT_BRIDGE_DIAGNOSTIC';reservation='UNKNOWN';diagnosticError=$_.Exception.GetType().Name}) | ConvertTo-Json -Compress))
    }
}

function Invoke-ExactBlockedSmokeRecovery {
    param([string]$PythonPath,[string]$ConfigPath)
    $args=@('-m','factory_dispatcher.chatgpt_extension_recover','--config',$ConfigPath,'--dispatch-id','D-SMOKE-CHATGPT-0008','--attempt-id','D-SMOKE-CHATGPT-0008-A001')
    $output=(& $PythonPath @args | Out-String).Trim(); $code=$LASTEXITCODE
    $payload=[ordered]@{kind='CHATGPT_RECOVERY_ATTEMPT';dispatchId='D-SMOKE-CHATGPT-0008';attemptId='D-SMOKE-CHATGPT-0008-A001';exitCode=$code;output=$output}
    [Console]::Error.WriteLine(($payload | ConvertTo-Json -Compress))
}

$dispatcherArguments=@($dispatcherPath,'--tick','--config',$configPath)
if ($DryRun) {$dispatcherArguments+='--dry-run'}

Push-Location -LiteralPath $projectPath
try {
    if (-not $DryRun) { Invoke-ExactBlockedSmokeRecovery -PythonPath $pythonPath -ConfigPath $configPath }
    Write-ChatGptBridgeDiagnostic -ProjectPath $projectPath -ConfigPath $configPath
    & $pythonPath @dispatcherArguments
    exit $LASTEXITCODE
}
finally { Pop-Location }
