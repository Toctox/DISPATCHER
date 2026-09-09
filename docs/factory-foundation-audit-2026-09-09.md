# FactoryBridge — Foundation Audit — 2026-09-09

Status: **FACTORY_BUS_V2 promoted and live-proven; terminal replay-integrity defect found by the audit is CLOSED.**

## Canonical path

```text
ChatGPT
  ↕
Toctox/DISPATCHER Issue #7 — FACTORY_BUS_V2
  ↕
FactoryBridge 0.13.0 on Windows notebook `supremo`
  ↕
ProjectHub local checkout / .NET / tests / showcase
```

ProjectHub authorized commit used by the live probes:

`6494e7b51aae694f4559f836199cb78212976edc`

The FactoryBridge binary semantic version remains `0.13.0`; `FACTORY_BUS_V2` is the independently versioned control protocol.

## Initial V2 proof

Mission `M-V2-SMOKE-20260909-0134` proved the promoted runtime understood the hardened V2 envelope (`targetCommit`, `issuedAt`, `expiresAt`, `payloadHash`), returned `ACK ACCEPTED`, then `CHECKPOINT DONE`, and validated the exact ProjectHub commit in `31,014 ms`.

## Adversarial findings

The live audit then exercised failure semantics:

- expired mission → `BLOCKED` — PASS;
- unauthorized target SHA → stopped before build/test with `NEEDS_BRAIN` — PASS;
- control for unknown mission → `BLOCKED` — PASS;
- `CANCEL` followed by `RESUME` → cancellation remained terminal — PASS;
- identical terminal replay → existing checkpoint republished without expensive re-execution — PASS;
- same terminal mission ID with a different valid canonical payload → old checkpoint was incorrectly republished — DEFECT FOUND.

## Replay-integrity fix

The source was hardened so validation checks the durable journal payload hash before terminal evidence may be reused.

Required invariant:

```text
same ID + same canonical payload
  → idempotent existing checkpoint

same ID + different canonical payload
  → BLOCKED / payload conflict
```

The same source batch also added or hardened:

- side-effect-free journal reads during validation;
- terminal control rejection;
- crash recovery that fails closed into `NEEDS_BRAIN` instead of blind replay;
- cancellation irreversibility;
- replay-conflict regression coverage after terminal completion;
- host `LOCALAPPDATA` isolation for runtime tests.

The pinned candidate used for the corrected promotion was:

`5627c2e64333178f5ae7308c09ac0551f609af63`

## Live closure proof

After the corrected promotion, the exact previously failing scenario was repeated through Issue #7.

A new envelope reused `M-V2-SMOKE-20260909-0134` with a different valid canonical payload. The notebook returned:

```text
state: BLOCKED
summary: mission id was already reserved with a different payload
observedAt: 2026-09-09T05:05:34Z
```

This closes the replay-integrity defect behaviorally.

A fresh V2 mission was then submitted to ensure the fix did not break normal execution:

`M-V2-POSTFIX-20260909-0206`

Observed:

```text
ACK: ACCEPTED
CHECKPOINT: DONE
commit: 6494e7b51aae694f4559f836199cb78212976edc
durationMs: 25835
observedAt: 2026-09-09T05:08:03Z
```

Therefore both sides of the invariant are now proven live: conflicting replay is rejected, while a new authorized mission still executes successfully.

## V2 safety properties currently proven or source-backed

- only `projecthub.verify`, `projecthub.showcase`, and `projecthub.full_cycle` are accepted mission kinds;
- unknown JSON fields fail strict decoding;
- no arbitrary shell, executable, arbitrary path, URL, branch, or argument list is accepted from the bus;
- one process-wide scheduler mutex serializes ProjectHub mission execution paths;
- the GitHub poller remains independent while a mission runs;
- journals and evidence live under `%LOCALAPPDATA%\FactoryBridge`;
- interrupted non-terminal journals fail closed rather than automatically replaying;
- `origin/main` must still equal the authorized `targetCommit` immediately before execution;
- `projecthub.full_cycle` re-checks the canonical checkout after sync;
- public Tailscale checkpoint output is metadata-only;
- Gateway bearer credentials remain local.

## Remaining hard boundaries

These are **not** solved by this audit and must not be represented as solved:

1. `DISPATCHER/main` and `ProjectHub/main` are still unprotected branches.
2. The runtime still derives GitHub access from the normal Git credential helper rather than a dedicated Issues-only credential.
3. The allowlist is not an OS sandbox; repository-controlled build/test code runs with the Windows identity of FactoryBridge.
4. `PAUSE`/`RESUME`/`CANCEL` act at safe mission stage boundaries; an already-running external build/test process is not forcibly killed mid-instruction.
5. Physical process-kill, network-loss, and reboot chaos still require an explicit destructive local campaign.

## Foundation conclusion

The V2 control foundation is now strong enough to proceed to the next test tier: concurrency under load, long-session control, crash/restart recovery, network interruption, and eventually OS-level isolation. The replay defect discovered during this audit is no longer open.