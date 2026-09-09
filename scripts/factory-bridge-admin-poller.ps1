[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$stateDir = Join-Path $root 'state'
$adminDir = Join-Path $root 'admin'
$pollerStatePath = Join-Path $stateDir 'admin-poller.json'
$requestPath = Join-Path $stateDir 'self-update-request.json'
$resultPath = Join-Path $stateDir 'self-update-result.json'
$updater = Join-Path $adminDir 'factory-bridge-autoupdate.ps1'
$protocol = 'FACTORY_ADMIN_V1'
$marker = '<!-- FACTORY_ADMIN_V1 -->'
$trustedAuthor = 'Toctox'
$repo = 'Toctox/DISPATCHER'
$issue = 7
$api = 'https://api.github.com'

New-Item -ItemType Directory -Path $stateDir,$adminDir -Force | Out-Null

function Get-GitHubCredential {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'git.exe'
    $psi.Arguments = 'credential fill'
    $psi.UseShellExecute = $false
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    if (-not $p.Start()) { throw 'git credential fill could not start' }
    $p.StandardInput.WriteLine('protocol=https')
    $p.StandardInput.WriteLine('host=github.com')
    $p.StandardInput.WriteLine('')
    $p.StandardInput.Close()
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) { throw ('git credential fill failed: ' + $stderr) }

    $token = $null
    foreach ($line in ($stdout -split "`r?`n")) {
        if ($line -match '^password=(.+)$') {
            $token = $Matches[1].Trim()
        }
    }
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw 'GitHub credential helper returned no token'
    }
    return $token
}

function Get-Headers([string]$Token) {
    return @{
        Accept = 'application/vnd.github+json'
        Authorization = ('Bearer ' + $Token)
        'X-GitHub-Api-Version' = '2022-11-28'
        'User-Agent' = 'FactoryBridge-Admin-Poller/2'
    }
}

function Get-Comments([string]$Token) {
    for ($page = 1; $page -le 20; $page++) {
        $uri = ('{0}/repos/{1}/issues/{2}/comments?per_page=100&page={3}' -f $api,$repo,$issue,$page)
        $pageResult = Invoke-RestMethod -Method Get -Uri $uri -Headers (Get-Headers $Token) -TimeoutSec 20
        $pageCount = 0
        foreach ($item in $pageResult) {
            if ($null -eq $item) { continue }
            $pageCount++
            Write-Output $item
        }
        if ($pageCount -lt 100) { break }
    }
}

