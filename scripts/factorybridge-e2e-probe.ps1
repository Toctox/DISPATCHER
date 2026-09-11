param(
    [ValidateSet('success','failure','spawn-timeout','inspect-tree')]
    [string]$Mode = 'success'
)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'FactoryBridge\state'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$pidPath = Join-Path $stateDir 'e2e-child.pid'

switch ($Mode) {
    'success' {
        Write-Output 'PHASE=e2e_success'
        Write-Output 'FACTORYBRIDGE_E2E_SUCCESS'
        exit 0
    }
    'failure' {
        Write-Output 'PHASE=e2e_failure'
        [Console]::Error.WriteLine('synthetic probe failed Password=CANARY_FACTORYBRIDGE_E2E_20260910 token=CANARY_TOKEN_20260910')
        exit 17
    }
    'spawn-timeout' {
        Write-Output 'PHASE=e2e_spawn_timeout'
        $child = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoLogo','-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 300' -PassThru -WindowStyle Hidden
        Set-Content -LiteralPath $pidPath -Value ([string]$child.Id) -Encoding ASCII
        Write-Output ('CHILD_PID=' + $child.Id)
        Start-Sleep -Seconds 300
        exit 0
    }
    'inspect-tree' {
        Write-Output 'PHASE=e2e_inspect_tree'
        if (-not (Test-Path -LiteralPath $pidPath -PathType Leaf)) {
            [Console]::Error.WriteLine('child pid evidence missing')
            exit 19
        }
        $childId = [int](Get-Content -LiteralPath $pidPath -Raw)
        $alive = $null -ne (Get-Process -Id $childId -ErrorAction SilentlyContinue)
        if ($alive) {
            [Console]::Error.WriteLine('descendant process is still alive')
            exit 18
        }
        Write-Output ('PROCESS_TREE_CANCELLED pid=' + $childId)
        exit 0
    }
}
