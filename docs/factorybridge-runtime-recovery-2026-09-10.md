# FactoryBridge runtime recovery — 2026-09-10

Operational recovery marker for the canonical FactoryBridge runtime.

Reason: after `M-FB-P0-IDENTITY-APPLY-20260910-2102` terminated `NEEDS_BRAIN`, a subsequent valid FACTORY_BUS_V2 mission (`M-FB-P0-IDENTITY-SCRIPTFIX-20260910-1929`) remained without ACK for multiple normal polling intervals. The repository main and installed runtime were both at `5bacfdd4320f1176fed5517f211b346b5f654818`.

This commit intentionally changes no executable behavior. Its purpose is to provide a new exact commit for the existing `FACTORY_ADMIN_V1` supervised self-update path so the updater can rebuild, promote, restart, and health-check FactoryBridge using the canonical fail-safe mechanism.

After recovery, the stalled mission must be reissued with a new mission ID rather than assumed to have executed.