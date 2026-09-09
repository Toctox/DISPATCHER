# FactoryBridge — Improvement Backlog — 2026-09-09

Status: **core runtime operational; v0.14.0 live-proven; hardening backlog consolidated for staged delivery.**

## Purpose

This document separates improvements into three classes:

1. changes that can be delivered through FactoryBridge self-update;
2. changes that require local/OS or GitHub account configuration outside the runtime binary;
3. future architecture work that should be introduced only after the current control plane remains stable.

## Package prepared for the next self-update

### 1. Admin poller comment-ID robustness

Canonical source previously retained the PowerShell sorting form that had already failed live when GitHub comment IDs were presented in a shape that produced an Object[] -> Int64 conversion error.

Improvement:

- flatten only valid comment objects;
- normalize IDs through string conversion before Int64 conversion;
- reject unparsable IDs instead of aborting the poll cycle;
- identify the poller as `FactoryBridge-Admin-Poller/2`.

### 2. Durable admin-poller health

New state file:

`%LOCALAPPDATA%\FactoryBridge\state\admin-poller-health.json`

It records:

- `status` (`OK`, `DEGRADED`, `ERROR`);
- compact diagnostic message;
- `observedAt`;
- last successful approval comment ID;
- whether an UPDATE_RESULT is still pending publication.

This removes dependence on Task Scheduler exit code alone when diagnosing the administrative control plane.

### 3. Durable retry for UPDATE_RESULT publication

Previously a successful local self-update could lose its GitHub `UPDATE_RESULT` if the final publication failed, because the publication exception was swallowed and the approval cursor could still advance.

Improvement:

- write the compact UPDATE_RESULT envelope to a durable local pending file before publication;
- retry that pending envelope on the next poll;
- delete it only after successful publication;
- avoid re-running the already-completed self-update solely because GitHub publication was temporarily unavailable.

Pending file:

`%LOCALAPPDATA%\FactoryBridge\state\admin-poller-pending-update-result.json`

### 4. Self-update now carries administrative control assets

The original self-updater promoted only `FactoryBridge.exe`. That meant a runtime binary could advance while the installed admin poller/updater scripts remained stale.

Improvement:

- synchronize the approved commit's `factory-bridge-autoupdate.ps1` and `factory-bridge-admin-poller.ps1` into `%LOCALAPPDATA%\FactoryBridge\admin` after successful runtime health validation;
- record `adminAssetsSynced` in installed/update state;
- report explicitly if runtime promotion succeeded but admin-asset synchronization needs attention.

### 5. Previous-known-good runtime state

The updater now records the previously installed source commit before a successful promotion in:

`%LOCALAPPDATA%\FactoryBridge\state\last-known-good-runtime.json`

This supplements, but does not replace, the independently pinned golden recovery command.

## Highest-priority follow-up improvements

### P0 — External Gateway qualification

Re-probe against the current runtime:

- public health/checkpoint endpoints expose only minimized metadata;
- mission objective, local paths, detailed stdout/stderr and decision content remain absent publicly;
- private endpoints return HTTP 401 without bearer authentication;
- restrictive response headers remain present.

This is a qualification task, not a reason to expand public capability.

### P0 — Isolated execution identity

Current `system.command`, builds and tests run with the Windows identity hosting FactoryBridge. The protocol classifier is not an OS sandbox.

Target options, in preferred order depending on operational fit:

- dedicated restricted Windows account;
- WSL2 runner with narrow mounts;
- container/VM runner with explicit bind mounts and no inherited personal credentials.

The runner should receive only the repository/worktree, build cache needed for the mission and explicit credentials required for that mission.

### P0 — Dedicated Runtime Bus GitHub credential

Replace broad cached Git credentials for bus operations with a fine-grained credential provisioned outside mission payloads and restricted as narrowly as possible to the canonical Runtime Bus repository/issue operations.

Credential material must never be written to Issue #7, public Gateway payloads, mission evidence or repository files.

### P0 — GitHub supply-chain governance

FactoryBridge cannot enforce account-side branch policy. Configure repository governance so runtime source cannot be silently replaced through an unreviewed direct write.

Targets:

- protect `DISPATCHER/main`;
- protect product repositories used by canonical missions;
- require strong account authentication;
- prefer reviewed/verified promotion paths for runtime-affecting commits.

