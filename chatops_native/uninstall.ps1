param(
    [switch]$RemoveFiles
)

$ErrorActionPreference = "Stop"

$HostName = "com.toctox.chatops_codex"
$Root = Join-Path $env:LOCALAPPDATA "ChatOpsCodex"
$RegistryPath = "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$HostName"

if (Test-Path -LiteralPath $RegistryPath) {
    Remove-Item -LiteralPath $RegistryPath -Recurse -Force
    Write-Host "Removed Native Messaging registration."
} else {
    Write-Host "Native Messaging registration was not present."
}

if ($RemoveFiles) {
    $running = Get-Process -Name "ChatOpsCodexHost" -ErrorAction SilentlyContinue
    if ($running) {
        throw "ChatOpsCodexHost is still running. Close ChatGPT/Chrome bridge sessions and retry."
    }
    if (Test-Path -LiteralPath $Root) {
        Remove-Item -LiteralPath $Root -Recurse -Force
        Write-Host "Removed $Root"
    }
} else {
    Write-Host "Files preserved at $Root"
    Write-Host "Use -RemoveFiles to delete them after disabling/removing the Chrome extension."
}

Write-Host "Remove or disable 'ChatOps Codex Bridge' from chrome://extensions if desired."
