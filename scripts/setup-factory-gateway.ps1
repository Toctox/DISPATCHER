param()

$ErrorActionPreference = 'Stop'

# Canonical gateway setup uses the stable Tailscale Funnel path.
$stableSetup = Join-Path $PSScriptRoot 'setup-factory-gateway-tailscale.ps1'
if (-not (Test-Path -LiteralPath $stableSetup -PathType Leaf)) {
    throw "Stable Factory gateway setup not found: $stableSetup"
}

& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $stableSetup
if ($LASTEXITCODE -ne 0) {
    throw "Stable Factory gateway setup failed with exit code $LASTEXITCODE."
}

# Once the stable public dashboard exists and the GitHub Issue bus has been proven,
# move operational state off Google Drive. This helper is fixed/allowlisted and
# idempotent; it never executes arbitrary paths supplied by Drive or GitHub.
$migration = Join-Path $PSScriptRoot 'migrate-drive-to-github-bus.ps1'
if (-not (Test-Path -LiteralPath $migration -PathType Leaf)) {
    throw "Factory Drive migration helper not found: $migration"
}

& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $migration
if ($LASTEXITCODE -ne 0) {
    throw "Factory Drive migration failed with exit code $LASTEXITCODE."
}

Write-Host ''
Write-Host 'FACTORY_GATEWAY_SETUP_CANONICAL=TAILSCALE_FUNNEL'
Write-Host 'FACTORY_PRIMARY_BUS=GITHUB_ISSUE_7'
Write-Host 'FACTORY_DRIVE_ROLE=DOCUMENTATION_AND_EMERGENCY_FALLBACK'
