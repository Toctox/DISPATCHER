# FactoryBridge — GitHub Runtime Bus

Status: rollout in progress

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

Full logs, builds, traces, retries and test evidence remain under `%LOCALAPPDATA%\FactoryBridge` and `ProjectHub-Lab`.

## Authentication

The local runtime obtains the already-cached GitHub credential through the standard Git credential helper (`git credential fill`). The token is used in memory for GitHub REST calls and is never written to Drive, logs, checkpoints or issue comments.

If no write credential is available, the runtime refuses to consume a trusted mission message rather than executing without being able to acknowledge/result it.

## Rate-budget design

The bus intentionally polls at one request per minute: approximately 60 GET requests/hour while idle, plus only a few writes per mission. This is orders of magnitude below the normal authenticated REST budget and avoids commits-as-heartbeats.

No GitHub Actions minutes are consumed by local builds/tests.

## Idempotency

The runtime keeps its cursor in `%LOCALAPPDATA%\FactoryBridge\state\github-bus.json`.

Before re-running a mission after a transient GitHub write failure/restart, it checks local mission evidence. A completed local mission can therefore re-publish its checkpoint without repeating the expensive computation.

## Network roles

- GitHub Issue #7: brain↔motor control mailbox.
- Tailscale Funnel: human-facing dashboard/showcase access.
- Google Drive: fallback and canonical documentation during migration; not the target primary execution bus.

## Rollout gate

The GitHub bus becomes primary only after an end-to-end smoke proves:

1. ChatGPT posts a MISSION to Issue #7.
2. Local FactoryBridge reads it without Drive participation.
3. Motor posts ACK.
4. ProjectHub verify runs locally.
5. Motor posts compact CHECKPOINT.
6. Repeated polling stays low-volume and no token/path is leaked.

Until that proof passes, the legacy Drive command path remains available for recovery/bootstrap.
