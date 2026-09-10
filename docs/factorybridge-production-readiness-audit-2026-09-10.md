# FactoryBridge — Production Readiness Closure Audit

Date: 2026-09-10
Scope: FactoryBridge v0.14.0, `Toctox/DISPATCHER`, GitHub Issue #7 control plane, local Windows runtime, Gateway, supervisor/executor, self-update path and recovery path.

## Executive decision

**Current state: OPERATIONAL, BUT NOT YET PRODUCTION-CLOSED.**

The Bridge is already capable enough to act as a practical local execution motor for ChatGPT workers. The runtime has durable mission journals, exact-commit binding, replay resistance, a GitHub MISSION/ACK/CHECKPOINT bus, supervisor/executor heartbeats, a localhost Gateway, self-update with rollback, a golden-recovery path and a risk-classified `system.command` capability.

However, the capability surface has grown beyond the original trust model. `system.command` now executes flexible PowerShell/CMD text under the Windows identity running FactoryBridge. The risk classifier is a policy layer, not an operating-system sandbox. Several observability, secret-handling, control-plane, self-update and recovery gaps remain. The P0 items below must be closed before declaring the Bridge production-safe and treating this infrastructure as finished.

This audit is intended to be the single closure reference. Future work should update this document rather than restart an ad-hoc investigation.

---

## 1. Live state observed on 2026-09-10

A sanitized local audit executed through FACTORY_BUS_V2 and returned `DONE` on runtime v0.14.0 / installed source `91c5641b02dc0e5f3a135ae1173d74c2eb0ff9e8`.

Observed facts:

- FactoryBridge runtime directory exists.
- Two FactoryBridge processes were active, consistent with supervisor + executor architecture.
- `FactoryBridge Supervisor` scheduled task was running.
- `FactoryBridge Admin Poller` was ready and its health state reported `OK` with `pendingUpdateResult=False`.
- `FactoryBridge Gateway Tunnel` task existed and was ready.
- durable state files existed for GitHub bus, admin poller health, installed runtime and last-known-good runtime.
- GitHub bus cursor was current at the time of collection.
- mission journals included at least: 67 `DONE`, 20 `NEEDS_BRAIN`, 6 `BLOCKED`.
- the audit checkpoint itself was truncated before the end of the collected report. This is live evidence that current checkpoint compaction can remove operationally important tail information.

The first audit probe was blocked because it contained `Invoke-Expression`; this correctly exercised the forbidden dynamic-execution policy. A second probe using explicit commands completed successfully.

---

## 2. Capability inventory

### 2.1 Control plane

FactoryBridge supports:

- GitHub Issue #7 as an append-only remote MISSION/ACK/CHECKPOINT bus;
- FACTORY_BUS_V2 mission integrity fields: mission ID, kind, objective, exact target commit, issue/expiry timestamps and canonical payload hash;
- trusted-author filtering;
- durable local reservation/journaling and duplicate mission protection;
- CONTROL messages for pause/resume/cancel;
- legacy V1 replay behavior without newly executing historical V1 commands.

### 2.2 Execution capabilities

Supported mission kinds in the current runtime include:

- `projecthub.verify`;
- `projecthub.full_cycle`;
- `projecthub.showcase`;
- `cafe.ccc.scan`;
- `system.command`.

`system.command` provides:

- PowerShell or CMD execution;
- command body up to 16 KiB;
- objective up to 32 KiB;
- optional working directory;
- timeout up to 1800 seconds;
- standard / guarded / approval / forbidden risk classes;
- complete stdout/stderr/exit-code evidence stored locally.

This makes the Bridge a general local development/operations motor rather than only a small set of hard-coded actions.

### 2.3 ProjectHub support

The repository contains dedicated ProjectHub execution paths for:

- sync and exact-commit checks;
- restore/build/test verification;
- showcase build and smoke validation;
- local workspace/worktree helpers;
- PostgreSQL local bootstrap/qualification work used by JOB0026.

### 2.4 Runtime resilience

The Bridge includes:

- supervisor + executor process separation;
- executor heartbeat during long-running actions;
- stale-heartbeat detection;
- automatic executor restart;
- durable local mission state;
- interrupted-mission recovery support;
- local runtime status and a read-only panel.

### 2.5 Gateway

A localhost HTTP Gateway listens on `127.0.0.1:8787` and exposes:

