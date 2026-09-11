# Local Agent install recovery

If the first bootstrap reports `Local agent did not become healthy on 127.0.0.1:8765`, use the current installer from `main`.

The corrected installer:

- actively qualifies a real Python 3 interpreter instead of trusting the Windows App Execution Alias;
- compiles `local_agent/agent.py` before registration;
- installs `FactoryNode Local Agent` as a per-user Windows Scheduled Task;
- launches the persistent process outside transient FactoryBridge mission capture;
- writes `%LOCALAPPDATA%\FactoryNode\local-agent\logs\agent.log`;
- returns task result and the tail of that log on health failure;
- keeps the access token only in `%LOCALAPPDATA%\FactoryNode\local-agent\token.txt`.

The service binds only to `127.0.0.1:8765`. Do not publish or commit the token file.

Manual bootstrap is intentionally supported when the GitHub Issue BUS is rate-limited. Git transport (`git fetch`) is independent of the REST API polling quota used by FactoryBridge.
