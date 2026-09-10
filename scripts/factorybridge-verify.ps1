param([string]$TestPattern = '.')
$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..\factory_bridge')
try {
    Write-Output 'PHASE=test'
    & go.exe test './...' '-run' $TestPattern
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output 'PHASE=vet'
    & go.exe vet './...'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Output 'FACTORYBRIDGE_VERIFY=PASS'
} finally { Pop-Location }