Public, minimized endpoints:

- `/public/health`;
- `/public/attention`;
- `/public/checkpoint`;
- `/public/showcase`.

Bearer-protected endpoints include:

- `/api/runtime/status`;
- `POST /api/missions`.

The token is randomly generated and persisted locally; comparison is constant-time. The Gateway sets no-store, nosniff and no-referrer protections, and the dashboard uses a restrictive CSP.

### 2.6 Self-update

FACTORY_ADMIN_V1 supports an explicit self-update approval flow:

- approval is read from GitHub;
- exact target SHA is validated;
- source is fetched;
- `go test ./...` runs before promotion;
- new binary is built in staging;
- previous binary is preserved;
- runtime is restarted;
- localhost health is checked;
- failure restores the previous executable;
- UPDATE_RESULT publishing is durable/retryable;
- admin scripts are synchronized from the approved source.

### 2.7 Recovery

A pinned `RECOVER_FACTORY_BRIDGE_GOLDEN.cmd` exists and can:

- fetch a known golden source SHA;
- test it;
- build a recovery binary;
- preserve the current binary;
- promote the golden binary;
- restart the supervisor.

This is useful but is not yet an offline-independent recovery mechanism.

---

## 3. P0 — blockers before production closure

### P0.1 Redact all remotely published output, including successful commands

**Finding:** failure diagnostics are being hardened, but successful `system.command` output currently becomes `Result.Output`, then checkpoint summary, without a mandatory secret-redaction pass.

**Impact:** a benign diagnostic that accidentally prints a token, password, connection string or authorization value can leak it into GitHub Issue #7 even when the command succeeds.

**Required closure:** one centralized `sanitizeForRemote()` path must be applied to every remotely published string: success summary, failure summary, blocked summary, UPDATE_RESULT diagnostic content and any future event payload. Local raw evidence may remain unsanitized only under a deliberately protected local evidence policy.

**Tests required:** success-output secret leak; failure-output secret leak; URI credentials; Authorization headers; PAT-like values; connection-string Password; PGPASSWORD; common token assignments; multiline secrets.

### P0.2 Replace prefix truncation with structured, tail-prioritized diagnostics

**Finding:** `checkpointEnvelope()` currently compacts `cp.Summary` to 800 characters, while generic `compact()` retains the prefix. The live deep audit was truncated before later facts. The proposed diagnostics PR builds a larger summary but it is still cut to 800 downstream.

**Impact:** the causal line at the end of build/test/runtime output can be lost even when the local evidence contains it. This directly caused repeated diagnostic missions during JOB0026.

**Required closure:** CHECKPOINT should carry structured bounded fields, not a monolithic summary:

- `phase`;
- `class`;
- `confidence`;
- `exitCode`;
- `error`;
- `evidenceTail`;
- `recommendedNextAction`;
- optional test summary.

If protocol compatibility requires a string summary, reserve explicit byte budgets for critical tail fields and never truncate them by prefix.

### P0.3 Complete Issue #16 instead of merging PR #18 as-is

PR #18 is not production-ready in its current form.

Observed defects:

- BOM was introduced before `package main` in `mission_runtime.go`;
- `Café` strings became mojibake (`CafÃ©`);
- PR body says `Closes #17` although the diagnostic requirement is Issue #16;
- only a subset of required failure classes/tests is implemented;
- TRX summarization is absent;
- 1800-character diagnostic output is still later compacted to 800 by the GitHub bus.

**Required closure:** rebuild the PR from clean `main`, preserve UTF-8 without BOM/regressions, implement the complete Issue #16 contract, then test end to end through the real Issue bus.

### P0.4 Treat `system.command` as privileged arbitrary shell, not as an allowlist substitute

**Finding:** the original mission architecture stated there was no arbitrary shell. Current v0.14 supports flexible PowerShell/CMD command text and uses regex classification to decide standard/guarded/approval/forbidden.

The classifier is useful policy friction but not containment. Examples of semantic execution paths that cannot be safely modeled by simple regex matching include:

- variables and call operator indirection;
- nested `cmd`/PowerShell invocations;
- `Start-Process` with arbitrary programs;
- .NET APIs, WMI/CIM and PowerShell providers;
- scripts invoked by path whose contents are not classified;
- package-manager lifecycle scripts;
- Git hooks/helpers/aliases;
- tools that themselves execute code.

