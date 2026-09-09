# FactoryBridge — GitHub Runtime Bus

Status: **PRIMARY / migration complete / post-migration proof passed**

## Objective

Use GitHub only as a low-volume mailbox between ChatGPT (brain) and the persistent local FactoryBridge Mission Runtime (motor).

Heavy computation and artifacts remain local. GitHub Actions are not part of this path.

## Canonical bus

Repository: `Toctox/DISPATCHER`

Issue: `#7 — FACTORY RUNTIME BUS — canonical brain↔motor mailbox`

Protocol marker: `<!-- FACTORY_BUS_V1 -->`

Trusted command author: `Toctox`

Poll interval: 60 seconds while the executor is online.

## Message model

### MISSION

```json
{
  "protocol": "FACTORY_BUS_V1",
  "type": "MISSION",
  "id": "M-...",
  "kind": "projecthub.verify",
  "objective": "..."
}
```

Accepted kinds remain strictly allowlisted:

- `projecthub.verify`
- `projecthub.showcase`
- `projecthub.full_cycle`

Unknown JSON fields are rejected. No arbitrary shell, local path, URL, executable, argument list or secret is accepted from the bus.

### ACK

The motor posts a compact ACK before computation begins.

### CHECKPOINT

The motor posts only compact state:

- mission id/kind;
- DONE, BLOCKED or NEEDS_BRAIN;
- summary;
- validated commit;
- duration;
- FactoryBridge version.

Full logs, builds, traces, retries and test evidence remain under `%LOCALAPPDATA%\FactoryBridge` and `%USERPROFILE%\ProjectHub-Lab`.

## Authentication

The local runtime obtains the already-cached GitHub credential through the standard Git credential helper (`git credential fill`). The token is used in memory for GitHub REST calls and is never written to Drive, logs, checkpoints or issue comments.

If no write credential is available, the runtime refuses to consume a trusted mission message rather than executing without being able to acknowledge/result it.

## Rate-budget design

The bus intentionally polls at one request per minute: approximately 60 GET requests/hour while idle, plus only a few writes per mission. This is orders of magnitude below the normal authenticated REST budget and avoids commits-as-heartbeats.

No GitHub Actions minutes are consumed by local builds/tests.

## Idempotency

The runtime keeps its cursor in `%LOCALAPPDATA%\FactoryBridge\state\github-bus.json`.

Before re-running a mission after a transient GitHub write failure/restart, it checks local mission evidence. A completed local mission can therefore re-publish its checkpoint without repeating the expensive computation.

## Network and storage roles

- **GitHub Issue #7** — canonical brain↔motor control mailbox.
- **Tailscale Funnel** — human-facing dashboard/showcase access.
- **`%LOCALAPPDATA%\FactoryBridge\mailbox`** — local runtime mailbox/status compatibility root.
- **`%LOCALAPPDATA%\FactoryBridge`** — state, evidence, staging and runtime data.
- **`%USERPROFILE%\ProjectHub-Lab`** — human-testable ProjectHub showcase builds.
- **Google Drive `FACTORY_BRIDGE`** — canonical docs, compact brain state and emergency bootstrap/recovery only. It is no longer an execution queue, result store, watchdog or log store.

## Proven rollout

The rollout gate passed on 2026-09-09.

### Proof 1 — verify before detachment

`M-BUS-SMOKE-20260909-001`

- ChatGPT posted MISSION to Issue #7.
- Local FactoryBridge returned ACK.
- `projecthub.verify` executed locally.
- CHECKPOINT returned `DONE`.
- validated ProjectHub commit: `6494e7b51aae694f4559f836199cb78212976edc`.
- local execution duration: 30,440 ms.

### Proof 2 — full cycle before detachment

`M-BUS-FULL-20260909-001`

- MISSION/ACK traveled through Issue #7.
- local cycle completed sync → verify → showcase publish → local smoke.
- CHECKPOINT returned `DONE`.
- validated ProjectHub commit: `6494e7b51aae694f4559f836199cb78212976edc`.
- local execution duration: 34,043 ms.

### Proof 3 — full cycle after Drive detachment

`M-BUS-POSTMIGRATE-20260909-001`

This is the decisive migration proof. At this point the legacy Drive operational directories had already been removed and `bridgeRoot` had been relocated to the local mailbox.

- ChatGPT posted MISSION through Issue #7 only.
- local FactoryBridge returned ACK.
- local cycle completed sync → verify → showcase publish → local smoke.
- CHECKPOINT returned `DONE`.
- validated ProjectHub commit: `6494e7b51aae694f4559f836199cb78212976edc`.
- local execution duration: 26,176 ms.

Therefore normal brain↔motor execution no longer depends on Google Drive synchronization.

## Drive detachment

The runtime configuration was migrated from the synchronized Google Drive root to:

`%LOCALAPPDATA%\FactoryBridge\mailbox`

The legacy Drive operational directories (`00_STATUS`, `01_COMMANDS`, `01_INBOX`, `02_OUTBOX`, `02_RESULTS`, `03_ARCHIVE`) and old executable/build/log artifacts were removed after the new local root was activated.

The Drive keeps only compact brain/documentation material plus explicit recovery/bootstrap files. The obsolete Cloudflare quick-tunnel URL and redundant legacy v0.13 recovery launcher were also removed after the stable Tailscale path and canonical recovery script were confirmed.

## Recovery

If the GitHub bus becomes unavailable, recover the local runtime with `RECOVER_FACTORY_BRIDGE.cmd` or reinstall from `INSTALL_FACTORY_BRIDGE_CLEAN.cmd`. Re-enabling the old Drive execution queue is an emergency migration/recovery operation, not the normal operating mode.

The runtime still identifies as FactoryBridge `0.13.0`; this migration does not claim a semantic version bump.