### P1 — Recovery independence from current main

Keep both:

- pinned golden recovery source;
- last-known-good local runtime metadata/binary.

Future recovery tooling should be able to restore a known-good commit without trusting a newly advanced `main` or tests modified by that same future commit.

### P1 — Safe parallelism with isolated worktrees

The global scheduler currently favors integrity over throughput.

Introduce a worktree/runner allocator so read-only or isolated jobs can run concurrently while canonical mutable operations remain serialized.

Required properties:

- unique worktree per mission;
- no shared mutable build/output directory;
- explicit cleanup lifecycle;
- journal records worktree path/commit;
- scheduler understands resource classes rather than one global mutex only.

### P1 — Stronger cancellation semantics

`PAUSE` and `CANCEL` currently act at safe boundaries; an external build/test subprocess already in progress is not forcibly killed.

Future improvement:

- process-group/job-object ownership for spawned commands;
- cooperative cancellation first;
- bounded grace period;
- hard termination only for the mission-owned process tree;
- evidence distinguishing graceful cancellation from forced termination.

### P1 — Offline control-plane resilience

GitHub/DNS outage currently delays new remote missions and checkpoints.

Future improvement:

- durable outbound checkpoint queue;
- explicit network state in health;
- retry with jitter/backoff;
- never replay mission side effects just because publication failed;
- optional local operator inbox that is clearly secondary to the canonical bus.

### P1 — Runtime identity in every compact checkpoint

Version alone is weaker than exact provenance.

Add compact runtime identity fields where useful:

- semantic bridge version;
- installed runtime source SHA;
- protocol version.

Do not expose local filesystem paths or secrets.

### P1 — Automated promotion certificate

After self-update, automatically run a compact qualification suite and write one local certificate plus a minimized GitHub checkpoint covering:

- binary health;
- executor heartbeat;
- admin poller health;
- exact installed source SHA;
- risk classifier standard/guarded basic smoke;
- public/private Gateway boundary status when network conditions permit.

### P2 — Risk-classifier maintainability

Improve policy quality without turning it into a false sandbox:

- keep rule IDs stable and versioned;
- add table-driven regression cases for common PowerShell/CMD variants;
- detect command chaining variants consistently;
- keep approval and forbidden rules explicit;
- expose only compact rule ID/reason in remote checkpoint, full command evidence locally.

### P2 — Evidence retention and rotation

Long sessions will accumulate journals, stdout/stderr and local artifacts.

Add:

- retention policy by age/state;
- bounded log sizes;
- archival of terminal mission summaries;
- preservation rules for NEEDS_BRAIN/BLOCKED/security events;
- disk-usage health thresholds.

### P2 — Structured capabilities discovery

Expose a local/private machine-readable capability snapshot so the reasoning plane can know what the motor can actually do without guessing.

Possible fields:

- runtime version/SHA;
- supported mission kinds;
- supported shells;
- relevant installed tools/versions;
- configured project roots;
- isolation mode;
- scheduler capacity;
- Gateway/admin-poller health.

Public endpoints must not expose sensitive configuration.

### P2 — Mission timeout hierarchy

Clarify and enforce separate limits for:

- command execution timeout;
- mission total timeout;
- poller HTTP timeout;
- control grace period;
- self-update health timeout.

Timeout terminal states should distinguish timeout from generic execution failure.

### P2 — Installer/setup convergence

Ensure clean install, autostart setup, recovery and self-update install the same canonical administrative scripts/config contract. Avoid hidden divergence between bootstrap files and the scripts used after promotion.

## Improvements intentionally not solved in the runtime alone

The following require operator/account/OS work and must not be falsely reported as fixed by a self-update:

- restricting the Windows account/ACL boundary;
- creating a WSL/container/VM sandbox;
- narrowing GitHub credential server-side permissions;
- enabling repository branch/ruleset protection;
- hardening the user's GitHub account authentication;
- ensuring the physical notebook is available, powered and network-connected.

## Delivery principle

Do not batch every architectural idea into one high-risk promotion. Use small, exact-SHA self-update packages with tests, live health validation, rollback and a canonical `UPDATE_RESULT` for each runtime-affecting increment.