**Impact:** a missed pattern executes with the same Windows identity as FactoryBridge.

**Required closure:** choose one of these production boundaries:

1. preferred: run general `system.command` under a restricted OS account / Windows Sandbox / WSL/container/VM with constrained filesystem and credentials;
2. alternatively: demote general shell to an explicitly privileged mission class and use typed, exact-commit scripts for routine automation.

The documentation must stop describing `system.command` as equivalent to a true allowlist.

### P0.5 Constrain working directories and execution roots

**Finding:** an absolute `workingDir` may resolve to any accessible directory; default can fall back to the user home. Shell commands themselves can access anything the Windows identity can access.

**Required closure:** define allowed roots for ordinary missions, e.g. FactoryNode workspaces, ProjectHub worktree and approved artifact directories. Access outside those roots should require a distinct privileged class or be blocked by the restricted OS identity.

### P0.6 Harden the trust model: payloadHash is integrity/dedupe, not authentication

**Finding:** mission `payloadHash` is SHA-256 over canonical payload fields. It is not keyed and not a signature. Anyone who can post as the trusted GitHub account/token can compute a valid hash.

**Required closure:** document the actual trust anchor as GitHub account/token + repository/control-plane governance. For sensitive/privileged operations, add a stronger independent authorization boundary: dedicated fine-grained credential, signed approval, separate local secret/HMAC, or another explicit second factor/channel.

The payload hash should continue to provide canonical integrity and replay/dedupe semantics, but must not be described as independent authentication.

### P0.7 CONTROL messages need freshness/replay guarantees

**Finding:** PAUSE/RESUME/CANCEL control handling is trusted-author based but does not use the full mission integrity/freshness envelope used by MISSION.

**Impact:** stale/replayed control comments can become ambiguous after state restoration/reset; control provenance is weaker than mission provenance.

**Required closure:** CONTROL V2 must include target mission, unique control ID/sequence, issuedAt, expiresAt and authenticated/canonical integrity fields. Duplicate controls must be idempotent and auditable.

### P0.8 Durable outbound CHECKPOINT queue

**Finding:** the main GitHub bus posts a checkpoint directly. If publishing fails after a mission finishes, the error is written to stderr. Unlike the admin UPDATE_RESULT path, there is no equivalent durable pending CHECKPOINT outbox/retry contract visible in the current bus implementation.

**Impact:** execution can finish locally while the remote brain never receives the terminal state.

**Required closure:** persist outbound ACK/CHECKPOINT before network publication, retry until acknowledged/published, and deduplicate by event ID. State cursor advancement must not make a terminal event irrecoverable.

### P0.9 Exact runtime identity in every result

`bridgeVersion=0.14.0` is insufficient after many materially different source commits share that version.

**Required closure:** every ACK/CHECKPOINT/health/update certificate should include:

- semantic version;
- exact installed source commit;
- build ID or binary SHA-256;
- protocol version;
- capability/risk-policy version.

This removes ambiguity about what code actually executed a mission.

---

## 4. P1 — reliability/autonomy closure

### P1.1 Replace giant nested-shell payloads with typed script missions

The session exposed repeated canonicalization/escaping problems caused by JSON containing a JSON string containing PowerShell containing Windows paths and quoting.

**Recommended design:** add a typed `script.run` / `repo.script` mission with:

- exact repository + commit;
- allowlisted script path;
- structured argument map/array;
- timeout;
- privilege class;
- expected output schema.

For recurring operations such as PostgreSQL qualification, define a specialized typed mission. The Issue should carry parameters, not 10–16 KiB of shell code.

Benefits:

- smaller payloads;
- fewer hash/canonicalization errors;
- code review of the script itself;
- easier redaction;
- stable phase/result schema;
- reduced risk-classifier ambiguity.

### P1.2 Canonical envelope composer

The mission-hash CLI is useful, but callers still manually construct nested envelopes.

Add a canonical CLI/API that accepts a typed mission input and outputs the **complete final FACTORY_BUS_V2 envelope** exactly as it must be posted, including hash. Add golden vectors shared by Go/PowerShell/Python for Unicode, `&`, `<`, `>`, backslashes, quotes and nested JSON.

### P1.3 Admin poller processes the wrong unit of work

