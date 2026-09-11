# Local Agent bootstrap incident — Windows port 8765

Observed on the real Windows host during first manual qualification:

- CPython 3.13 resolved successfully.
- `agent.py` reached `ThreadingHTTPServer((HOST, PORT), Handler)`.
- Windows rejected bind on `127.0.0.1:8765` with `PermissionError: [WinError 10013]`.
- The failure is therefore a host socket/port restriction, not Python discovery or agent syntax.

Remediation:

- canonical local-agent port moved to `18765`;
- installer injects `FACTORY_LOCAL_AGENT_PORT=18765` in the Startup VBS launcher;
- installer writes `%LOCALAPPDATA%\FactoryNode\local-agent\port.txt`;
- health qualification targets `http://127.0.0.1:18765/health`;
- persistence is provided by `FactoryNode-Local-Agent.vbs` in the current user Startup folder, launching through `pythonw.exe`;
- `CREATE_NO_WINDOW` is applied to Local Agent child-process execution so PowerShell/CMD work does not create visible console windows.

The local agent remains loopback-only and authenticated for non-health operations.
