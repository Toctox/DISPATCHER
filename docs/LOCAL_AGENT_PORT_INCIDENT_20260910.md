# Local Agent bootstrap incident — Windows port 8765

Observed on the real Windows host during first manual qualification:

- CPython 3.13 resolved successfully.
- `agent.py` reached `ThreadingHTTPServer((HOST, PORT), Handler)`.
- Windows rejected bind on `127.0.0.1:8765` with `PermissionError: [WinError 10013]`.
- The failure is therefore a host socket/port restriction, not Python discovery or agent syntax.

Remediation:

- canonical local-agent port moved to `18765`;
- installer injects `FACTORY_LOCAL_AGENT_PORT=18765` into the scheduled-task runner;
- installer writes `%LOCALAPPDATA%\FactoryNode\local-agent\port.txt`;
- health qualification targets `http://127.0.0.1:18765/health`;
- persistent task remains `FactoryNode Local Agent`;
- startup failures are retained in `%LOCALAPPDATA%\FactoryNode\local-agent\logs\agent.log`.

The local agent remains loopback-only and authenticated for non-health operations.