Current admin poller scans comments newer than the last successful comment ID and retains only the last valid approval found in a poll.

**Risk:** if multiple valid approvals arrive between polls, earlier approvals can be skipped when the cursor advances to the last one.

**Required closure:** process valid approvals sequentially by comment ID, persist per-approval terminal state, then advance the cursor only after each item is durably terminal.

### P1.4 Admin health can hide degraded pending publication

`Retry-PendingUpdateResult()` can fail and set health to DEGRADED, but the flow can later write a new `OK` health such as “no new valid self-update approval” while a pending result still exists.

**Required closure:** health severity must be derived from durable state at the end of every poll. `pendingUpdateResult=true` after a failed publish must remain DEGRADED until delivery succeeds.

### P1.5 Self-update approval races with mutable `origin/main`

The updater requires the approved SHA to still equal `origin/main` at install time.

**Impact:** a legitimate exact-commit approval can fail merely because main advances before the poller runs. Conversely, the design ties promotion eligibility to the mutable main branch rather than to an immutable approved artifact/ref.

**Required closure:** approval should authorize an immutable commit/artifact that is already proven by policy. Branch/ruleset governance determines which commits are eligible before approval; runtime install should verify exact approved object/signature, not require that main has not moved.

### P1.6 Promotion certificate is too shallow

Current self-update runs Go tests/build and checks localhost `/public/health` + executor online. That proves process liveness, not full control-plane correctness.

**Required post-update certificate:** automatically test:

- binary/source SHA identity;
- supervisor/executor heartbeats;
- public health;
- private Gateway unauthorized/authorized behavior;
- GitHub poll read;
- one harmless MISSION -> ACK -> CHECKPOINT smoke;
- risk forbidden fail-closed probe;
- durable outbound retry path;
- admin poller health;
- config compatibility.

Only after this should UPDATE_RESULT say production promotion `DONE`.

### P1.7 Golden recovery is not independent enough

Current golden recovery depends on:

- GitHub/DNS/network;
- repository availability;
- local Git;
- local Go toolchain;
- pinned source still building on the current machine.

It validates process existence rather than full health.

**Required closure:** keep a local frozen prebuilt golden binary + SHA-256 + compatible config schema + recovery script that needs no network or compiler. Periodically test it. After recovery, verify `/public/health`, exact binary checksum and control-plane compatibility.

### P1.8 Process-tree termination

Supervisor kills the executor process when heartbeat is stale. Long-running child processes spawned by the executor may survive depending on Windows process semantics.

**Required closure:** run each mission in a Windows Job Object or equivalent process group so timeout/cancel/supervisor recovery terminates the complete descendant tree deterministically.

### P1.9 Crash-loop handling

Supervisor restarts indefinitely with a small capped delay and a monotonically increasing restart count.

**Required closure:** add stable-window reset, crash-loop detection, quarantine/degraded state and clear local/remote attention signal instead of endless restart churn.

### P1.10 Gateway supervision

Gateway is started in a goroutine attached to supervisor mode. If the Gateway listener exits, the error is logged but the supervisor process can keep running.

**Required closure:** supervise/restart the Gateway or make Gateway health part of the supervisor health state.

### P1.11 Heartbeat read failure policy

Supervisor deliberately treats local heartbeat read/parse errors as healthy to avoid false kills. This is sensible transiently, but persistent corruption can indefinitely suppress remediation.

**Required closure:** tolerate a bounded number/time window of read errors, then mark runtime DEGRADED and restart only when enough evidence exists.

### P1.12 Global mission serialization

A global mutex serializes missions. This is safe but prevents independent workloads from progressing and makes one slow mission a bottleneck.

**Required closure:** resource-aware lanes, for example:

- privileged/global lane: serialized;
- ProjectHub workspace lanes: parallel when separate worktrees/databases are used;
- read-only diagnostics lane;
- admin update lane exclusive with all execution.

Concurrency must remain bounded and auditable.

### P1.13 Mission-level deadline and cancellation contract

Command timeout exists, but complex missions can contain multiple stages/retries without one obvious total deadline. PAUSE/CANCEL only take effect at safe boundaries.

**Required closure:** add mission deadline + per-step deadline + process-tree cancellation + terminal reason taxonomy (`cancelled`, `timeout`, `failed`, `blocked`).

### P1.14 GitHub bus scaling and rotation

