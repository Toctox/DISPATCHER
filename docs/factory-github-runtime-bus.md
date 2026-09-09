# FactoryBridge — GitHub Runtime Bus

Status: **FACTORY_BUS_V2 is deployed and live-proven on installed FactoryBridge v0.14.0, source commit `120359f4a620c6d163c06885a8802ea41eea186f`. Core bus, durable reservation, controls, scheduler-yield, network recovery, self-update and `system.command` risk handling have canonical evidence in Issue #7. Public Gateway minimization/private-auth still require fresh revalidation on the current runtime.**

## Objective

Use GitHub as a low-volume structured control mailbox between ChatGPT (brain) and the persistent local FactoryBridge runtime (motor). Heavy computation and artifacts remain local; GitHub Actions are not required for this path.

## Canonical bus

- Repository: `Toctox/DISPATCHER`
- Issue: `#7 — FACTORY RUNTIME BUS — canonical brain↔motor mailbox`
- Protocol marker: `<!-- FACTORY_BUS_V2 -->`
- Trusted command author: `Toctox`
- Idle poll interval: 60 seconds with bounded exponential backoff after failures

Repository `main` may advance with documentation-only commits without changing the installed binary. Installed runtime identity is therefore tracked by the source commit reported by live ACK/CHECKPOINT evidence, not inferred from the current documentation HEAD.

## V2 mission envelope

```json
{
  "protocol": "FACTORY_BUS_V2",
  "type": "MISSION",
  "id": "M-...",
  "kind": "projecthub.verify",
  "objective": "...",
  "targetCommit": "<full authorized SHA>",
  "issuedAt": "<RFC3339>",
  "expiresAt": "<RFC3339>",
  "payloadHash": "<sha256>"
}
```

`payloadHash` is SHA-256 over the canonical JSON projection containing `id`, `kind`, `objective`, `targetCommit`, `issuedAt` and `expiresAt`.

A mission fails closed for malformed ID/kind/SHA/time, expiry, hash mismatch, target mismatch, same-ID/different-payload replay or other violated runtime invariants.

Currently deployed mission kinds include:

- `projecthub.verify`
- `projecthub.showcase`
- `projecthub.full_cycle`
- `system.command`

## Durable journal and replay control

Each mission is reserved locally before ACK under `%LOCALAPPDATA%\FactoryBridge\missions\<mission-id>\journal.json`.

State model:

```text
RECEIVED → QUEUED → ACKED → RUNNING → DONE / NEEDS_BRAIN / BLOCKED
```

Same mission ID with a different payload is rejected. Restart with a non-terminal journal and no terminal evidence fails closed to `NEEDS_BRAIN`; it does not blindly replay side effects.

## Scheduler and controls

All mission execution enters a process-wide scheduler boundary protecting the canonical ProjectHub checkout. GitHub, Gateway and compatibility inboxes may queue independently while mutually exclusive effects remain serialized.

Allowed controls:

- `PAUSE`
- `RESUME`
- `CANCEL`

Controls act at deterministic safe boundaries and do not forcibly kill an already-running external build/test subprocess.

Scheduler yield is live-proven: `M-YIELD-TARGET-20260909-1637` reached PAUSE; `M-YIELD-PROBE-20260909-1637` completed while the target remained paused; the target then RESUMEd and completed.

## Polling, pagination and recovery

The poller is independent from mission execution, paginates Issue comments in pages of 100 and backs off up to five minutes on failures before returning to the normal 60-second cadence after success.

The administrative poller had one live PowerShell sorting failure, was repaired, and subsequently completed successful self-update promotions. Earlier DNS failures (`Could not resolve host: github.com`) produced `NEEDS_BRAIN` as designed; `M-NETWORK-RECOVERY-20260909-1648` later completed a full canonical cycle, proving network recovery without repository corruption.

## system.command

`system.command` carries structured JSON inside `objective` and executes only through fixed PowerShell or CMD shells. The runtime classifies commands as standard, guarded, approval or forbidden.

Live evidence now covers:

- standard execution: `M-SYSCMD-STANDARD-20260909-1652` -> DONE;
- guarded execution: `M-VERSION-POLLER-20260909-1704` -> DONE with `risk=guarded`;
- approval without explicit approval: `M-RISK-APPROVAL-BLOCK-20260909-1723` -> BLOCKED in 53 ms;
- forbidden encoded/obfuscated pattern: `M-RISK-FORBIDDEN-BLOCK-20260909-1723` -> BLOCKED in 25 ms.

The approved-execution branch for approval-class commands has not been deliberately live-qualified and should not be described as proven until a safe reversible test is designed.

See `docs/factorybridge-system-command-risk-model.md`.

## Self-update and current runtime

`U-VERSION-ALIGN-20260909-1703` returned `UPDATE_RESULT: DONE` for source commit `120359f4a620c6d163c06885a8802ea41eea186f`: tests, build, promotion and post-update health validation passed.

`M-VERSION-CONFIRM-20260909-1708` then returned `ACK: ACCEPTED` and `CHECKPOINT: DONE`, both reporting `bridgeVersion: 0.14.0`. This is the canonical proof that the installed v0.14.0 binary is actually processing V2 missions.

## Public Gateway boundary

Public Tailscale endpoints are intended to remain read-only and metadata-minimized; private endpoints remain bearer-protected by design. Historical hardening removed mission objective, internal summary, decision question, detailed step output and local filesystem paths from public checkpoint data.

These external-boundary properties have not yet been freshly re-probed after the v0.14.0 promotion. Before declaring the full promotion gate closed, revalidate:

1. public checkpoint/attention payload contains only allowed metadata;
2. private endpoint without bearer returns HTTP 401.

## Security boundary still open

The protocol allowlist and risk classifier are not an OS sandbox. Builds, tests and approved commands run with the Windows identity running FactoryBridge. A repository-controlled build/test can access whatever that identity can access.

Priority hardening:

1. restricted runner account / WSL / container / VM;
2. dedicated fine-grained GitHub credential for the Runtime Bus;
3. protected repository/branch governance;
4. known-good golden recovery commit and bootstrap independent of arbitrary future `main`;
5. isolated worktrees/runners for safe parallelism.

## Current audit conclusion

The core ChatGPT -> GitHub -> FactoryBridge -> Windows -> GitHub -> ChatGPT control loop is operational and no longer a proof of concept. The remaining high-value work is boundary hardening and chaos qualification rather than proving basic transport or execution viability.

See `docs/factorybridge-live-audit-2026-09-09-1723.md` for the latest consolidated audit state.
