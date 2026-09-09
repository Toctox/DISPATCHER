[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FactoryBridge'
$stateDir = Join-Path $root 'state'
$adminDir = Join-Path $root 'admin'
$pollerStatePath = Join-Path $stateDir 'admin-poller.json'
$healthPath = Join-Path $stateDir 'admin-poller-health.json'
$requestPath = Join-Path $stateDir 'self-update-request.json'
$resultPath = Join-Path $stateDir 'self-update-result.json'
$pendingPublishPath = Join-Path $stateDir 'admin-poller-pending-update-result.json'
$updater = Join-Path $adminDir 'factory-bridge-autoupdate.ps1'
$protocol = 'FACTORY_ADMIN_V1'
$marker = '<!-- FACTORY_ADMIN_V1 -->'
$trustedAuthor = 'Toctox'
$repo = 'Toctox/DISPATCHER'
$issue = 7
$api = 'https://api.github.com'

New-Item -ItemType Directory -Path $stateDir,$adminDir -Force | Out-Null

function Write-JsonAtomic([string]$Path, $Value) {
    $tmp = $Path + '.tmp'
    [System.IO.File]::WriteAllText($tmp, ($Value | ConvertTo-Json -Depth 10), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Write-Health([string]$Status, [string]$Message, [Int64]$LastSuccessfulCommentId) {
    $health = [ordered]@{
        status = $Status
        message = $Message
        observedAt = (Get-Date).ToUniversalTime().ToString('o')
        lastSuccessfulApprovalId = $LastSuccessfulCommentId
        pendingUpdateResult = (Test-Path -LiteralPath $pendingPublishPath -PathType Leaf)
    }
    Write-JsonAtomic -Path $healthPath -Value $health
}

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
    if ($p.ExitCode -ne 0) { throw "git credential fill failed: $stderr" }
    $token = $null
    foreach ($line in ($stdout -split "`r?`n")) {
        if ($line -match '^password=(.+)$') { $token = $Matches[1].Trim() }
    }
    if ([string]::IsNullOrWhiteSpace($token)) { throw 'GitHub credential helper returned no token' }
    return $token
}

function Get-Headers([string]$Token) {
    return @{
        Accept = 'application/vnd.github+json'
        Authorization = "Bearer $Token"
        'X-GitHub-Api-Version' = '2022-11-28'
        'User-Agent' = 'FactoryBridge-Admin-Poller/2'
    }
}

function Get-Comments([string]$Token) {
    $all = @()
    for ($page = 1; $page -le 20; $page++) {
        $uri = "$api/repos/$repo/issues/$issue/comments?per_page=100&page=$page"
        $items = @(Invoke-RestMethod -Method Get -Uri $uri -Headers (Get-Headers $Token) -TimeoutSec 20)
        foreach ($item in $items) {
            if ($null -ne $item -and $null -ne $item.id) { $all += $item }
        }
        if ($items.Count -lt 100) { break }
    }
    return $all
}

function Get-PayloadHash([string]$Id, [string]$Action, [string]$TargetCommit, [string]$IssuedAt, [string]$ExpiresAt) {
    $material = $Id + "`n" + $Action + "`n" + $TargetCommit.ToLowerInvariant() + "`n" + $IssuedAt + "`n" + $ExpiresAt
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($material)
        return ([System.BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Read-PollerState {
    if (-not (Test-Path -LiteralPath $pollerStatePath -PathType Leaf)) {
        return [pscustomobject]@{ lastSuccessfulApprovalId = [Int64]0 }
    }
    try { return (Get-Content -LiteralPath $pollerStatePath -Raw | ConvertFrom-Json) }
    catch { return [pscustomobject]@{ lastSuccessfulApprovalId = [Int64]0 } }
}

function Write-PollerState([Int64]$CommentId, [string]$ApprovalId, [string]$TargetCommit) {
    $state = [ordered]@{
        lastSuccessfulApprovalId = $CommentId
        approvalId = $ApprovalId
        targetCommit = $TargetCommit
        updatedAt = (Get-Date).ToUniversalTime().ToString('o')
    }
    Write-JsonAtomic -Path $pollerStatePath -Value $state
}

function New-UpdateEnvelope($Approval, $Result) {
    return [ordered]@{
        protocol = 'FACTORY_BUS_V2'
        type = 'UPDATE_RESULT'
        id = [string]$Approval.id
        state = [string]$Result.state
        summary = [string]$Result.summary
        commit = [string]$Result.targetCommit
        observedAt = (Get-Date).ToUniversalTime().ToString('o')
    }
}

function Publish-UpdateEnvelope([string]$Token, $Envelope) {
    $body = '<!-- FACTORY_BUS_V2 -->' + [Environment]::NewLine + '```json' + [Environment]::NewLine + ($Envelope | ConvertTo-Json -Depth 8) + [Environment]::NewLine + '```'
    $uri = "$api/repos/$repo/issues/$issue/comments"
    Invoke-RestMethod -Method Post -Uri $uri -Headers (Get-Headers $Token) -ContentType 'application/json' -Body (@{body=$body} | ConvertTo-Json -Compress) -TimeoutSec 20 | Out-Null
}

function Retry-PendingUpdateResult([string]$Token) {
    if (-not (Test-Path -LiteralPath $pendingPublishPath -PathType Leaf)) { return $true }
    try {
        $pending = Get-Content -LiteralPath $pendingPublishPath -Raw | ConvertFrom-Json
        Publish-UpdateEnvelope -Token $Token -Envelope $pending
        Remove-Item -LiteralPath $pendingPublishPath -Force
        return $true
    }
    catch {
        return $false
    }
}

function Parse-Approval($Comment, [Int64]$LastSuccessfulCommentId) {
    $commentIdText = [string]$Comment.id
    [Int64]$commentId = 0
    if (-not [Int64]::TryParse($commentIdText, [ref]$commentId)) { return $null }
    if ($commentId -le $LastSuccessfulCommentId) { return $null }
    if ([string]$Comment.user.login -cne $trustedAuthor) { return $null }
    $body = [string]$Comment.body
    if (-not $body.Contains($marker)) { return $null }
    $match = [regex]::Match($body, '(?s)```json\s*(\{.*?\})\s*```')
    if (-not $match.Success) { return $null }
    try { $a = $match.Groups[1].Value | ConvertFrom-Json }
    catch { return $null }

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
    } catch { return $null }
    $now = [DateTimeOffset]::UtcNow
    if ($expires -le $issued -or $expires -le $now) { return $null }
    if (($expires - $issued).TotalHours -gt 2.1) { return $null }
    if ($issued -gt $now.AddMinutes(5)) { return $null }
    $want = Get-PayloadHash -Id $id -Action 'bridge.self_update' -TargetCommit $target -IssuedAt $issuedText -ExpiresAt $expiresText
    if (-not [string]::Equals(([string]$a.payloadHash).Trim(), $want, [StringComparison]::OrdinalIgnoreCase)) { return $null }

    return [pscustomobject]@{
        commentId = $commentId
        id = $id
        action = 'bridge.self_update'
        targetCommit = $target
        issuedAt = $issuedText
        expiresAt = $expiresText
        payloadHash = $want
    }
}

$lastSuccessful = [Int64]0
try {
    $token = Get-GitHubCredential
    $state = Read-PollerState
    [Int64]::TryParse(([string]$state.lastSuccessfulApprovalId), [ref]$lastSuccessful) | Out-Null

    if (-not (Retry-PendingUpdateResult -Token $token)) {
        Write-Health -Status 'DEGRADED' -Message 'pending UPDATE_RESULT could not be published; will retry next poll' -LastSuccessfulCommentId $lastSuccessful
    }

    $comments = @(Get-Comments -Token $token | Sort-Object { [Int64]([string]$_.id) })
    $approval = $null
    foreach ($comment in $comments) {
        $candidate = Parse-Approval -Comment $comment -LastSuccessfulCommentId $lastSuccessful
        if ($null -ne $candidate) { $approval = $candidate }
    }

    if ($null -eq $approval) {
        Write-Health -Status 'OK' -Message 'poll completed; no new valid self-update approval' -LastSuccessfulCommentId $lastSuccessful
        exit 0
    }
    if (-not (Test-Path -LiteralPath $updater -PathType Leaf)) { throw "fixed updater not installed: $updater" }

    $request = [ordered]@{
        id = $approval.id
        action = $approval.action
        targetCommit = $approval.targetCommit
        issuedAt = $approval.issuedAt
        expiresAt = $approval.expiresAt
        payloadHash = $approval.payloadHash
        sourceCommentId = $approval.commentId
    }
    Write-JsonAtomic -Path $requestPath -Value $request

    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $updater
    $updateExit = $LASTEXITCODE
    if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { throw 'self-update result was not produced' }
    $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json

    $envelope = New-UpdateEnvelope -Approval $approval -Result $result
    Write-JsonAtomic -Path $pendingPublishPath -Value $envelope
    try {
        Publish-UpdateEnvelope -Token $token -Envelope $envelope
        Remove-Item -LiteralPath $pendingPublishPath -Force
    }
    catch {
        # Keep the durable pending envelope and retry on the next poll.
    }

    if ($updateExit -eq 0 -and ([string]$result.state -eq 'DONE' -or [string]$result.state -eq 'NOOP')) {
        Write-PollerState -CommentId $approval.commentId -ApprovalId $approval.id -TargetCommit $approval.targetCommit
        $lastSuccessful = $approval.commentId
        Write-Health -Status 'OK' -Message ("self-update processed: " + [string]$result.state) -LastSuccessfulCommentId $lastSuccessful
        exit 0
    }

    Write-Health -Status 'ERROR' -Message ("self-update failed with state=" + [string]$result.state + " exit=" + [string]$updateExit) -LastSuccessfulCommentId $lastSuccessful
    exit $updateExit
}
catch {
    try { Write-Health -Status 'ERROR' -Message $_.Exception.Message -LastSuccessfulCommentId $lastSuccessful } catch { }
    throw
}
