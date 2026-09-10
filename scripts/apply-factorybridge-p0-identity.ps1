[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$cfg = Get-Content (Join-Path $env:LOCALAPPDATA 'FactoryBridge\config.json') -Raw | ConvertFrom-Json
$repo = [string]$cfg.dispatcherWorkDir
$wt = Join-Path $env:LOCALAPPDATA 'FactoryNode\workspaces\FB-P0-identity'
$branch = 'feat/factorybridge-production-closure-p0-identity-script'

function Replace-Required {
    param(
        [string]$Text,
        [string]$Old,
        [string]$New,
        [string]$Label
    )
    if (-not $Text.Contains($Old)) {
        throw ('PHASE=patch cause=pattern_missing label=' + $Label)
    }
    return $Text.Replace($Old, $New)
}

Write-Output 'PHASE=fetch'
& git.exe -C $repo fetch origin $branch
if ($LASTEXITCODE -ne 0) { throw 'PHASE=fetch cause=git_fetch_failed' }
if (Test-Path -LiteralPath $wt) { throw 'PHASE=worktree cause=workspace_exists' }
& git.exe -C $repo worktree add --detach $wt ('origin/' + $branch)
if ($LASTEXITCODE -ne 0) { throw 'PHASE=worktree cause=create_failed' }

Write-Output 'PHASE=patch'
$p = Join-Path $wt 'factory_bridge\system_command.go'
$s = [IO.File]::ReadAllText($p)
$s = Replace-Required -Text $s -Old 'res.Output = compact(strings.TrimSpace(stdout), 1200)' -New 'res.Output = tailCompact(sanitizeRemoteText(strings.TrimSpace(stdout)), 1200)' -Label 'system-command-output'
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'factory_bridge\github_bus.go'
$s = [IO.File]::ReadAllText($p)
$old = 'BridgeVersion string `json:"bridgeVersion,omitempty"`'
$new = ('BridgeVersion string `json:"bridgeVersion,omitempty"`' + "`r`n`t" +
    'SourceCommit string `json:"sourceCommit,omitempty"`' + "`r`n`t" +
    'BinarySHA256 string `json:"binarySha256,omitempty"`' + "`r`n`t" +
    'ProtocolVersion string `json:"protocolVersion,omitempty"`' + "`r`n`t" +
    'PolicyVersion string `json:"policyVersion,omitempty"`')
$s = Replace-Required -Text $s -Old $old -New $new -Label 'github-envelope-identity-fields'
$old = 'envelope.Summary = sanitizeRemoteText(envelope.Summary)'
$new = ('envelope.Summary = sanitizeRemoteText(envelope.Summary)' + "`r`n`t" +
    'envelope.SourceCommit = runtimeSourceCommit()' + "`r`n`t" +
    'envelope.BinarySHA256 = runningBinarySHA256()' + "`r`n`t" +
    'envelope.ProtocolVersion = factoryBusProtocolVersion' + "`r`n`t" +
    'envelope.PolicyVersion = factoryRiskPolicyVersion')
$s = Replace-Required -Text $s -Old $old -New $new -Label 'github-envelope-identity-values'
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'factory_bridge\gateway.go'
$s = [IO.File]::ReadAllText($p)
$old = 'BridgeVersion     string `json:"bridgeVersion"`'
$new = ('BridgeVersion     string `json:"bridgeVersion"`' + "`r`n`t" +
    'SourceCommit      string `json:"sourceCommit"`' + "`r`n`t" +
    'BinarySHA256      string `json:"binarySha256"`' + "`r`n`t" +
    'ProtocolVersion   string `json:"protocolVersion"`' + "`r`n`t" +
    'PolicyVersion     string `json:"policyVersion"`')
$s = Replace-Required -Text $s -Old $old -New $new -Label 'gateway-status-identity-fields'
$old = 'BridgeVersion:     bridgeVersion,'
$new = ('BridgeVersion:     bridgeVersion,' + "`r`n`t`t" +
    'SourceCommit:      runtimeSourceCommit(),' + "`r`n`t`t" +
    'BinarySHA256:      runningBinarySHA256(),' + "`r`n`t`t" +
    'ProtocolVersion:   factoryBusProtocolVersion,' + "`r`n`t`t" +
    'PolicyVersion:     factoryRiskPolicyVersion,')
$s = Replace-Required -Text $s -Old $old -New $new -Label 'gateway-status-identity-values'
$old = '"bridgeVersion":  status.BridgeVersion,'
$new = ('"bridgeVersion":  status.BridgeVersion,' + "`r`n`t`t`t" +
    '"sourceCommit":   status.SourceCommit,' + "`r`n`t`t`t" +
    '"binarySha256":   status.BinarySHA256,' + "`r`n`t`t`t" +
    '"protocolVersion": status.ProtocolVersion,' + "`r`n`t`t`t" +
    '"policyVersion":  status.PolicyVersion,')
$s = Replace-Required -Text $s -Old $old -New $new -Label 'gateway-private-status-identity-values'
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

$p = Join-Path $wt 'scripts\factory-bridge-autoupdate.ps1'
$s = [IO.File]::ReadAllText($p)
$old = '& $go.Source build -trimpath -ldflags ''-s -w'' -o $nextExe .'
$new = '& $go.Source build -trimpath -ldflags (''-s -w -X main.buildSourceCommit='' + $target) -o $nextExe .'
$s = Replace-Required -Text $s -Old $old -New $new -Label 'autoupdate-source-commit-ldflags'
[IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))

Write-Output 'PHASE=format-test'
Push-Location (Join-Path $wt 'factory_bridge')
try {
    & gofmt.exe -w runtime_identity.go runtime_identity_test.go system_command_hardening.go system_command.go github_bus.go gateway.go
    if ($LASTEXITCODE -ne 0) { throw 'PHASE=gofmt cause=failed' }
    & go.exe test ./...
    if ($LASTEXITCODE -ne 0) { throw 'PHASE=test cause=go_test_failed' }
}
finally {
    Pop-Location
}

Write-Output 'PHASE=powershell-parse'
$tokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile((Join-Path $wt 'scripts\factory-bridge-autoupdate.ps1'), [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw ('PHASE=powershell-parse cause=' + $parseErrors[0].Message) }

Write-Output 'PHASE=commit'
& git.exe -C $wt add factory_bridge scripts/factory-bridge-autoupdate.ps1 scripts/apply-factorybridge-p0-identity.ps1
& git.exe -C $wt commit -m 'security(factory-bridge): bind runtime identity and close script bypass'
if ($LASTEXITCODE -ne 0) { throw 'PHASE=commit cause=git_commit_failed' }
& git.exe -C $wt push origin HEAD:$branch
if ($LASTEXITCODE -ne 0) { throw 'PHASE=push cause=git_push_failed' }
$head = ((& git.exe -C $wt rev-parse HEAD) -join '').Trim()
Write-Output ('FACTORYBRIDGE_P0_IDENTITY=PASS commit=' + $head)
