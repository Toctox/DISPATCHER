# FactoryBridge — GitHub Runtime Bus

Status: **V1 proven in production-like local use; V2 hardening staged on `main`, local promotion pending clean installer test**

## Objective

Use GitHub as a low-volume, structured control mailbox between ChatGPT (brain) and the persistent local FactoryBridge Mission Runtime (motor). Heavy computation and artifacts remain local. GitHub Actions are not required for this path.

## Canonical bus

- Repository: `Toctox/DISPATCHER`
- Issue: `#7 — FACTORY RUNTIME BUS — canonical brain↔motor mailbox`
- V2 marker: `<!-- FACTORY_BUS_V2 -->`
- Trusted command author: `Toctox`
- Idle poll interval: 60 seconds, with bounded exponential backoff after failures

## Why V2 exists

The original V1 proved transport and execution, but an audit identified four foundation risks that had to be addressed before expanding autonomy:

1. a long mission blocked the GitHub poller;
2. GitHub/Gateway/local inbox execution paths could race on one ProjectHub checkout;
3. a crash after ACK could cause ambiguous replay behavior;
4. a mission was not cryptographically bound to the exact ProjectHub commit authorized by the brain.

V2 directly addresses these four risks.

## V2 mission envelope

```json
{
  "protocol": "FACTORY_BUS_V2",
  "type": "MISSION",
  "id": "M-...",
  "kind": "projecthub.verify",
  "objective": "...",
  "targetCommit": "<full 40-character ProjectHub SHA>",
  "issuedAt": "<RFC3339>",
  "expiresAt": "<RFC3339>",
  "payloadHash": "<sha256>"
}
```

`payloadHash` is SHA-256 over the canonical JSON projection containing `id`, `kind`, `objective`, `targetCommit`, `issuedAt` and `expiresAt`.

A mission fails closed when:

- the ID is invalid;
- the kind is not allowlisted;
- target SHA is malformed;
- timestamps are malformed or expired;
- the payload hash does not match;
- `origin/main` no longer equals the authorized target commit;
- a canonical checkout no longer equals the authorized target after sync;
- the same mission ID was already reserved with a different payload.

Accepted mission kinds remain strictly allowlisted:

- `projecthub.verify`
- `projecthub.showcase`
- `projecthub.full_cycle`

No arbitrary shell, executable, path, URL, branch, script body or argument list is accepted from the bus.

## Durable journal

Each mission is reserved locally before ACK:

`%LOCALAPPDATA%\FactoryBridge\missions\<mission-id>\journal.json`

State model:

```text
RECEIVED → QUEUED → ACKED → RUNNING → DONE / NEEDS_BRAIN / BLOCKED
```

The journal stores the canonical payload hash. Same ID + different payload is rejected.

If FactoryBridge restarts with a non-terminal journal and no terminal evidence, it does not automatically replay the operation. It records `NEEDS_BRAIN` and asks for an explicit retry with a new mission ID. This is a deliberate fail-closed choice against duplicate side effects.

## Single scheduler

All mission execution enters one process-wide execution mutex. The GitHub bus, authenticated Gateway and compatibility local inbox can queue work independently, but only one mission can mutate/test the canonical ProjectHub checkout at a time.

This is the first scheduler boundary. Future worktree/container isolation may permit safe parallel read-only or isolated jobs without weakening this invariant.

## Independent polling and controls

After durable reservation and ACK, mission execution runs in a separate goroutine. The GitHub poller remains available during long-running work.

V2 control message:

```json
{
  "protocol": "FACTORY_BUS_V2",
  "type": "CONTROL",
  "id": "M-...",
  "action": "PAUSE"
}
```

Allowed controls:

- `PAUSE`
- `RESUME`
- `CANCEL`

Controls are applied at deterministic stage boundaries. FactoryBridge does not kill an already-running external build/test subprocess just to satisfy a control message; it stops or pauses at the next safe boundary.

## Pagination and backoff

The V2 poller paginates issue comments in pages of 100 and can consume multiple pages in one poll. This removes the single-page blind spot present in the first implementation. Poll failures increase the next delay up to five minutes; a successful poll resets it to 60 seconds.

## Historical V1 behavior

V1 remains important evidence of the migration:

- `M-BUS-SMOKE-20260909-001` — verify passed, 30,440 ms.
- `M-BUS-FULL-20260909-001` — full cycle passed, 34,043 ms.
- `M-BUS-POSTMIGRATE-20260909-001` — full cycle passed after Drive detachment, 26,176 ms.
- `M-FOUNDATION-AUDIT-20260909-0050` — fresh audit verify passed, 18,207 ms.

All validated ProjectHub commit `6494e7b51aae694f4559f836199cb78212976edc`.

After V2 cutover, historical V1 messages may be parsed for evidence compatibility, but an old V1 mission without existing local terminal evidence is never newly executed.

## Public Gateway minimization

Public Tailscale endpoints remain read-only. V2 hardening removes mission objective, internal summary, decision question, step output and local filesystem paths from public checkpoint data. Public attention only reports whether attention is required plus mission ID/state metadata.

The private Gateway remains bearer-protected.

## Current network/storage roles

- GitHub Issue #7 — canonical control mailbox.
- `%LOCALAPPDATA%\FactoryBridge` — journal, missions, state, evidence, logs, source and staging.
- `%LOCALAPPDATA%\FactoryBridge\mailbox` — local compatibility mailbox/status root.
- `%USERPROFILE%\ProjectHub-Lab` — human-testable immutable builds.
- Tailscale Funnel — human-facing read-only dashboard/showcase.
- Google Drive `FACTORY_BRIDGE` — compact brain state, documentation and emergency bootstrap/recovery only.

## Security boundary still open

The protocol allowlist is not an OS sandbox. `dotnet build` and `dotnet test` execute repository-controlled code with the Windows identity running FactoryBridge. The next major hardening boundary is an isolated runner identity/VM/WSL/container with restricted access to personal files and credentials.

A dedicated fine-grained GitHub credential restricted to the Runtime Bus is also preferable to reusing a broader cached Git credential. The code never writes the credential to the bus or evidence, but credential scope remains an account-side configuration concern.

## Promotion gate for V2

Do not call V2 locally deployed until all of the following pass on the notebook:

1. `go test ./...`;
2. `go build`;
3. clean installer promotion;
4. supervisor/executor heartbeat healthy;
5. one V2 `projecthub.verify` mission with an exact `targetCommit` and valid payload hash;
6. ACK arrives while the poller remains responsive;
7. CHECKPOINT returns `DONE` for the authorized commit;
8. public Gateway probe confirms minimized payload and private endpoint still returns 401 without bearer.

Until this gate passes, V1 remains the currently installed runtime behavior even though V2 source is present on repository `main`.
