[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$cfg = Get-Content (Join-Path $env:LOCALAPPDATA 'FactoryBridge\config.json') -Raw | ConvertFrom-Json
$repo = [string]$cfg.dispatcherWorkDir
$wt = Join-Path $env:LOCALAPPDATA 'FactoryNode\workspaces\FB-P0-apply2'

& git.exe -C $repo fetch origin feat/factorybridge-production-closure-p0
if ($LASTEXITCODE -ne 0) { throw 'PHASE=fetch cause=git_fetch_failed' }
if (Test-Path $wt) { throw 'PHASE=worktree cause=workspace_exists' }
& git.exe -C $repo worktree add --detach $wt origin/feat/factorybridge-production-closure-p0
if ($LASTEXITCODE -ne 0) { throw 'PHASE=worktree cause=create_failed' }

$p = Join-Path $wt 'factory_bridge\mission_runtime.go'
$s = [IO.File]::ReadAllText($p)
$s = $s.Replace('return fail("System command failed; stdout, stderr and risk classification were retained locally.", command)', 'return fail(systemCommandFailureSummary(command), command)')
$s = $s.Replace('cp.Summary = command.Output', 'cp.Summary = sanitizeRemoteText(command.Output)')
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'factory_bridge\github_bus.go'
$s = [IO.File]::ReadAllText($p)
$s = $s.Replace('Summary:       compact(cp.Summary, 800),', 'Summary:       remoteCheckpointSummary(cp.Summary),')
$s = $s.Replace("envelope.Protocol = githubBusProtocol`r`n", "envelope.Protocol = githubBusProtocol`r`n`t envelope.Summary = sanitizeRemoteText(envelope.Summary)`r`n")
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'factory_bridge\main.go'
$s = [IO.File]::ReadAllText($p)
$s = $s.Replace('const bridgeVersion = "0.14.0"', 'const bridgeVersion = "0.15.0"')
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

Push-Location (Join-Path $wt 'factory_bridge')
try {
    Write-Output 'PHASE=gofmt'
    & gofmt.exe -w mission_runtime.go github_bus.go main.go system_command_diagnostics.go system_command_diagnostics_test.go
    if ($LASTEXITCODE -ne 0) { throw 'PHASE=gofmt cause=failed' }
    Write-Output 'PHASE=test'
    & go.exe test ./...
    if ($LASTEXITCODE -ne 0) { throw 'PHASE=test cause=go_test_failed' }
}
finally { Pop-Location }

Write-Output 'PHASE=commit'
& git.exe -C $wt add factory_bridge scripts/apply-factorybridge-p0.ps1
$dirty = (& git.exe -C $wt status --porcelain) -join "`n"
if ($dirty) {
    & git.exe -C $wt commit -m 'feat(factory-bridge): close P0 diagnostics and remote redaction'
    if ($LASTEXITCODE -ne 0) { throw 'PHASE=commit cause=git_commit_failed' }
}
& git.exe -C $wt push origin HEAD:feat/factorybridge-production-closure-p0
if ($LASTEXITCODE -ne 0) { throw 'PHASE=push cause=git_push_failed' }
$head = ((& git.exe -C $wt rev-parse HEAD) -join '').Trim()
Write-Output ('FACTORYBRIDGE_P0_IMPLEMENTATION=PASS commit=' + $head)
