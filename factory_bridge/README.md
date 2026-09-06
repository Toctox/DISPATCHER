# FactoryBridge v0.3.0

Controlled local runner for PROJECT FACTORY. Drive commands contain only `id` and a known `action`; no shell command is accepted from Drive.

FactoryBridge now has two explicit terminal roles:

- `executor`: the only role that consumes `01_COMMANDS` and runs allowlisted actions.
- `panel`: read-only observer. It reads bridge status, the last command/result and executor heartbeat; it never consumes commands, creates results, archives commands, launches a shell, or starts child processes.

`start_factory_bridge.cmd` opens one CMD window for each role.

## Supported actions

- `bridge.ping`
- `system.info`
- `git.status`
- `git.pull` (only when local `allowGitPull=true`)
- `dispatcher.test`

`dispatcher.test` is fixed in code to the local Dispatcher entrypoint:

`powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <dispatcherWorkDir>\scripts\run-tick.ps1 -DryRun`

The Drive JSON cannot replace or extend that command. Unknown JSON fields are rejected. Legacy v0.1 config keys `dispatcherStart` and `dispatcherTest` are accepted only so an existing config keeps loading; their values are ignored for execution.

## Execution evidence

Every executed known command writes `02_RESULTS\RESULT__<id>.json`. For process-backed actions the RESULT preserves:

- `stdout`
- `stderr`
- `exitCode`
- `startedAt`
- `finishedAt`
- `durationMs`
- fixed `logicalCommand`

Existing RESULT IDs remain idempotent: if `RESULT__<id>.json` already exists, the command is not executed again and the input is archived. Processed command files continue to be moved to `03_ARCHIVE`.

The executor also maintains local observation files under `00_STATUS`:

- `executor.json` — PID/start/heartbeat used by the panel to determine ONLINE/OFFLINE.
- `last_command.json`
- `last_result.json`

These status files are informational only and do not authorize execution.

## Local config

`%LOCALAPPDATA%\FactoryBridge\config.json`

```json
{
  "bridgeRoot": "G:\\Meu Drive\\FACTORY_BRIDGE",
  "dispatcherWorkDir": "C:\\Users\\natan\\OneDrive\\Desktop\\projetos\\FactoryDispatcher",
  "dispatcherStart": "legacy value may remain; ignored by v0.3.0",
  "dispatcherTest": "legacy value may remain; ignored by v0.3.0",
  "allowGitPull": false,
  "commandTimeoutSec": 120,
  "pollIntervalMs": 1000
}
```

Required paths/variables:

- `%LOCALAPPDATA%` must exist so FactoryBridge can read `%LOCALAPPDATA%\FactoryBridge\config.json`.
- `bridgeRoot` must point to the synchronized `FACTORY_BRIDGE` root containing/receiving `01_COMMANDS`.
- `dispatcherWorkDir` is required only for `git.status`, `git.pull`, and `dispatcher.test`.
- `git.exe` must be on `PATH` for `git.status`/`git.pull`.
- `powershell.exe` must be available for `dispatcher.test`.

## Run modes

Executor only:

```powershell
.\FactoryBridge.exe --mode executor
```

Read-only panel only:

```powershell
.\FactoryBridge.exe --mode panel
```

Backward compatibility: invoking `FactoryBridge.exe` with no arguments starts the executor.

To open both terminal windows:

```cmd
start_factory_bridge.cmd
```

## Build on Windows

From the `factory_bridge` directory:

```powershell
go test ./...
go build -trimpath -ldflags "-s -w" -o FactoryBridge.exe .
```

Then start both terminals:

```powershell
.\start_factory_bridge.cmd
```

Or cross-build from another OS:

```bash
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "-s -w" -o FactoryBridge.exe .
```

## Test command

```json
{
  "id": "DISPATCHER-TEST-0002",
  "action": "dispatcher.test"
}
```
