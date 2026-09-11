# FactoryBridge v0.17.0

FactoryBridge is the persistent local execution runtime used by ChatGPT as a controlled motor. Runtime version, source commit, executable SHA-256, protocol version and policy version are independent identity fields and are emitted by the live runtime.

## Current architecture

```text
ChatGPT
  ↕
GitHub Issue #7 — FACTORY_BUS_V2
  ↕
FactoryBridge supervisor / executor / gateway
  ↕
local Git / .NET / PostgreSQL / ProjectHub / tests
```

Normal execution no longer depends on Google Drive synchronization. Runtime state, journals, durable outbound events, evidence, logs and staging live under `%LOCALAPPDATA%\FactoryBridge`. GitHub Issue #7 is the canonical brain↔motor control plane.

## Runtime roles

- `supervisor` — owns the executor and local gateway lifecycle.
- `executor` — polls the GitHub Runtime Bus and executes policy-authorized missions.
- `panel` — legacy read-only observer.

The Windows executor uses Job Object containment for spawned processes: a child is created suspended, attached to a `KILL_ON_JOB_CLOSE` job, then resumed. Cancellation or timeout terminates the process tree instead of only the direct child.

## GitHub Runtime Bus V2

Repository: `Toctox/DISPATCHER`

Issue: `#7 — FACTORY RUNTIME BUS`

Marker: `<!-- FACTORY_BUS_V2 -->`

Trusted author: `Toctox`

Idle polling is approximately once per minute with bounded exponential backoff on failures.

### Hardened mission envelope

A V2 mission binds its ID, kind, objective, exact target commit and validity window into the canonical `payloadHash`. The hash is an integrity/deduplication mechanism; it is **not** independent authentication. Trust also depends on the canonical GitHub author/control plane and local policy.

```json
{
  "protocol": "FACTORY_BUS_V2",
  "type": "MISSION",
  "id": "M-EXAMPLE-001",
  "kind": "script.run",
  "objective": "{\"repo\":\"dispatcher\",\"script\":\"scripts/factorybridge-smoke.ps1\",\"args\":{\"Message\":\"hello\"}}",
  "targetCommit": "<full reviewed commit SHA>",
  "issuedAt": "<RFC3339>",
  "expiresAt": "<RFC3339>",
  "payloadHash": "<SHA-256 of the canonical mission payload>"
}
```

Expired, malformed, tampered or moved-target missions fail closed. Historical V1 comments remain parseable as evidence but are never newly executed after the V2 cutover.

### Mission kinds and execution boundary

Product/canonical operations include `projecthub.verify`, `projecthub.showcase` and `projecthub.full_cycle`.

`script.run` / `repo.script` execute only scripts in the versioned allowlist, from an isolated worktree at the exact reviewed commit, with structured arguments validated as data. Script bodies and arbitrary executable arguments are not accepted from the remote message.

`system.command` is deliberately constrained:

- an ordinary literal grammar permits a very small set of safe read/write operations inside approved roots;
- indirect script/process execution is blocked and must use an allowlisted exact-commit script mission;
- destructive/sensitive operations require explicit risk approval;
- general shell additionally requires a separate local HMAC authorization, which is not derivable from GitHub message contents;
- approved roots are canonicalized through links/reparse points before use.

Regex risk classification is not an OS sandbox. Repository-controlled build/test code still runs with the Windows identity of the FactoryBridge service; stronger OS identity isolation remains a separate hardening boundary.

## Durable journal and outbound queue

Every mission is durably reserved before execution. Journal states include:

```text
RECEIVED → QUEUED → ACKED → RUNNING → DONE / NEEDS_BRAIN / BLOCKED
```

The same mission ID with a different canonical payload is rejected. A restart does not blindly replay a non-terminal mission; it produces fail-closed recovery evidence and requires an explicit new mission ID for side-effecting retry.

ACK/CHECKPOINT publication uses a disk-backed outbound queue. Events have stable IDs, sequence and attempt metadata. Publication is at-least-once; after an uncertain POST, the runtime reconciles the stable event ID before retrying. Terminal local evidence is reconciled back into the queue after crash windows.

