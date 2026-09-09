# FactoryBridge — Foundation Audit — 2026-09-09

Status: **implementation in progress; V2 source hardened, local deployment gate still pending**

## Scope

Audit the brain↔motor foundation after migration from Google Drive execution queues to GitHub Issue #7, with emphasis on correctness under long sessions, replay, crash, concurrency, provenance and public exposure.

## Fresh liveness proof

During this audit, `M-FOUNDATION-AUDIT-20260909-0050` was sent through the installed V1 GitHub bus. The local FactoryBridge returned ACK and then `CHECKPOINT DONE` after `projecthub.verify`, validating ProjectHub commit `6494e7b51aae694f4559f836199cb78212976edc` in 18,207 ms.

This proves the existing V1 transport/runtime remained alive before V2 source changes were promoted locally.

## Findings

### F1 — Long mission blocked control polling

Severity: High.

V1 called `executeMission` synchronously from the GitHub poller. A two-hour mission could therefore prevent the same service from seeing PAUSE/CANCEL/new decisions for two hours.

V2 mitigation: durable ACK then asynchronous mission execution; poller stays independent.

### F2 — Multiple execution entry points lacked a shared scheduler

Severity: High.

GitHub, authenticated Gateway and local compatibility inbox could invoke mission execution through separate goroutines against the same canonical checkout.

V2 mitigation: one process-wide mission execution mutex serializes mission effects.

### F3 — Crash after ACK had ambiguous replay semantics

Severity: High for future mutating operations.

V1 only advanced the GitHub cursor after complete execution. Crash timing could cause a mission to be seen again.

V2 mitigation: journal is persisted before ACK. Restart with non-terminal journal fails closed to `NEEDS_BRAIN`; no blind automatic replay.

### F4 — Mission authorization was not bound to an immutable ProjectHub SHA

Severity: High.

A V1 full-cycle mission synchronized whatever `origin/main` contained when the notebook eventually picked it up.

V2 mitigation: `targetCommit` is mandatory for hardened envelopes and checked against remote `origin/main` before execution and against the canonical checkout after sync.

### F5 — Same mission ID could not prove same payload

Severity: High for idempotency.

V2 mitigation: SHA-256 canonical payload hash stored in the durable journal. Same ID + different payload is rejected.

### F6 — Mission expiry was not enforced

Severity: Medium.

V2 mitigation: RFC3339 `issuedAt` + `expiresAt`; expired missions fail closed.

### F7 — Issue comment retrieval had a one-page blind spot

Severity: Medium.

V1 requested `per_page=100` but did not paginate.

V2 mitigation: bounded multi-page retrieval and tests covering 101 comments.

### F8 — Public Funnel checkpoint exposed too much context

Severity: Medium.

V1 public checkpoint could include objective, summary, decision question and step details.

V2 mitigation: public payload is metadata-only. Local paths and detailed mission content remain private/local.

### F9 — Protocol allowlist is not an OS sandbox

Severity: Critical boundary, not fixed by protocol V2.

`dotnet build` and `dotnet test` execute repository-controlled code with the Windows identity running FactoryBridge. A malicious build target or test can access anything available to that identity.

Required next boundary: isolated Windows account, VM, WSL/container or equivalent execution environment with restricted filesystem and credential access.

### F10 — GitHub credential scope is broader than the ideal Runtime Bus scope

Severity: Medium/High depending on credential permissions.

Current runtime obtains cached Git credentials through `git credential fill`. The token is kept in memory and never emitted to evidence, but its server-side scope is not narrowed by FactoryBridge.

Required next boundary: dedicated fine-grained credential for Issue read/write only, provisioned outside the bus.

### F11 — Main branch protection is not a runtime-enforced invariant

Severity: High supply-chain concern.

The runtime verifies local provenance but cannot guarantee GitHub account/repository branch policy. Repository administration must protect `DISPATCHER/main` and `ProjectHub/main` from unauthorized direct changes.

### F12 — Recovery should not trust arbitrary future main

Severity: High.

A clean installer that always fetches current `main` is safe against accidental build/test failures but not against a malicious future main that also changes tests.

Planned mitigation in this audit: preserve a known-good runtime commit as a golden recovery target after the V2 local promotion gate passes.

## Changes staged in source

- `FACTORY_BUS_V2` protocol and marker.
- immutable `targetCommit`.
- `issuedAt` / `expiresAt` TTL.
- SHA-256 canonical `payloadHash`.
- durable per-mission journal.
- same-ID/different-payload rejection.
- one process-wide mission execution scheduler lock.
- asynchronous GitHub mission execution.
- `PAUSE`, `RESUME`, `CANCEL` control messages.
- fail-closed restart recovery for non-terminal missions.
- multi-page issue comment retrieval.
- bounded exponential poll backoff.
- historical V1 evidence compatibility without executing new legacy V1 missions.
- metadata-only public Gateway checkpoint/attention output.
- explicit public endpoint links and restrictive HTTP headers.
- new hardening tests for tamper, expiry, replay, control, interruption and pagination.

## Chaos / endurance qualification plan

The following tests should be executed only after V2 clean installation passes normal tests/build and a recovery route has been confirmed:

| Test | Pass condition |
|---|---|
| Kill executor immediately after ACK | restart produces `NEEDS_BRAIN`, never blind duplicate execution |
| Kill executor mid-build/test | journal survives; no silent DONE |
| Kill supervisor | scheduled-task recovery restores one supervisor/executor |
| Reboot Windows mid-mission | interrupted journal reconciles fail-closed |
| Internet unavailable for 10 min | poller backs off and resumes without losing cursor |
| GitHub credential unavailable | trusted pending V2 mission is not executed |
| Same V2 mission submitted repeatedly | one durable reservation only |
| Same ID with different payload | BLOCKED |
| Expired mission | BLOCKED before execution |
| `main` moves after mission issue | target mismatch blocks execution |
| Dirty ProjectHub checkout | canonical operations refuse |
| Two missions arrive together | scheduler serializes execution |
| Gateway + GitHub mission together | scheduler serializes execution |
| More than 100 comments | all new messages discovered |
| Cursor state damaged | historical V1 missions do not newly execute |
| Unknown healthy process on ProjectHub port | stop refuses to kill it |
| Public checkpoint with secret-like objective | objective/details absent publicly |
| 2–3 hour mission | GitHub poller remains responsive to controls |

## Promotion ladder

### Gate A — source correctness

- `go test ./...` passes locally.
- `go build` passes locally.

### Gate B — clean runtime promotion

- previous binary preserved.
- one scheduled supervisor.
- executor heartbeat online.
- local source points to the hardened commit.

### Gate C — V2 end-to-end

- one exact-SHA V2 mission accepted.
- durable ACK observed.
- verify returns DONE for the authorized SHA.
- poller remains responsive during execution.

### Gate D — external boundary

- Tailscale public health/checkpoint/showcase still reachable from external curl.
- unauthenticated private endpoint remains 401.
- public checkpoint contains metadata only.

### Gate E — chaos

Run the qualification matrix above, beginning with process kill tests and only then reboot/network fault tests.

## What the notebook can become after these gates

A persistent private execution laboratory for ChatGPT: Git worktrees, .NET builds/tests, local PostgreSQL, Playwright/E2E, immutable showcase builds, traces/screenshots and long deterministic workflows, while the conversation carries only decisions and compact checkpoints.

The design intentionally does **not** place a second reasoning model on the notebook. The notebook is motor, durable state, laboratory and evidence store; ChatGPT remains the intermittent reasoning/control plane.
