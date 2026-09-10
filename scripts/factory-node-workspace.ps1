param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('create', 'status')]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$Repo,

    [Parameter(Mandatory = $true)]
    [string]$MissionId,

    [string]$Commit = 'HEAD'
)

$ErrorActionPreference = 'Stop'

if ($MissionId -notmatch '^[A-Za-z0-9._-]{1,96}$') {
    throw 'Invalid MissionId.'
}

if (-not (Test-Path -LiteralPath $Repo)) {
    throw 'Repository path does not exist.'
}

$inside = (& git -C $Repo rev-parse --is-inside-work-tree 2>$null)
if ($LASTEXITCODE -ne 0 -or $inside.Trim() -ne 'true') {
    throw 'Repository path is not a Git work tree.'
}

$root = Join-Path $env:LOCALAPPDATA 'FactoryNode'
$workspaces = Join-Path $root 'workspaces'
New-Item -ItemType Directory -Path $workspaces -Force | Out-Null
$target = Join-Path $workspaces $MissionId

switch ($Action) {
    'create' {
        if (Test-Path -LiteralPath $target) {
            throw 'Workspace already exists.'
        }

        & git -C $Repo worktree add --detach $target $Commit
        if ($LASTEXITCODE -ne 0) {
            throw 'git worktree add failed.'
        }

        [ordered]@{
            status    = 'CREATED'
            missionId = $MissionId
            workspace = $target
            commit    = (& git -C $target rev-parse HEAD).Trim()
        } | ConvertTo-Json -Compress
    }

    'status' {
        if (-not (Test-Path -LiteralPath $target)) {
            throw 'Workspace not found.'
        }

        [ordered]@{
            status    = 'OK'
            missionId = $MissionId
            workspace = $target
            commit    = (& git -C $target rev-parse HEAD).Trim()
            gitStatus = ((& git -C $target status --short --branch) | Out-String).Trim()
        } | ConvertTo-Json -Compress
    }
}

# Deliberately no delete/prune action. Destructive lifecycle operations remain approval-gated.