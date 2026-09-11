# Manual qualification — local agent on 18765

Use only for direct host qualification before the persistent installer is re-run:

```powershell
$env:FACTORY_LOCAL_AGENT_PORT='18765'
$base="$env:LOCALAPPDATA\FactoryNode\local-agent"
$py=(py -3 -c "import sys; print(sys.executable)" | Select-Object -First 1)
& $py "$base\agent.py"
```

Expected terminal line:

```text
Factory Local Agent listening on http://127.0.0.1:18765
```

In another PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:18765/health
```

Do not copy or publish `token.txt`.