function Get-PayloadHash(
    [string]$Id,
    [string]$Action,
    [string]$TargetCommit,
    [string]$IssuedAt,
    [string]$ExpiresAt
) {
    $material = $Id + "`n" + $Action + "`n" + $TargetCommit.ToLowerInvariant() + "`n" + $IssuedAt + "`n" + $ExpiresAt
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($material)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Read-PollerState {
    if (-not (Test-Path -LiteralPath $pollerStatePath -PathType Leaf)) {
        return [pscustomobject]@{ lastSuccessfulApprovalId = [Int64]0 }
    }
    try {
        return (Get-Content -LiteralPath $pollerStatePath -Raw | ConvertFrom-Json)
    }
    catch {
        return [pscustomobject]@{ lastSuccessfulApprovalId = [Int64]0 }
    }
}

function Write-PollerState([Int64]$CommentId, [string]$ApprovalId, [string]$TargetCommit) {
    $state = [ordered]@{
        lastSuccessfulApprovalId = $CommentId
        approvalId = $ApprovalId
        targetCommit = $TargetCommit
        updatedAt = (Get-Date).ToUniversalTime().ToString('o')
    }
    [System.IO.File]::WriteAllText(
        $pollerStatePath,
        ($state | ConvertTo-Json -Depth 6),
        (New-Object System.Text.UTF8Encoding($false))
    )
}

function Publish-UpdateResult([string]$Token, $Approval, $Result) {
    $envelope = [ordered]@{
        protocol = 'FACTORY_BUS_V2'
        type = 'UPDATE_RESULT'
        id = [string]$Approval.id
        state = [string]$Result.state
        summary = [string]$Result.summary
        commit = [string]$Result.targetCommit
        observedAt = (Get-Date).ToUniversalTime().ToString('o')
    }

    $body = "<!-- FACTORY_BUS_V2 -->`n" + ($envelope | ConvertTo-Json -Depth 8)
    $uri = ('{0}/repos/{1}/issues/{2}/comments' -f $api,$repo,$issue)

    Invoke-RestMethod `
        -Method Post `
        -Uri $uri `
        -Headers (Get-Headers $Token) `
        -ContentType 'application/json' `
        -Body (@{ body = $body } | ConvertTo-Json -Compress) `
        -TimeoutSec 20 | Out-Null
}

function Parse-Approval($Comment, [Int64]$LastSuccessfulCommentId) {
    if ([Int64]$Comment.id -le $LastSuccessfulCommentId) { return $null }
    if ([string]$Comment.user.login -cne $trustedAuthor) { return $null }

    $commentBody = [string]$Comment.body
    if (-not $commentBody.Contains($marker)) { return $null }

    $match = [regex]::Match($commentBody, '(?s)```json\s*(\{.*?\})\s*```')
    if (-not $match.Success) { return $null }

    try {
        $a = $match.Groups[1].Value | ConvertFrom-Json
    }
    catch {
        return $null
    }

    if ([string]$a.protocol -cne $protocol) { return $null }
    if ([string]$a.type -cne 'ALLOW_COMMAND') { return $null }
    if ([string]$a.action -cne 'bridge.self_update') { return $null }

    $id = ([string]$a.id).Trim()
    if ($id -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$') { return $null }

    $target = ([string]$a.targetCommit).Trim().ToLowerInvariant()
    if ($target -notmatch '^[0-9a-f]{40}$') { return $null }

    $issuedText = ([string]$a.issuedAt).Trim()
    $expiresText = ([string]$a.expiresAt).Trim()

    try {
        $issued = [DateTimeOffset]::Parse($issuedText).ToUniversalTime()
        $expires = [DateTimeOffset]::Parse($expiresText).ToUniversalTime()
    }
    catch {
        return $null
    }

    $now = [DateTimeOffset]::UtcNow
    if ($expires -le $issued -or $expires -le $now) { return $null }
    if (($expires - $issued).TotalHours -gt 2.1) { return $null }
    if ($issued -gt $now.AddMinutes(5)) { return $null }

    $want = Get-PayloadHash `
        -Id $id `
        -Action 'bridge.self_update' `
        -TargetCommit $target `
        -IssuedAt $issuedText `
        -ExpiresAt $expiresText

    if (-not [string]::Equals(
        ([string]$a.payloadHash).Trim(),
        $want,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        return $null
    }

    return [pscustomobject]@{
        commentId = [Int64]$Comment.id
        id = $id
        action = 'bridge.self_update'
        targetCommit = $target
        issuedAt = $issuedText
        expiresAt = $expiresText
        payloadHash = $want
    }
}

$token = Get-GitHubCredential
$state = Read-PollerState
$lastSuccessful = [Int64]$state.lastSuccessfulApprovalId
$comments = @(Get-Comments -Token $token)

$approval = $null
$latestApprovalCommentId = $lastSuccessful
foreach ($comment in $comments) {
    $candidate = Parse-Approval -Comment $comment -LastSuccessfulCommentId $lastSuccessful
    if ($null -ne $candidate -and [Int64]$candidate.commentId -gt $latestApprovalCommentId) {
        $approval = $candidate
        $latestApprovalCommentId = [Int64]$candidate.commentId
    }
}

if ($null -eq $approval) {
    Write-Host 'FACTORYBRIDGE_ADMIN_NO_PENDING_APPROVAL'
    exit 0
}

if (-not (Test-Path -LiteralPath $updater -PathType Leaf)) {
    throw ('fixed updater not installed: ' + $updater)
}

$request = [ordered]@{
    id = $approval.id
    action = $approval.action
    targetCommit = $approval.targetCommit
    issuedAt = $approval.issuedAt
    expiresAt = $approval.expiresAt
    payloadHash = $approval.payloadHash
    sourceCommentId = $approval.commentId
}

[System.IO.File]::WriteAllText(
    $requestPath,
    ($request | ConvertTo-Json -Depth 8),
    (New-Object System.Text.UTF8Encoding($false))
)

& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $updater
$updateExit = $LASTEXITCODE

if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
    throw 'self-update result was not produced'
}

$result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json

try {
    Publish-UpdateResult -Token $token -Approval $approval -Result $result
}
catch {
    Write-Warning ('Could not publish UPDATE_RESULT: ' + $_.Exception.Message)
}

if ($updateExit -eq 0 -and (
    [string]$result.state -eq 'DONE' -or
    [string]$result.state -eq 'NOOP'
)) {
    Write-PollerState `
        -CommentId $approval.commentId `
        -ApprovalId $approval.id `
        -TargetCommit $approval.targetCommit

    Write-Host ('FACTORYBRIDGE_ADMIN_UPDATE_' + ([string]$result.state))
    exit 0
}

exit $updateExit
