# FactoryBridge v0.2.0

Controlled local runner for PROJECT FACTORY. Drive commands contain only `id` and a known `action`; no shell command is accepted from Drive.

## Supported actions in this version

- `bridge.ping`
- `system.info`
- `git.status`
- `git.pull` (only when local `allowGitPull=true`)
- `dispatcher.test`

`dispatcher.test` is fixed in code to the local Dispatcher entrypoint:

`powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File <dispatcherWorkDir>\scripts\run-tick.ps1 -DryRun`

The Drive JSON cannot replace or extend that command. Legacy v0.1 config keys `dispatcherStart` and `dispatcherTest` are accepted only so an existing config keeps loading; v0.2.0 ignores their values for execution.

## Local config

`%LOCALAPPDATA%\FactoryBridge\config.json`

```json
{
  "bridgeRoot": "G:\\Meu Drive\\FACTORY_BRIDGE",
  "dispatcherWorkDir": "C:\\Users\\natan\\OneDrive\\Desktop\\projetos\\FactoryDispatcher",
  "dispatcherStart": "legacy value may remain; ignored by v0.2.0",
  "dispatcherTest": "legacy value may remain; ignored by v0.2.0",
  "allowGitPull": false,
  "commandTimeoutSec": 120,
  "pollIntervalMs": 1000
}
```

## Build on Windows

```powershell
go test ./...
go build -trimpath -ldflags "-s -w" -o FactoryBridge.exe .
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