The control issue already carries a large history. The bus has finite pagination limits; the admin poller repeatedly enumerates up to 2000 comments. No ETag/rate-limit strategy or bus epoch rotation is evident.

**Required closure:** implement bus epochs/rotation and state migration before Issue #7 becomes an operational database. Monitor GitHub rate limit and last successful poll. Use pagination links/cursors robustly rather than a fixed 20-page ceiling.

### P1.15 Evidence retention and ACL

Raw stdout/stderr and mission payloads accumulate under LOCALAPPDATA. They may contain operationally sensitive content.

**Required closure:** define:

- retention TTL/size quota;
- rotation/archival;
- Windows ACL restricted to the runtime identity;
- secret-aware local classification;
- disk-pressure behavior;
- secure deletion policy where appropriate.

### P1.16 Gateway token lifecycle

The bearer token is static once generated and has no explicit rotation/expiry/audience mechanism.

**Required closure:** support rotation and state invalidation; keep Gateway loopback-only by default; if a tunnel is enabled, require explicit exposure policy, TLS/tunnel authentication and rate limiting.

---

## 5. P2 — maintainability and capability improvements

### P2.1 Risk policy as data + tests

Move command-risk patterns into a versioned policy module/table with explicit rule IDs and fixtures. Add tests for aliases, quoting, Unicode, nested shells and false positives.

### P2.2 Fuzz/property testing

Add fuzz/property tests for:

- GitHub envelope parser;
- payload canonicalization;
- mission ID/replay logic;
- risk classifier;
- command quoting;
- redaction;
- long output/truncation.

### P2.3 Deprecate or isolate legacy architecture

The repository still contains older Drive/browser/ChatGPT dispatcher configuration and newer GitHub FactoryBridge runtime concepts. Mark legacy paths clearly, disable by default, or move them to a dedicated legacy directory/repository to reduce cognitive and security surface.

### P2.4 Capability discovery

Expose a sanitized `CAPABILITIES` endpoint/checkpoint containing exact runtime commit and supported mission kinds, shells, timeout limits, risk-policy version and optional installed tools. Workers should not infer capabilities from old documentation.

### P2.5 Stable event schema

Add event ID/sequence/attempt fields so duplicate ACK/CHECKPOINT/UPDATE_RESULT messages can be ordered and reconciled deterministically.

### P2.6 Versioning discipline

Material capability changes should advance semantic version or at minimum capability-policy version. Do not allow multiple materially different runtimes to identify only as `0.14.0`.

---

## 6. Security model after closure

The intended production trust model should be explicit:

1. **Chat/worker is an untrusted planner relative to the OS.**
2. **GitHub/control-plane identity authenticates who may request work.**
3. **Mission canonicalization + digest protects exact intent/replay semantics, not identity by itself.**
4. **Risk policy decides whether a request is standard, guarded, privileged or forbidden.**
5. **OS identity/sandbox limits actual blast radius if the policy misses something.**
6. **Exact-commit script execution limits semantic drift.**
7. **Remote output is always sanitized.**
8. **Raw evidence is local, access-controlled and retained for a bounded period.**
9. **Self-update is a separate privilege domain with immutable approved artifacts and automatic rollback.**
10. **Every terminal state is durably delivered and independently reconcilable.**

Without item 5, regex policy should not be treated as sufficient containment for arbitrary shell.

---

## 7. Production closure gates

FactoryBridge may be declared `PRODUCTION_READY` only when every gate below has live evidence on the installed runtime.

### Gate A — Identity and provenance

- [ ] every ACK/CHECKPOINT includes exact source SHA + binary SHA + protocol/policy version;
- [ ] dedicated least-privilege GitHub credential is documented and verified;
- [ ] privileged authorization is distinct from a plain unkeyed payload digest.

### Gate B — Secret safety

- [ ] all remote success/failure outputs pass centralized redaction;
- [ ] mission objective secret scanner blocks obvious secret material before publish/execute;
- [ ] local evidence ACL and retention verified;
- [ ] live canary secret test proves no value reaches Issue #7.

### Gate C — Diagnostic completeness

- [ ] Issue #16 fully implemented;
- [ ] restore/build/test/DB/hash/timeout/environment/git/policy failures each diagnosed from one CHECKPOINT;
- [ ] TRX summary is included when relevant;
- [ ] long-output test proves causal tail survives;
- [ ] unknown evidence produces `class=unknown`, never invented certainty.

