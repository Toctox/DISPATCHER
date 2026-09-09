# FactoryBridge v0.13.0

FactoryBridge is the persistent local execution runtime used by ChatGPT as a controlled motor. The binary version remains `0.13.0`; the control protocol is independently versioned.

## Current architecture

```text
ChatGPT
  ↕
GitHub Issue #7 — FACTORY_BUS_V2
  ↕
FactoryBridge executor
  ↕
local Git / .NET / PostgreSQL / ProjectHub / tests
```

Normal execution no longer depends on Google Drive synchronization. Runtime state, journals, evidence, logs and staging live under `%LOCALAPPDATA%\FactoryBridge`. Human-testable ProjectHub builds live under `%USERPROFILE%\ProjectHub-Lab`.

Google Drive `FACTORY_BRIDGE` is retained only for compact brain state, documentation and emergency bootstrap/recovery.

Tailscale Funnel exposes a deliberately minimal read-only human dashboard. It is not the primary brain↔motor control path.

## Runtime roles

- `supervisor` — owns one executor lifecycle and the local gateway.
- `executor` — polls the GitHub Runtime Bus and executes allowlisted missions.
- `panel` — legacy read-only observer.

A compatibility local mailbox still exists under `%LOCALAPPDATA%\FactoryBridge\mailbox`, but GitHub Issue #7 is the canonical control bus.

## GitHub Runtime Bus V2

Repository: `Toctox/DISPATCHER`

Issue: `#7 — FACTORY RUNTIME BUS`

Marker: `<!-- FACTORY_BUS_V2 -->`

Trusted author: `Toctox`

Idle polling: approximately one request per minute with bounded exponential backoff on failures.

### Hardened mission envelope

A V2 mission is pinned to one ProjectHub commit and one validity window:

```json
{
  "protocol": "FACTORY_BUS_V2",
  "type": "MISSION",
  "id": "M-EXAMPLE-001",
  "kind": "projecthub.verify",
  "objective": "Verify the authorized ProjectHub commit",
  "targetCommit": "6494e7b51aae694f4559f836199cb78212976edc",
  "issuedAt": "2026-09-09T04:00:00Z",
  "expiresAt": "2026-09-09T05:00:00Z",
  "payloadHash": "<sha256 of canonical mission payload>"
}
```

The canonical payload hash covers `id`, `kind`, `objective`, `targetCommit`, `issuedAt` and `expiresAt`. An expired, malformed, tampered or moved-target mission fails closed.

Historical `FACTORY_BUS_V1` comments can still be parsed for evidence compatibility, but V1 missions that do not already have local evidence are never newly executed after the V2 cutover.

### Allowlisted mission kinds

- `projecthub.verify`
- `projecthub.showcase`
- `projecthub.full_cycle`

No mission can contain a shell command, executable, arbitrary path, arbitrary URL, branch, argument list or script body.

## Single scheduler and durable journal

Every mission execution passes through one process-wide execution mutex. GitHub, the authenticated local Gateway and the compatibility local inbox therefore cannot mutate the ProjectHub checkout concurrently.

Before execution, FactoryBridge persists `%LOCALAPPDATA%\FactoryBridge\missions\<mission-id>\journal.json`.

Journal states include:

```text
RECEIVED → QUEUED → ACKED → RUNNING → DONE / NEEDS_BRAIN / BLOCKED
```

The same mission ID with a different canonical payload is rejected. Reusing the same payload does not create a second reservation.

If the runtime restarts while a journal is non-terminal, FactoryBridge does **not** blindly replay the mission. It records `NEEDS_BRAIN` and asks for an explicit retry with a new mission ID. This trades automatic replay for protection against duplicate side effects.

## Independent poller and mission control

GitHub polling is no longer blocked by a long mission. After a mission has been durably queued and acknowledged, execution runs independently while the poller continues checking Issue #7.

V2 supports control messages for a known mission:

```json
{
  "protocol": "FACTORY_BUS_V2",
  "type": "CONTROL",
  "id": "M-EXAMPLE-001",
  "action": "PAUSE"
}
```

Allowed controls:

- `PAUSE`
- `RESUME`
- `CANCEL`

Controls are observed between deterministic mission stages. A single external build/test process already in progress is not forcibly killed mid-instruction; control takes effect at the next safe stage boundary.

## ProjectHub provenance

ProjectHub operations continue to fail closed unless their canonical requirements are satisfied:

- branch `main` where a canonical checkout is required;
- clean working tree;
- `HEAD == origin/main`;
- managed process provenance for start/stop operations.

V2 adds an authorization invariant: `origin/main` must still equal the mission's `targetCommit` immediately before execution. After `projecthub.full_cycle` sync, the canonical checkout is checked again against that target. If `main` moved between brain authorization and local execution, the mission stops instead of silently testing newer code.

## Mission behavior

`projecthub.verify` performs canonical Release build and tests without starting the server.

`projecthub.showcase` publishes a human-testable build into `%USERPROFILE%\ProjectHub-Lab`.

`projecthub.full_cycle` performs:

```text
safe sync
→ target SHA re-check
→ verify
→ showcase publish
→ local showcase smoke
```

Full logs, build products, traces and detailed evidence remain local. The GitHub bus receives compact ACK/CHECKPOINT messages only.

## Gateway boundary

Local bind address: `127.0.0.1:8787`.

Public read-only endpoints exposed through the Tailscale Funnel:

- `/public/health`
- `/public/attention`
- `/public/checkpoint`
- `/public/showcase`

Public checkpoint data is metadata-only. Mission objective, internal summary, decision question, execution steps and local filesystem paths are intentionally not exposed.

Private endpoints still require the local bearer token:

- `GET /api/runtime/status`
- `POST /api/missions`

The token remains local under `%LOCALAPPDATA%\FactoryBridge\gateway`.

## Local state

Primary paths:

```text
%LOCALAPPDATA%\FactoryBridge\
  bin\
  mailbox\
  missions\
  state\
  gateway\
  staging\
  source\

%USERPROFILE%\ProjectHub-Lab\
```

Local config:

`%LOCALAPPDATA%\FactoryBridge\config.json`

`bridgeRoot` should point to `%LOCALAPPDATA%\FactoryBridge\mailbox`, not Google Drive.

## Credentials

The current runtime obtains the already-cached GitHub credential using `git credential fill` and keeps the returned token in memory. It is never written to Issue comments, Drive checkpoints or runtime evidence.

A future dedicated fine-grained Issues-only credential remains preferable to a broad Git credential. Until that credential is provisioned, the protocol/allowlist and local runtime isolation are the principal execution boundaries.

## Recovery

Fast recovery of an installed binary:

`RECOVER_FACTORY_BRIDGE.cmd`

Clean source-based reinstall:

`INSTALL_FACTORY_BRIDGE_CLEAN.cmd`

The clean installer fetches source, runs `go test ./...`, builds in local staging and promotes only after tests/build pass. A golden recovery pin is maintained separately so emergency recovery does not need to trust an arbitrary future `main`.

## Verification before promotion

```powershell
go test ./...
go build -trimpath -ldflags "-s -w" -o FactoryBridge.exe .
```

The runtime must not be promoted when either command fails.

## Remaining hard boundary

The allowlist is not an operating-system sandbox. `dotnet build` and `dotnet test` execute repository-controlled code with the Windows identity running FactoryBridge. The next major security boundary is therefore an isolated build/test identity or VM/WSL/container with restricted access to personal files and credentials.
