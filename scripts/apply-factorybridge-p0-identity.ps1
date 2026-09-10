[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$cfg = Get-Content (Join-Path $env:LOCALAPPDATA 'FactoryBridge\config.json') -Raw | ConvertFrom-Json
$repo = [string]$cfg.dispatcherWorkDir
$wt = Join-Path $env:LOCALAPPDATA 'FactoryNode\workspaces\FB-P0-identity'
$branch = 'feat/factorybridge-production-closure-p0-identity-script'

Write-Output 'PHASE=fetch'
& git.exe -C $repo fetch origin $branch
if ($LASTEXITCODE -ne 0) { throw 'PHASE=fetch cause=git_fetch_failed' }
if (Test-Path $wt) { throw 'PHASE=worktree cause=workspace_exists' }
& git.exe -C $repo worktree add --detach $wt ('origin/' + $branch)
if ($LASTEXITCODE -ne 0) { throw 'PHASE=worktree cause=create_failed' }

Write-Output 'PHASE=patch'
$p = Join-Path $wt 'factory_bridge\system_command.go'
$s = [IO.File]::ReadAllText($p)
$s = $s.Replace('res.Output = compact(strings.TrimSpace(stdout), 1200)', 'res.Output = tailCompact(sanitizeRemoteText(strings.TrimSpace(stdout)), 1200)')
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'factory_bridge\github_bus.go'
$s = [IO.File]::ReadAllText($p)
$s = $s.Replace('BridgeVersion string `json:"bridgeVersion,omitempty"`', "BridgeVersion string `json:\"bridgeVersion,omitempty\"``r`n`tSourceCommit string `json:\"sourceCommit,omitempty\"``r`n`tBinarySHA256 string `json:\"binarySha256,omitempty\"``r`n`tProtocolVersion string `json:\"protocolVersion,omitempty\"``r`n`tPolicyVersion string `json:\"policyVersion,omitempty\"`")
$s = $s.Replace('envelope.Summary = sanitizeRemoteText(envelope.Summary)', "envelope.Summary = sanitizeRemoteText(envelope.Summary)`r`n`tenvelope.SourceCommit = runtimeSourceCommit()`r`n`tenvelope.BinarySHA256 = runningBinarySHA256()`r`n`tenvelope.ProtocolVersion = factoryBusProtocolVersion`r`n`tenvelope.PolicyVersion = factoryRiskPolicyVersion")
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'factory_bridge\gateway.go'
$s = [IO.File]::ReadAllText($p)
$s = $s.Replace('BridgeVersion     string `json:"bridgeVersion"`', "BridgeVersion     string `json:\"bridgeVersion\"``r`n`tSourceCommit      string `json:\"sourceCommit\"``r`n`tBinarySHA256      string `json:\"binarySha256\"``r`n`tProtocolVersion   string `json:\"protocolVersion\"``r`n`tPolicyVersion     string `json:\"policyVersion\"`")
$s = $s.Replace('BridgeVersion:     bridgeVersion,', "BridgeVersion:     bridgeVersion,`r`n`t`tSourceCommit:      runtimeSourceCommit(),`r`n`t`tBinarySHA256:      runningBinarySHA256(),`r`n`t`tProtocolVersion:   factoryBusProtocolVersion,`r`n`t`tPolicyVersion:     factoryRiskPolicyVersion,")
$s = $s.Replace('"bridgeVersion":  status.BridgeVersion,', "\"bridgeVersion\":  status.BridgeVersion,`r`n`t`t`t\"sourceCommit\":   status.SourceCommit,`r`n`t`t`t\"binarySha256\":   status.BinarySHA256,`r`n`t`t`t\"protocolVersion\": status.ProtocolVersion,`r`n`t`t`t\"policyVersion\":  status.PolicyVersion,")
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'scripts\factory-bridge-autoupdate.ps1'
$s = [IO.File]::ReadAllText($p)
$s = $s.Replace("& `$go.Source build -trimpath -ldflags '-s -w' -o `$nextExe .", "& `$go.Source build -trimpath -ldflags ('-s -w -X main.buildSourceCommit=' + `$target) -o `$nextExe .")
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

Write-Output 'PHASE=format-test'
Push-Location (Join-Path $wt 'factory_bridge')
try {
    & gofmt.exe -w runtime_identity.go runtime_identity_test.go system_command_hardening.go system_command.go github_bus.go gateway.go
    if ($LASTEXITCODE -ne 0) { throw 'PHASE=gofmt cause=failed' }
    & go.exe test ./...
    if ($LASTEXITCODE -ne 0) { throw 'PHASE=test cause=go_test_failed' }
}
finally { Pop-Location }

Write-Output 'PHASE=powershell-parse'
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $wt 'scripts\factory-bridge-autoupdate.ps1'), [ref]$null, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw ('PHASE=powershell-parse cause=' + $parseErrors[0].Message) }

Write-Output 'PHASE=commit'
& git.exe -C $wt add factory_bridge scripts/factory-bridge-autoupdate.ps1 scripts/apply-factorybridge-p0-identity.ps1
& git.exe -C $wt commit -m 'security(factory-bridge): bind runtime identity and close script bypass'
if ($LASTEXITCODE -ne 0) { throw 'PHASE=commit cause=git_commit_failed' }
& git.exe -C $wt push origin HEAD:$branch
if ($LASTEXITCODE -ne 0) { throw 'PHASE=push cause=git_push_failed' }
$head = ((& git.exe -C $wt rev-parse HEAD) -join '').Trim()
Write-Output ('FACTORYBRIDGE_P0_IDENTITY=PASS commit=' + $head)