### Gate D — Execution containment

- [ ] ordinary commands restricted to approved roots and/or restricted OS identity;
- [ ] child process tree is terminated on timeout/cancel;
- [ ] privileged/forbidden policy live-tested;
- [ ] exact-commit typed script path available for routine automation.

### Gate E — Delivery durability

- [ ] outbound ACK/CHECKPOINT durable retry demonstrated under simulated GitHub outage;
- [ ] duplicate event reconciliation demonstrated;
- [ ] bus state loss/restart recovery demonstrated;
- [ ] CONTROL freshness/replay protections demonstrated.

### Gate F — Supervisor/runtime resilience

- [ ] executor crash auto-recovers;
- [ ] executor hang auto-recovers;
- [ ] Gateway crash auto-recovers or raises degraded health;
- [ ] crash-loop quarantine works;
- [ ] supervisor scheduled-task recovery verified after logon/reboot scenario.

### Gate G — Self-update

- [ ] multiple approvals in one poll are processed sequentially;
- [ ] pending UPDATE_RESULT remains degraded until delivered;
- [ ] immutable approved commit/artifact can be installed even if main subsequently advances;
- [ ] automatic promotion certificate passes;
- [ ] failed candidate rolls back and reports exact old/new runtime identities.

### Gate H — Disaster recovery

- [ ] offline prebuilt golden binary exists with checksum;
- [ ] recovery works with GitHub/network unavailable;
- [ ] recovery health + source/binary identity verified;
- [ ] golden artifact is periodically re-qualified.

### Gate I — Scale/maintenance

- [ ] bus rotation/epoch policy exists;
- [ ] evidence retention/rotation exists;
- [ ] disk-pressure behavior tested;
- [ ] rate-limit telemetry exists;
- [ ] capability endpoint reflects installed runtime, not documentation assumptions.

### Gate J — End-to-end production certificate

A final automated scenario must prove in one run:

1. control plane online;
2. harmless read mission succeeds;
3. guarded workspace write succeeds;
4. approval-class command without approval blocks;
5. forbidden command blocks;
6. intentional failed build/test returns actionable sanitized diagnosis in one CHECKPOINT;
7. successful command containing a canary secret in local stdout does **not** publish that canary remotely;
8. timeout kills descendants;
9. ProjectHub canonical verification passes;
10. restart preserves/reconciles durable state;
11. exact installed runtime identity is emitted;
12. final certificate is stored locally and posted in compact sanitized form.

Only then set the closure document status to `PRODUCTION_READY`.

---

## 8. Recommended implementation order

### Phase 0 — stop current leaks and blindness

1. rebuild PR #18 cleanly and fully implement Issue #16;
2. centralized remote redaction for success and failure paths;
3. structured checkpoint fields / tail-safe compaction;
4. exact runtime source/binary identity;
5. durable outbound checkpoint queue.

### Phase 1 — constrain authority

6. typed exact-commit script missions;
7. working-root restrictions;
8. restricted OS identity / sandbox for general shell;
9. CONTROL freshness/replay protection;
10. least-privilege control-plane credential.

### Phase 2 — make upgrades and recovery boring

11. fix admin-poller multi-approval and health-state bugs;
12. immutable self-update approval model;
13. automatic promotion certificate;
14. offline golden binary + checksum;
15. Gateway supervision and crash-loop handling.

### Phase 3 — scale without rediscovery

16. resource-aware concurrency;
17. bus epoch rotation/rate telemetry;
18. evidence TTL/quota/ACL;
19. capability discovery endpoint;
20. fuzz/property/chaos suite.

---

## 9. Definition of “do not return to Bridge work”

The Bridge infrastructure can be considered finished enough to leave alone when:

- all P0 items are closed;
- all production closure gates A–J have canonical live evidence;
- the final end-to-end certificate is green on the installed runtime;
- the recovery drill is green;
- documentation matches actual code capabilities;
- future workers can determine capability, runtime identity, health and failure cause without asking a human to inspect LOCALAPPDATA or constructing ad-hoc diagnostic missions.

After that point, new Bridge work should be triggered only by a concrete new product requirement, a discovered security defect, dependency/platform change, or failed periodic production certificate — not by routine ProjectHub development.
