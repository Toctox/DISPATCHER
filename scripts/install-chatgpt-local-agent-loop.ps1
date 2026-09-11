$ErrorActionPreference='Stop'
$repoRoot=(Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source=Join-Path $repoRoot 'browser_extension\chatgpt_local_agent_loop'
$dest=Join-Path $env:LOCALAPPDATA 'FactoryNode\chatgpt-local-agent-loop'
if(-not (Test-Path (Join-Path $source 'manifest.json'))){throw 'canonical extension manifest missing'}
$health=Invoke-RestMethod 'http://127.0.0.1:18765/health' -TimeoutSec 3
if(-not $health.ok){throw 'Factory Local Agent is not healthy on 127.0.0.1:18765'}
if(Test-Path $dest){Remove-Item $dest -Recurse -Force}
New-Item -ItemType Directory -Path $dest -Force|Out-Null
Copy-Item -Path (Join-Path $source '*') -Destination $dest -Recurse -Force
Write-Host "CHATGPT_LOCAL_AGENT_LOOP_INSTALLED=$dest"
Write-Host "LOCAL_AGENT_HEALTH=$($health.ok)"
Write-Host 'Reload the unpacked extension in chrome://extensions if it was already loaded.'
