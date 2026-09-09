param()

$ErrorActionPreference = 'Stop'

# Canonical gateway setup now uses the stable Tailscale Funnel path.
# The legacy Cloudflare quick-tunnel runner remains in the repository as fallback,
# but it is no longer the primary setup invoked by FactoryBridge.
$stableSetup = Join-Path $PSScriptRoot 'setup-factory-gateway-tailscale.ps1'
if (-not (Test-Path -LiteralPath $stableSetup -PathType Leaf)) {
    throw "Stable Factory gateway setup not found: $stableSetup"
}

& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $stableSetup
if ($LASTEXITCODE -ne 0) {
    throw "Stable Factory gateway setup failed with exit code $LASTEXITCODE."
}

Write-Host ''
Write-Host 'FACTORY_GATEWAY_SETUP_CANONICAL=TAILSCALE_FUNNEL'
