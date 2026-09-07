# Autonomous WORKSTREAMS

`WORKSTREAMS` is an optional control sheet used only to keep governed Factory work moving when the execution `QUEUE` is empty.

It is not a second execution queue. `QUEUE` remains the sole source of executable jobs. `WORKSTREAMS` only authorizes the Dispatcher to enqueue one deduplicated `ORCHESTRATOR` planning checkpoint for an explicitly active change.

## Sheet contract

Create a sheet named `WORKSTREAMS` with these headers in row 1, in this order:

| Column | Header | Required | Meaning |
| --- | --- | --- | --- |
| A | `workstreamId` | yes | Stable activation identity. Change it only when intentionally starting a new planning cycle. |
| B | `changeId` | yes | Governed change, for example `CR-0005`. |
| C | `targetRef` | yes | Canonical Drive artifact ID the Orchestrator must inspect. |
| D | `status` | yes | Only `ACTIVE` is eligible. Any other value is ignored. |
| E | `priority` | no | Positive integer; defaults to `100`. Higher values are considered first. |
| F | `targetState` | no | Desired governed destination, for example `READY_FOR_IMPLEMENTATION`. It is context, not authority to bypass gates. |
| G | `requiredSources` | no | JSON array of additional source references supplied to the Orchestrator request. |
| H | `instructions` | no | Additional workstream-specific instructions. |

Example:

```text
workstreamId | changeId | targetRef | status | priority | targetState | requiredSources | instructions
WS-CR-0005   | CR-0005  | <drive-id> | ACTIVE | 100 | READY_FOR_IMPLEMENTATION | [] | Preserve Gate B.5 evidence requirements.
```

## Mechanical behavior

On an otherwise `IDLE` tick the autonomous dispatcher performs, in order:

1. publish eligible DECISION results;
2. materialize any staged `DISPATCH_PLAN` into `QUEUE` jobs;
3. enqueue an `ORCHESTRATOR` continuation when specialist receipts still need consolidation;
4. if none of the above applies, inspect `WORKSTREAMS` and enqueue at most one `AUTONOMOUS_WORKSTREAM_PLANNING` job.

The workstream planning job is `READ_ONLY`, requires a terminal receipt, and may write only to its attempt staging area. It instructs the Orchestrator to reconstruct canonical state and either produce one `DISPATCH_PLAN` or explicitly report that no governed work is actionable.

## Dedupe and flow control

The dedupe key is derived from `workstreamId`. The same workstream activation cannot continuously reseed itself. If a new planning cycle is intentionally required after a completed/no-op cycle, use a new `workstreamId`.

A workstream is never seeded while any job is `READY`, `WAITING_DEPENDENCIES`, or in an active leased/executing state. This prevents backlog generation from competing with current execution.

## Governance boundary

The Dispatcher does not choose product semantics, bypass gates, or decide which specialist agents are needed. The `ORCHESTRATOR` produces the `DISPATCH_PLAN`; the existing plan validator then restricts agents/executors, applies dedupe, preserves dependencies, and creates the actual `QUEUE` rows.