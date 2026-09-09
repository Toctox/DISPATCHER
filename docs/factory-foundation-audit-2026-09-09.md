# FactoryBridge — Foundation Audit — 2026-09-09

Status: **V2 live proof passed; adversarial audit found one terminal-replay defect; source fix prepared and requires one re-promotion before closure.**

## Scope

This audit tests the foundation of the ChatGPT ↔ GitHub ↔ FactoryBridge ↔ ProjectHub execution path rather than only testing a successful happy path.

Canonical control plane:

```text
ChatGPT
  ↕
Toctox/DISPATCHER Issue #7 — FACTORY_BUS_V2
  ↕
FactoryBridge on Windows notebook `supremo`
  ↕
ProjectHub local checkout / .NET / tests / showcase
```

ProjectHub authorized commit during this audit:

`6494e7b51aae694f4559f836199cb78212976edc`

FactoryBridge binary semantic version remains `0.13.0`; V2 is the independently versioned control protocol.

## Live post-promotion V2 proof

Mission: `M-V2-SMOKE-20260909-0134`

The V2 envelope included:

- full `targetCommit`;
- `issuedAt`;
- `expiresAt`;
- SHA-256 `payloadHash` over the canonical mission payload.

Observed runtime behavior:

- ACK: `ACCEPTED` and explicitly durably queued;
- terminal CHECKPOINT: `DONE`;
- validated commit: `6494e7b51aae694f4559f836199cb78212976edc`;
- execution duration: `31,014 ms`.

This proves that the promoted notebook runtime understands and executes `FACTORY_BUS_V2`.

## Adversarial live tests

### Expired mission — PASS

Mission: `M-V2-EXPIRED-20260909-0136`

Expected: no ProjectHub execution.

Observed: `BLOCKED` with `mission has expired`.

### Wrong target commit — PASS / fail closed

Mission: `M-V2-WRONGSHA-20260909-0136`

Authorized target was all-zero SHA while `origin/main` was the real ProjectHub commit.

Observed:

- mission was durably queued;
- target validation stopped execution before build/test;
- terminal state `NEEDS_BRAIN`;
- diagnostic contained both authorized and observed SHA;
- duration was only `1,661 ms`.

No valid ProjectHub build/test was allowed for the unauthorized target.

### Unknown mission control — PASS

A `CANCEL` for an unknown mission ID returned `CONTROL_ACK BLOCKED` with `mission is not known locally`.

### Cancellation irreversibility — PASS on promoted V2

For a known journal, `CANCEL` was acknowledged. A subsequent `RESUME` returned `BLOCKED` with `mission cancellation is terminal`.

The source hardening after this live test further rejects any control for a journal that is already terminal (`DONE`, `NEEDS_BRAIN`, `BLOCKED`).

### Identical terminal replay — PASS

Reposting the exact V2 payload for the already-completed smoke mission caused the existing terminal checkpoint to be re-published with the original duration instead of re-running the expensive verification.

### Same ID with different payload after terminal completion — DEFECT FOUND

The promoted V2 runtime incorrectly checked existing terminal evidence before comparing the journal payload hash. Therefore a reused mission ID with a different, otherwise valid payload caused the old `DONE` checkpoint to be re-published instead of returning a conflict.

This is a real replay-integrity defect discovered by the adversarial audit.

## Source fix after defect discovery

The source was changed so `validateMissionIntegrity` compares a new V2 payload with the existing durable journal before terminal evidence can be reused.

Invariant after the fix:

```text
same ID + same canonical payload
  → idempotent existing checkpoint

same ID + different canonical payload
  → BLOCKED / payload conflict
```

Additional hardening applied in the same source batch:

- journal reads used for validation are side-effect-free and do not reserve/create the mission directory;
- validation cannot make Gateway submission falsely believe an ID was already used;
- controls are rejected after journal state becomes terminal;
- regression test covers same-ID/different-payload after terminal completion;
- regression test covers validation without mission-directory creation;
- crash-recovery test proves a `RUNNING` journal becomes `NEEDS_BRAIN` after recovery and is not automatically replayed;
- cancellation cannot be resumed;
- terminal journals reject new controls;
- runtime tests isolate `LOCALAPPDATA` from the host machine.

## Existing V2 safety properties

- only three mission kinds are accepted: `projecthub.verify`, `projecthub.showcase`, `projecthub.full_cycle`;
- unknown JSON fields fail strict decoding;
- no arbitrary shell, executable, local path, arbitrary URL, branch or argument list is accepted from the bus;
- one process-wide scheduler mutex serializes ProjectHub mission execution;
- the GitHub poller remains independent while a mission runs;
- journal state is durable under `%LOCALAPPDATA%\FactoryBridge\missions`;
- non-terminal crash recovery refuses blind replay and requires a new mission ID for explicit retry;
- `origin/main` must still equal the authorized `targetCommit` immediately before execution;
- `projecthub.full_cycle` re-checks the canonical checkout after sync;
- public Tailscale checkpoint output is metadata-only;
- the Gateway bearer token stays local.

## Remaining hard boundaries

These are not solved by the protocol patch and must not be represented as solved:

1. `DISPATCHER/main` and `ProjectHub/main` are still unprotected branches.
2. The runtime still derives its GitHub credential from the normal Git credential helper instead of a dedicated Issues-only credential.
3. The allowlist is not an OS sandbox. Repository-controlled build/test code runs with the Windows identity of FactoryBridge.
4. Active `PAUSE`/`RESUME` during a genuinely long-running external process is only effective at safe stage boundaries; mid-process kill is intentionally not implemented.
5. Physical reboot / process-kill chaos has a source-level regression harness but still requires an explicit local destructive test campaign.

## Promotion gate for the replay fix

The replay fix is not considered live merely because it exists in GitHub source.

A pinned promotion must:

1. fetch the exact audited DISPATCHER commit;
2. verify the checkout SHA;
3. run `go test ./...`;
4. run `go build` into local staging;
5. preserve the previous binary;
6. promote only after tests/build pass;
7. restart the canonical supervisor;
8. validate the local process;
9. re-run a live same-ID/different-payload replay test through Issue #7.

Only after that live replay test passes should the replay defect be marked closed.
