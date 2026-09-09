# FactoryBridge — GitHub Runtime Bus

Status: **FACTORY_BUS_V2 deployed and proven on the notebook at DISPATCHER commit `a0abdf1d1964bbfbe345d25cb5642ee087eeb077`; core bus, controls, scheduler-yield, network recovery and `system.command` execution have live evidence in Issue #7. Public Gateway minimization/private-auth should still be rechecked independently before treating every historical promotion-gate item as freshly closed.**

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
  "targetCommit": "<full 40-character authorized SHA>",
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
- the authorized target no longer matches the runtime-specific commit invariant;
- a canonical checkout no longer equals the authorized target after sync when the mission kind requires ProjectHub checkout validation;
- the same mission ID was already reserved with a different payload.

Accepted mission kinds are strictly allowlisted. The deployed runtime currently includes:

- `projecthub.verify`
- `projecthub.showcase`
- `projecthub.full_cycle`
- `system.command`

`system.command` is not unrestricted process execution. The mission carries structured command JSON inside `objective`; the runtime chooses a fixed PowerShell or CMD executable and applies the documented risk classifier. Standard and guarded operations may run automatically; approval-class operations require explicit `riskApproval:"approved"`; forbidden patterns never run automatically. See `docs/factorybridge-system-command-risk-model.md`.

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

All mission execution enters one process-wide execution scheduler boundary. GitHub bus, authenticated Gateway and compatibility local inbox may queue work independently, while mutually exclusive work against the canonical ProjectHub checkout remains serialized.

Paused work must not monopolize the global scheduler. This behavior is now proven live: `M-YIELD-TARGET-20260909-1637` reached `CONTROL_ACK: PAUSE`; while it remained paused, `M-YIELD-PROBE-20260909-1637` executed and completed `DONE`; after `RESUME`, the target mission also completed `DONE`.

Future worktree/container isolation may permit additional safe parallel read-only or isolated jobs without weakening checkout integrity.

## Independent polling and controls

After durable reservation and ACK, mission execution runs independently from polling. The GitHub poller remains available during long-running work.

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

Controls are applied at deterministic safe boundaries. FactoryBridge does not kill an already-running external build/test subprocess merely to satisfy a control message; it stops or pauses at the next safe boundary.

## Pagination and backoff

The V2 poller paginates issue comments in pages of 100 and can consume multiple pages in one poll. This removes the single-page blind spot present in the first implementation. Poll failures increase the next delay up to five minutes; a successful poll resets it to 60 seconds.

The administrative poller was also repaired after a PowerShell comment-ID sorting failure and now runs successfully on its scheduled cadence. The self-update path subsequently completed a full `UPDATE_RESULT: DONE` for runtime commit `a0abdf1d1964bbfbe345d25cb5642ee087eeb077`.

## Live V2 evidence — 2026-09-09

Canonical Issue #7 currently contains the following decisive evidence:

- self-update `A-SYSTEM-COMMAND-20260909-143009` → `UPDATE_RESULT: DONE` for `a0abdf1d1964bbfbe345d25cb5642ee087eeb077`;
- `M-YIELD-TARGET-20260909-1637` → `PAUSE`, later `RESUME`, then `DONE`;
- `M-YIELD-PROBE-20260909-1637` → `DONE` while the target mission remained paused, proving scheduler yield;
- `M-NETWORK-RECOVERY-20260909-1648` → `DONE`, proving GitHub DNS/fetch recovery after earlier transient `Could not resolve host: github.com` failures;
- `M-SYSCMD-STANDARD-20260909-1652` → `ACK: ACCEPTED` then `CHECKPOINT: DONE`, proving live `system.command` execution on target runtime commit `a0abdf1d1964bbfbe345d25cb5642ee087eeb077`.

The `system.command` smoke returned PowerShell `5.1.22621.4249`, Git `2.55.0.windows.5` and a live result for the scheduled task `FactoryBridge Admin Poller`.

Historical `NEEDS_BRAIN` states tied to the earlier transient DNS outage remain valid historical evidence but are superseded operationally by `M-NETWORK-RECOVERY-20260909-1648: DONE`.

## Historical V1 behavior

V1 remains important evidence of the migration:

- `M-BUS-SMOKE-20260909-001` — verify passed, 30,440 ms.
- `M-BUS-FULL-20260909-001` — full cycle passed, 34,043 ms.
- `M-BUS-POSTMIGRATE-20260909-001` — full cycle passed after Drive detachment, 26,176 ms.
- `M-FOUNDATION-AUDIT-20260909-0050` — fresh audit verify passed, 18,207 ms.

All validated ProjectHub commit `6494e7b51aae694f4559f836199cb78212976edc`.

After V2 cutover, historical V1 messages may be parsed for evidence compatibility, but an old V1 mission without existing local terminal evidence is never newly executed.

## Public Gateway minimization

Public Tailscale endpoints are intended to remain read-only. V2 hardening removes mission objective, internal summary, decision question, step output and local filesystem paths from public checkpoint data. Public attention should report only whether attention is required plus mission ID/state metadata.

The private Gateway remains bearer-protected by design.

Because the latest live validation sequence focused on the GitHub bus, scheduler, network recovery, self-update and `system.command`, re-run the public minimized-payload probe and unauthenticated-private-endpoint `401` check before using those two Gateway properties as fresh evidence for the current runtime.

## Current network/storage roles

- GitHub Issue #7 — canonical control mailbox.
- `%LOCALAPPDATA%\FactoryBridge` — journal, missions, state, evidence, logs, source and staging.
- `%LOCALAPPDATA%\FactoryBridge\mailbox` — local compatibility mailbox/status root.
- `%USERPROFILE%\ProjectHub-Lab` — human-testable immutable builds.
- Tailscale Funnel — human-facing read-only dashboard/showcase.
- Google Drive `FACTORY_BRIDGE` — compact brain state, documentation and emergency bootstrap/recovery only.

## Security boundary still open

The protocol allowlist and command risk classifier are not an OS sandbox. `dotnet build`, `dotnet test` and approved command execution run with the Windows identity running FactoryBridge. The next major hardening boundary is an isolated runner identity/VM/WSL/container with restricted access to personal files and credentials.

A dedicated fine-grained GitHub credential restricted to the Runtime Bus is also preferable to reusing a broader cached Git credential. The code never writes the credential to the bus or evidence, but credential scope remains an account-side configuration concern.

## Promotion status for V2

The original promotion checklist was:

1. `go test ./...`;
2. `go build`;
3. clean installer promotion;
4. supervisor/executor heartbeat healthy;
5. one V2 `projecthub.verify` mission with an exact `targetCommit` and valid payload hash;
6. ACK arrives while the poller remains responsive;
7. CHECKPOINT returns `DONE` for the authorized commit;
8. public Gateway probe confirms minimized payload and private endpoint still returns 401 without bearer.

The runtime is no longer merely staged: self-update/promotion to `a0abdf1d1964bbfbe345d25cb5642ee087eeb077` returned `UPDATE_RESULT: DONE`, repeated V2 missions have completed successfully, controls remain responsive, scheduler-yield is proven, network recovery is proven and `system.command` is proven live.

Do not infer fresh completion of item 8 solely from these Issue #7 mission results. Re-probe the public/private Gateway boundary when a fully refreshed promotion-gate certificate is required.
