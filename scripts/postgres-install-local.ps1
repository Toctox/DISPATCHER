[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$postgresVersion = '18.6'
$packageRevision = '1'
$downloadUrl = "https://get.enterprisedb.com/postgresql/postgresql-$postgresVersion-$packageRevision-windows-x64-binaries.zip"
$root = Join-Path $env:LOCALAPPDATA 'ProjectHub\PostgreSQL'
$installDir = Join-Path $root $postgresVersion
$dataDir = Join-Path $root 'data'
$downloadDir = Join-Path $root 'downloads'
$secretDir = Join-Path $root 'secrets'
$archive = Join-Path $downloadDir "postgresql-$postgresVersion-$packageRevision-windows-x64-binaries.zip"
$logDir = Join-Path $root 'logs'
$serverLog = Join-Path $logDir 'postgresql.log'
$secretPath = Join-Path $secretDir 'postgres-superuser.dpapi'
$metadataPath = Join-Path $root 'runtime.json'
$superuser = 'projecthub_admin'
$port = 5432
$taskName = 'ProjectHub Local PostgreSQL'

foreach ($dir in @($root, $downloadDir, $secretDir, $logDir)) {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}

function Find-PostgresBin {
    param([string]$Base)
    if (-not (Test-Path -LiteralPath $Base -PathType Container)) { return $null }
    $postgres = Get-ChildItem -LiteralPath $Base -Filter 'postgres.exe' -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Directory.Name -ieq 'bin' } |
        Select-Object -First 1
    if ($null -eq $postgres) { return $null }
    $bin = $postgres.Directory.FullName
    foreach ($name in @('initdb.exe','pg_ctl.exe','psql.exe','pg_isready.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $bin $name) -PathType Leaf)) { return $null }
    }
    return $bin
}

function Find-PostgresShare {
    param([string]$Base)
    if (-not (Test-Path -LiteralPath $Base -PathType Container)) { return $null }
    $bki = Get-ChildItem -LiteralPath $Base -Filter 'postgres.bki' -File -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $bki) { return $null }
    return $bki.Directory.FullName
}

$binDir = Find-PostgresBin -Base $installDir
$shareDir = Find-PostgresShare -Base $installDir
$downloaded = $false
$extracted = $false
$initialized = $false
$started = $false

if ($null -eq $binDir -or $null -eq $shareDir) {
    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $archive -UseBasicParsing
        $downloaded = $true
    }
    if (Test-Path -LiteralPath $installDir) {
        Remove-Item -LiteralPath $installDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $installDir -Force
    $extracted = $true
    $binDir = Find-PostgresBin -Base $installDir
    $shareDir = Find-PostgresShare -Base $installDir
    if ($null -eq $binDir) {
        throw 'PostgreSQL archive did not contain the expected Windows binaries.'
    }
    if ($null -eq $shareDir) {
        throw 'PostgreSQL archive did not contain postgres.bki; installation layout is incomplete.'
    }
}

$postgres = Join-Path $binDir 'postgres.exe'
$initdb = Join-Path $binDir 'initdb.exe'
$pgCtl = Join-Path $binDir 'pg_ctl.exe'
$pgIsReady = Join-Path $binDir 'pg_isready.exe'

$versionOutput = (& $postgres --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $versionOutput -notmatch [regex]::Escape($postgresVersion)) {
    throw "Unexpected PostgreSQL binary version: $versionOutput"
}

if (-not (Test-Path -LiteralPath (Join-Path $dataDir 'PG_VERSION') -PathType Leaf)) {
    if (Test-Path -LiteralPath $dataDir) {
        Remove-Item -LiteralPath $dataDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $password = [Convert]::ToBase64String($bytes)
    $secure = ConvertTo-SecureString $password -AsPlainText -Force
    $protected = ConvertFrom-SecureString $secure
    [IO.File]::WriteAllText($secretPath, $protected, (New-Object Text.UTF8Encoding($false)))

    $pwFile = Join-Path $env:TEMP ("factorybridge-postgres-" + [Guid]::NewGuid().ToString('N') + '.pw')
    try {
        [IO.File]::WriteAllText($pwFile, $password, (New-Object Text.UTF8Encoding($false)))
        & $initdb '-D' $dataDir '-L' $shareDir '-U' $superuser '-A' 'scram-sha-256' '--encoding=UTF8' "--pwfile=$pwFile" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "initdb failed with exit code $LASTEXITCODE" }
    }
    finally {
        if (Test-Path -LiteralPath $pwFile) { Remove-Item -LiteralPath $pwFile -Force }
        $password = $null
    }

    Add-Content -LiteralPath (Join-Path $dataDir 'postgresql.conf') -Value @"

# Managed by FactoryBridge postgres.install
listen_addresses = '127.0.0.1'
port = $port
password_encryption = 'scram-sha-256'
"@
    $initialized = $true
}
elseif (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) {
    throw 'Existing FactoryBridge PostgreSQL data directory has no DPAPI-protected superuser secret; refusing to replace credentials.'
}

& $pgCtl '-D' $dataDir 'status' *> $null
$ourServerRunning = ($LASTEXITCODE -eq 0)
if (-not $ourServerRunning) {
    $portBusy = $false
    $client = New-Object Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $port, $null, $null)
        if ($async.AsyncWaitHandle.WaitOne(500)) {
            try { $client.EndConnect($async); $portBusy = $client.Connected } catch { $portBusy = $false }
        }
    }
    finally { $client.Close() }
    if ($portBusy) {
        throw "TCP port $port is already in use by a process not owned by this FactoryBridge PostgreSQL cluster."
    }
    & $pgCtl '-D' $dataDir '-l' $serverLog '-w' '-t' '60' 'start' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pg_ctl start failed with exit code $LASTEXITCODE" }
    $started = $true
}

& $pgIsReady '-h' '127.0.0.1' '-p' ([string]$port) | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL did not become ready on 127.0.0.1:5432.' }

$taskStatus = 'existing'
try {
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($null -eq $existing) {
        $actionArgs = "start -D `"$dataDir`" -l `"$serverLog`""
        $action = New-ScheduledTaskAction -Execute $pgCtl -Argument $actionArgs
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
        $taskStatus = 'created'
    }
}
catch {
    $taskStatus = 'warning:' + $_.Exception.Message
}

$runtime = [ordered]@{
    managedBy = 'FactoryBridge'
    version = $postgresVersion
    installDir = $installDir
    binDir = $binDir
    shareDir = $shareDir
    dataDir = $dataDir
    host = '127.0.0.1'
    port = $port
    superuser = $superuser
    secretStore = 'DPAPI CurrentUser'
    secretPath = $secretPath
    taskName = $taskName
    updatedAt = (Get-Date).ToString('o')
}
[IO.File]::WriteAllText($metadataPath, ($runtime | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))

$result = [ordered]@{
    kind = 'POSTGRES_INSTALL'
    status = 'OK'
    version = $postgresVersion
    host = '127.0.0.1'
    port = $port
    superuser = $superuser
    running = $true
    downloaded = $downloaded
    extracted = $extracted
    initialized = $initialized
    started = $started
    persistenceTask = $taskStatus
    secretStored = $true
    secretStore = 'DPAPI CurrentUser'
    metadataPath = $metadataPath
}
Write-Output ($result | ConvertTo-Json -Compress)