## CONTROL V2

Control messages carry `controlId`, monotonic `controlSequence`, `issuedAt`, `expiresAt` and integrity data. The local ledger rejects expired/future controls, replayed/older sequences, altered reuse of IDs and corrupted state. Allowed actions remain `PAUSE`, `RESUME` and `CANCEL` at defined safe boundaries.

## Failure CHECKPOINT contract

A terminal failed `system.command` or typed script CHECKPOINT is designed to be diagnosable without a second probe. The remote envelope retains a compact compatibility `summary` and adds a structured `diagnostic` object derived from durable local evidence:

```json
{
  "diagnostic": {
    "phase": "build",
    "exitCode": 1,
    "error": "exit status 1",
    "class": "compile",
    "confidence": "high",
    "evidence": "file.cs(...): error ...",
    "stderrTail": "...final sanitized stderr...",
    "stdoutTail": "...final sanitized stdout...",
    "recommendedNextAction": "fix the first compiler or analyzer error, then rerun build",
    "trx": {
      "total": 10,
      "passed": 8,
      "failed": 1,
      "skipped": 1,
      "failedTests": ["Example.FailingTest"]
    }
  }
}
```

The stable taxonomy includes `dependency_lock`, `compile`, `test_assertion`, `database_connectivity`, `database_schema_or_sql`, `timeout`, `environment_missing`, `git_state`, `permission_or_policy`, `integrity_payload_hash` and `unknown`. Low-confidence evidence is reported as unknown instead of promoted to fact.

Remote human text is sanitized at the envelope serialization boundary before event hashing, durable outbox persistence or GitHub POST. Password/token/bearer patterns are replaced with `[REDACTED]`. Compact step evidence is tail-preserving; complete raw command results remain only in local `evidence.Results`.

## ProjectHub provenance

Canonical ProjectHub operations fail closed unless their expected branch/commit/cleanliness and managed-process provenance requirements hold. A mission's authorized `targetCommit` is rechecked before execution so a moving branch cannot silently substitute newer code.

`projecthub.full_cycle` performs:

```text
safe sync
→ target SHA re-check
→ verify
→ showcase publish
→ local showcase smoke
```

## Gateway boundary

Local bind address: `127.0.0.1:8787`.

Public endpoints are read-only and metadata-limited. Private APIs require the local gateway bearer token. The token remains under `%LOCALAPPDATA%\FactoryBridge\gateway`; token lifecycle/rotation is tracked as production-hardening work.

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

%LOCALAPPDATA%\FactoryNode\
  workspaces\
  artifacts\
```

Local config: `%LOCALAPPDATA%\FactoryBridge\config.json`.

## Credentials

The runtime obtains the already-cached GitHub credential through `git credential fill` and keeps it in memory. It must never be written to Issue comments, Drive checkpoints or runtime evidence. A dedicated least-privilege credential remains preferable to a broad Git credential.

## Recovery and promotion

`RECOVER_FACTORY_BRIDGE.cmd` provides fast recovery of an installed binary. `INSTALL_FACTORY_BRIDGE_CLEAN.cmd` performs a clean source-based reinstall. `RECOVER_FACTORY_BRIDGE_GOLDEN.cmd` is a pinned source-based recovery path today; it still depends on GitHub + Git + Go and therefore does **not** yet satisfy the formal offline-golden recovery gate.

The Admin Poller processes self-update approvals sequentially with durable processed/successful cursors. A terminal failed update does not block all later approvals forever, and a pending `UPDATE_RESULT` must be delivered before advancing to the next approval.

Before promotion, the updater runs tests and builds into staging. Runtime replacement is followed by health validation and rollback on failure. Formal production closure additionally requires an immutable-target update flow, an E2E promotion certificate and offline frozen-binary recovery proof.

## Verification before promotion

```powershell
go test ./...
go build -trimpath -ldflags "-s -w -X main.buildSourceCommit=<exact-sha>" -o FactoryBridge.exe .
```

A runtime must not be promoted when tests, build or post-update health fail.
