# FactoryBridge v0.13.0

Controlled local agent runtime. Drive commands contain only `id` and a known `action`; no shell command, path, argument list, script body, branch, or commit SHA is accepted from Drive.

See [`docs/factory-bridge-agent-runtime-v1.md`](../docs/factory-bridge-agent-runtime-v1.md) for the architecture and operating model.

## Runtime roles

- `supervisor`: launches and monitors exactly one executor, preserving the last abnormal executor exit in status.
- `executor`: consumes `01_COMMANDS` and runs allowlisted actions.
- `panel`: read-only observer; it never consumes commands or launches child processes.

## Supported actions

### Runtime diagnostics

- `bridge.ping`
- `bridge.doctor`
- `system.info`

`bridge.doctor` checks Git, .NET, PowerShell, optional Go self-update capability, and configured Bridge/Dispatcher/ProjectHub directories.

### ProjectHub — atomic actions

- `projecthub.status`
- `projecthub.sync`
- `projecthub.build`
- `projecthub.test`
- `projecthub.start`
- `projecthub.stop`
- `projecthub.logs`
- `projecthub.validate`

`projecthub.status` reports the local branch, `HEAD`, `origin/main`, dirty state, ahead/behind counts, application health, and whether the running server has Bridge-recorded commit provenance.

`projecthub.sync` is fixed in code to:

1. `git status --porcelain` and refuse a dirty checkout;
2. refuse while ProjectHub is running;
3. `git fetch origin main`;
4. `git switch main`;
5. `git merge --ff-only origin/main`;
6. verify `branch=main`, clean checkout, and `HEAD == origin/main`.

No branch or SHA can be supplied by Drive.

`projecthub.build`, `projecthub.test`, and `projecthub.start` fail closed unless the checkout is canonical (`main`, clean, `HEAD == origin/main`). Results record `builtCommit`, `testedCommit`, or `startedCommit` respectively.

`projecthub.start` records the managed PID and commit in `00_STATUS/projecthub-process.json`. If `127.0.0.1:5080/health` is already healthy but there is no matching Bridge-managed commit provenance, start fails instead of accepting an unknown process.

`projecthub.stop` kills only the PID recorded in `00_STATUS/projecthub-process.json`. A healthy but untracked process is never killed automatically.

`projecthub.logs` returns only bounded tails of the fixed Bridge-managed ProjectHub stdout/stderr logs.

`projecthub.validate` owns a stopped-to-stopped validation lifecycle: canonical preflight, Release build, Release tests, managed start, health/provenance check, and managed stop. It refuses to run if ProjectHub is already active.

### ProjectHub — high-level actions (v0.13)

These actions reduce model/Drive round-trips while preserving the same allowlist boundary.

- `projecthub.snapshot` — runs `bridge.doctor`, canonical `projecthub.status`, and bounded `projecthub.logs`, returning one consolidated evidence payload without mutating either checkout.
- `projecthub.verify` — one canonical preflight followed by Release build and tests; it never starts the server. This is the preferred tight-loop action while iterating on implementation.
- `projecthub.refresh_validate` — performs safe fast-forward `projecthub.sync` and then the complete stopped-to-stopped `projecthub.validate` pipeline. This is the preferred post-commit validation action.

Typical autonomous worker cycle:

```text
projecthub.snapshot
  -> decide/edit in GitHub
  -> projecthub.refresh_validate
  -> if failure: inspect consolidated evidence / projecthub.logs
  -> correct and repeat
```

Fixed ProjectHub commands remain:

- restore: `dotnet restore ProjectHub.slnx --locked-mode`
- build: `dotnet build ProjectHub.slnx --configuration Release --no-restore`
- test: `dotnet test ProjectHub.slnx --configuration Release --no-build --no-restore`
- start: `dotnet run --project src/ProjectHub.Server --configuration Release --no-build --no-launch-profile --urls http://127.0.0.1:5080`

### PostgreSQL local

- `postgres.install`

The PostgreSQL action remains explicitly allowlisted and keeps local credentials on the notebook; secrets are not returned through Drive results.

### Existing bridge/dispatcher actions

- `git.status`
- `git.pull` (only when local `allowGitPull=true`)
- `git.push` (only when local `allowGitPush=true`)
- `dispatcher.test`
- `dispatcher.tick`

The legacy Dispatcher actions are kept for compatibility; ProjectHub delivery does not depend on reconstructing the old Project Factory orchestration.

## Drive command boundary

Valid command shape:

```json
{
  "id": "PROJECTHUB-REFRESH-VALIDATE-001",
  "action": "projecthub.refresh_validate"
}
```

Unknown fields are rejected by the JSON decoder.

## Execution evidence

Every command writes `02_RESULTS/RESULT__<id>.json` containing structured evidence such as:

- `status`
- `stdout` / `stderr`
- `exitCode`
- `startedAt` / `finishedAt` / `durationMs`
- fixed `logicalCommand`
- action-specific metadata, including ProjectHub commit provenance where applicable.

High-level actions additionally return ordered `meta.steps` and nested evidence for their component operations.

Existing RESULT IDs remain idempotent: an already-produced result prevents re-execution and the incoming command is archived.

`00_STATUS` contains observational state only:

- `supervisor.json`
- `executor.json`
- `last_command.json`
- `last_result.json`
- `projecthub-process.json` when a Bridge-managed ProjectHub server is active.

## Local config

`%LOCALAPPDATA%\FactoryBridge\config.json`

```json
{
  "bridgeRoot": "G:\\Meu Drive\\FACTORY_BRIDGE",
  "dispatcherWorkDir": "C:\\Users\\natan\\OneDrive\\Desktop\\projetos\\FactoryDispatcher",
  "projectHubWorkDir": "C:\\Users\\natan\\OneDrive\\Desktop\\projetos\\ProjectHub",
  "allowGitPull": false,
  "allowGitPush": false,
  "commandTimeoutSec": 120,
  "pollIntervalMs": 1000
}
```

`git.exe`, `dotnet.exe`, and `powershell.exe` must be available on `PATH` for the normal ProjectHub runtime. `go.exe` is required only for source-based FactoryBridge self-update.

## Self-update bootstrap

After the DISPATCHER checkout is updated, create the fixed marker:

`<bridgeRoot>\00_STATUS\factory-bridge-update.request.json`

Then issue `dispatcher.tick`. The updated `scripts/run-tick.ps1` calls `scripts/factory-bridge-update.ps1`, which runs `go test ./...`, builds `FactoryBridge.next.exe`, and schedules replacement only after successful tests/build. The prior executable is retained as `FactoryBridge.prev.exe`.

## Run

Supervisor:

```powershell
.\FactoryBridge.exe --mode supervisor
```

Executor only:

```powershell
.\FactoryBridge.exe --mode executor
```

Read-only panel:

```powershell
.\FactoryBridge.exe --mode panel
```

## Build on Windows

```powershell
go test ./...
go build -trimpath -ldflags "-s -w" -o FactoryBridge.exe .
```
