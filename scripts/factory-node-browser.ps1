param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('probe', 'open')]
    [string]$Action,

    [string]$Url = 'about:blank',

    [ValidateSet('edge', 'chrome')]
    [string]$Browser = 'edge'
)

$ErrorActionPreference = 'Stop'

$programFiles = $env:ProgramFiles
$programFilesX86 = ${env:ProgramFiles(x86)}

$edgeCandidates = @(
    (Join-Path (Join-Path (Join-Path (Join-Path $programFiles 'Microsoft') 'Edge') 'Application') 'msedge.exe'),
    (Join-Path (Join-Path (Join-Path (Join-Path $programFilesX86 'Microsoft') 'Edge') 'Application') 'msedge.exe')
)
$chromeCandidates = @(
    (Join-Path (Join-Path (Join-Path (Join-Path $programFiles 'Google') 'Chrome') 'Application') 'chrome.exe'),
    (Join-Path (Join-Path (Join-Path (Join-Path $programFilesX86 'Google') 'Chrome') 'Application') 'chrome.exe')
)

$edge = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$chrome = $chromeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if ($Action -eq 'probe') {
    [ordered]@{
        status = 'OK'
        node = if (Get-Command node -ErrorAction SilentlyContinue) { (& node --version).Trim() } else { $null }
        npm = if (Get-Command npm -ErrorAction SilentlyContinue) { (& npm --version).Trim() } else { $null }
        npx = [bool](Get-Command npx -ErrorAction SilentlyContinue)
        edge = $edge
        chrome = $chrome
        playwrightGlobal = [bool](Get-Command playwright -ErrorAction SilentlyContinue)
    } | ConvertTo-Json -Compress
    exit 0
}

$exe = if ($Browser -eq 'chrome') { $chrome } else { $edge }
if (-not $exe) {
    throw "Requested browser '$Browser' was not found."
}

$uri = $null
if (-not [Uri]::TryCreate($Url, [UriKind]::Absolute, [ref]$uri)) {
    throw 'Url must be an absolute URI.'
}
if ($uri.Scheme -notin @('http', 'https', 'about')) {
    throw 'Only http, https and about URLs are allowed.'
}

$process = Start-Process -FilePath $exe -ArgumentList @('--new-window', $Url) -PassThru
[ordered]@{
    status = 'STARTED'
    browser = $Browser
    executable = $exe
    url = $Url
    processId = $process.Id
} | ConvertTo-Json -Compress

# This runner opens a browser only. Programmatic browser control will use a separately pinned Playwright package.