# Local Agent install recovery

If an older bootstrap reports that the Local Agent did not become healthy, use the current installer from `main`.

The corrected installer:

- actively qualifies a real Python 3 interpreter instead of trusting the Windows App Execution Alias;
- requires the matching `pythonw.exe` so the persistent process has no console window;
- compiles `local_agent/agent.py` before installation;
- removes the legacy per-user Scheduled Task `FactoryNode Local Agent` when present;
- removes the legacy visible `FactoryNode-Local-Agent.cmd` from Startup when present;
- installs `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FactoryNode-Local-Agent.vbs`;
- launches `agent.py` through `pythonw.exe` with `FACTORY_LOCAL_AGENT_PORT=18765`;
- validates `GET /health` on `127.0.0.1:18765`;
- keeps the access token only in `%LOCALAPPDATA%\FactoryNode\local-agent\token.txt`.

The service binds only to `127.0.0.1:18765`. Do not publish or commit the token file.

The VBS + `pythonw.exe` startup path is intentional: it avoids PowerShell/CMD windows stealing focus from the ChatGPT extension while preserving per-user automatic startup.
