param(
  [ValidateSet('install','health')]
  [string]$Action = 'install'
)

$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'FactoryNode'
$state = Join-Path $root 'state'
$tools = Join-Path $root 'tools'
$logs = Join-Path $root 'logs'
$artifacts = Join-Path $root 'artifacts'
$cache = Join-Path $root 'cache'
$tmp = Join-Path $root 'tmp'
$workspaces = Join-Path $root 'workspaces'
$browserRuntime = Join-Path $root 'browser-runtime'

@($root,$state,$tools,$logs,$artifacts,$cache,$tmp,$workspaces,$browserRuntime) | ForEach-Object {
  New-Item -ItemType Directory -Path $_ -Force | Out-Null
}

function Write-JsonFile([string]$Path,[object]$Value) {
  $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Get-CommandPath([string]$Name) {
  $c = Get-Command $Name -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  return $null
}

function Get-BrowserPath {
  $candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe')
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
  }
  return $null
}

function Invoke-Health {
  $bridgeHealth = Join-Path $env:LOCALAPPDATA 'FactoryBridge\state\installed-runtime.json'
  $node = Get-CommandPath 'node'
  $npm = Get-CommandPath 'npm'
  $git = Get-CommandPath 'git'
  $dotnet = Get-CommandPath 'dotnet'
  $python = Get-CommandPath 'python'
  $browser = Get-BrowserPath
  $pwPackage = Join-Path $browserRuntime 'node_modules\playwright-core\package.json'
  $health = [ordered]@{
    schema = 'FACTORY_NODE_HEALTH_V1'
    observedAt = [DateTime]::UtcNow.ToString('o')
    host = $env:COMPUTERNAME
    root = $root
    bridgeRuntimeState = (Test-Path -LiteralPath $bridgeHealth)
    tools = [ordered]@{
      git = [bool]$git
      dotnet = [bool]$dotnet
      node = [bool]$node
      npm = [bool]$npm
      python = [bool]$python
      browser = [bool]$browser
      playwrightCore = (Test-Path -LiteralPath $pwPackage)
    }
    paths = [ordered]@{
      browser = $browser
      workspaces = $workspaces
      artifacts = $artifacts
      cache = $cache
      logs = $logs
      tools = $tools
    }
  }
  Write-JsonFile (Join-Path $state 'node-health.json') $health
  $health | ConvertTo-Json -Depth 8 -Compress
}

if ($Action -eq 'health') {
  Invoke-Health
  exit 0
}

# Synchronize versioned FactoryNode tools from this repository checkout when available.
foreach ($name in @('factory-node-workspace.ps1','factory-node-browser.ps1')) {
  $src = Join-Path $PSScriptRoot $name
  if (Test-Path -LiteralPath $src) {
    Copy-Item -LiteralPath $src -Destination (Join-Path $tools $name) -Force
  }
}

# Prepare a lightweight browser automation runtime using the already-installed Edge/Chrome.
$packageJson = [ordered]@{
  name = 'factory-node-browser-runtime'
  private = $true
  version = '1.0.0'
  dependencies = [ordered]@{ 'playwright-core' = '1.63.0' }
}
Write-JsonFile (Join-Path $browserRuntime 'package.json') $packageJson

$npm = Get-CommandPath 'npm'
if (-not $npm) { throw 'npm is required for browser runtime installation' }
Push-Location $browserRuntime
try {
  & $npm install --ignore-scripts --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE" }
}
finally { Pop-Location }

$probe = @'
const { chromium } = require('playwright-core');
(async () => {
  const result = { status: 'UNKNOWN', channel: null, version: null };
  for (const channel of ['msedge', 'chrome']) {
    try {
      const browser = await chromium.launch({ channel, headless: true });
      result.status = 'OK';
      result.channel = channel;
      result.version = browser.version();
      await browser.close();
      console.log(JSON.stringify(result));
      process.exit(0);
    } catch (_) {}
  }
  result.status = 'BLOCKED';
  console.log(JSON.stringify(result));
  process.exit(2);
})();
'@
Set-Content -LiteralPath (Join-Path $browserRuntime 'probe-browser.js') -Value $probe -Encoding UTF8

$node = Get-CommandPath 'node'
if (-not $node) { throw 'node is required for browser probe' }
Push-Location $browserRuntime
try {
  $probeOutput = & $node (Join-Path $browserRuntime 'probe-browser.js')
  $probeExit = $LASTEXITCODE
}
finally { Pop-Location }
if ($probeExit -ne 0) { throw "browser automation probe failed: $probeOutput" }

$manifest = [ordered]@{
  schema = 'FACTORY_NODE_MANIFEST_V1'
  installedAt = [DateTime]::UtcNow.ToString('o')
  host = $env:COMPUTERNAME
  mode = 'windows-native'
  isolationTarget = 'wsl2'
  browserAutomation = [ordered]@{
    engine = 'playwright-core'
    version = '1.63.0'
    probe = $probeOutput
    bundledBrowserDownload = $false
  }
  destructiveCleanupEnabled = $false
  privilegeModel = 'user-context-default; privileged actions separate'
}
Write-JsonFile (Join-Path $state 'node-manifest.json') $manifest
Invoke-Health