param(
    [switch]$RunSmoke
)

$ErrorActionPreference = "Stop"

$HostName = "com.toctox.chatops_codex"
$ExtensionId = "mhjghnfpggpfmcoheapfibohfeiipmhn"
$Root = Join-Path $env:LOCALAPPDATA "ChatOpsCodex"
$HostExe = Join-Path $Root "ChatOpsCodexHost.exe"
$ExtensionTarget = Join-Path $Root "extension"
$ManifestPath = Join-Path $Root "$HostName.json"
$RegistryPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$HostName"

$HostSource = Join-Path $PSScriptRoot "host"
$ExtensionSource = Join-Path $PSScriptRoot "extension"

if (-not (Test-Path -LiteralPath $HostSource)) {
    throw "Host source not found: $HostSource"
}
if (-not (Test-Path -LiteralPath $ExtensionSource)) {
    throw "Extension source not found: $ExtensionSource"
}

$Go = Get-Command go.exe -ErrorAction SilentlyContinue
if (-not $Go) {
    throw "go.exe was not found on PATH. Install Go or build ChatOpsCodexHost.exe separately."
}

New-Item -ItemType Directory -Force -Path $Root | Out-Null

Write-Host "Building native host..."
Push-Location $HostSource
try {
    & $Go.Source build -trimpath -ldflags "-s -w" -o $HostExe .
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $HostExe)) {
        throw "Native host build failed."
    }
}
finally {
    Pop-Location
}

if (Test-Path -LiteralPath $ExtensionTarget) {
    Remove-Item -LiteralPath $ExtensionTarget -Recurse -Force
}
Copy-Item -LiteralPath $ExtensionSource -Destination $ExtensionTarget -Recurse -Force

Write-Host ""
Write-Host "Native host: $HostExe"
Write-Host "Chrome extension folder: $ExtensionTarget"
Write-Host "Expected extension ID: $ExtensionId"

Write-Host ""
Write-Host "Validating Codex discovery..."
& $HostExe --self-test
if ($LASTEXITCODE -ne 0) {
    throw "Native host self-test failed."
}

if ($RunSmoke) {
    Write-Host ""
    Write-Host "Running harmless Codex smoke..."
    & $HostExe --smoke
    if ($LASTEXITCODE -ne 0) {
        throw "Codex smoke failed."
    }
}

$Manifest = [ordered]@{
    name = $HostName
    description = "Local ChatOps Codex host"
    path = $HostExe
    type = "stdio"
    allowed_origins = @("chrome-extension://$ExtensionId/")
} | ConvertTo-Json -Depth 4

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ManifestPath, $Manifest, $Utf8NoBom)

New-Item -Path $RegistryPath -Force | Out-Null
Set-Item -Path $RegistryPath -Value $ManifestPath

$InstallState = [ordered]@{
    installedAt = [DateTime]::UtcNow.ToString("o")
    hostName = $HostName
    hostExecutable = $HostExe
    extensionDirectory = $ExtensionTarget
    extensionId = $ExtensionId
    nativeManifest = $ManifestPath
    registryPath = $RegistryPath
} | ConvertTo-Json -Depth 4
[System.IO.File]::WriteAllText((Join-Path $Root "install-state.json"), $InstallState, $Utf8NoBom)

Write-Host ""
Write-Host "INSTALL COMPLETE"
Write-Host "Registered per-user Native Messaging host:"
Write-Host "  $RegistryPath"
Write-Host "Allowed extension:"
Write-Host "  chrome-extension://$ExtensionId/"
Write-Host ""
Write-Host "ONE MANUAL CHROME STEP REMAINS:"
Write-Host "1. Open chrome://extensions"
Write-Host "2. Enable Developer mode"
Write-Host "3. Click 'Load unpacked' and select:"
Write-Host "   $ExtensionTarget"
Write-Host "4. Confirm Chrome shows extension ID:"
Write-Host "   $ExtensionId"
Write-Host "5. Reload chatgpt.com"
