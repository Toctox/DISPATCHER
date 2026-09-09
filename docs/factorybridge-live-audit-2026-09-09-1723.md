# FactoryBridge — Live Audit — 2026-09-09 17:23 BRT

Status: **runtime v0.14.0 promoted and live-proven; core Runtime Bus healthy; external Gateway boundary and destructive-risk qualification remain open.**

## Canonical runtime

- Repository: `Toctox/DISPATCHER`
- Canonical bus: Issue #7
- Installed FactoryBridge runtime source commit: `120359f4a620c6d163c06885a8802ea41eea186f`
- FactoryBridge version: `0.14.0`
- Repository `main` may advance with documentation-only commits without changing the installed runtime binary; runtime identity is therefore tracked separately from repository-head identity.
- ProjectHub authorized commit used by the latest full-cycle proofs: `6494e7b51aae694f4559f836199cb78212976edc`

## Fresh evidence

`U-VERSION-ALIGN-20260909-1703` returned `UPDATE_RESULT: DONE` for commit `120359f4a620c6d163c06885a8802ea41eea186f`; tests, build, promotion and post-update health validation passed.

`M-VERSION-CONFIRM-20260909-1708` then returned `ACK: ACCEPTED` and `CHECKPOINT: DONE` with `bridgeVersion: 0.14.0`, proving the promoted binary is the runtime actually processing V2 missions.

Earlier live evidence remains valid for the current architecture:

- scheduler yield: paused target allowed an independent probe to complete, then resumed and completed;
- network recovery: canonical full cycle succeeded after earlier transient `github.com` DNS failures;
- replay/integrity: same ID with a different payload was rejected;
- `system.command` standard path: live PowerShell diagnostics completed successfully;
- `system.command` guarded path: `Start-ScheduledTask` completed successfully and was reported as `risk=guarded`;
- self-update: GitHub authorization -> local admin poller -> tests/build -> promotion -> post-update health -> `UPDATE_RESULT` is live-proven.

## Audit findings

### Healthy / proven

1. GitHub Issue #7 is a working durable brain-to-motor mailbox.
2. Mission envelopes are bound to ID, kind, objective, exact commit, issue/expiry times and SHA-256 payload hash.
3. Journal-before-ACK and fail-closed restart behavior reduce replay ambiguity.
4. The global scheduler prevents unsafe concurrent mutation of the canonical ProjectHub checkout.
5. PAUSE/RESUME does not starve the global scheduler.
6. The GitHub poller remains independent from mission execution.
7. Multi-page comment retrieval removes the original 100-comment blind spot.
8. Transient network failure is observable and recoverable without corrupting repository state.
9. Runtime self-update is operational and verifiable.
10. Runtime version metadata is now aligned with the deployed v0.14 feature set.

### Known limitations / residual risk

1. **No OS sandbox.** Builds, tests and approved commands run with the Windows identity of FactoryBridge. Repository-controlled build/test code can access whatever that identity can access.
2. **Credential scope is external to the runtime.** The local Git credential helper may provide a token broader than the ideal Issue-only Runtime Bus permission set.
3. **Branch governance is not enforced by FactoryBridge.** GitHub account protection, branch/ruleset policy and repository permissions remain supply-chain boundaries.
4. **Global serialization limits throughput.** Integrity is favored over parallelism until worktrees/isolated runners are introduced.
5. **Controls are safe-boundary controls, not hard process preemption.** PAUSE/CANCEL do not forcibly terminate an already-running external build/test subprocess.
6. **GitHub/DNS remains a control-plane dependency.** During an outage, new remote missions and checkpoints can be delayed even though local state remains durable.
7. **ChatGPT connector policy is an upstream boundary.** Some operational payloads may be blocked before reaching GitHub even when FactoryBridge itself would accept them.
8. **Admin self-update has a bootstrap dependency.** A broken admin poller can stall update ingestion; retain a known-good recovery path outside the normal runtime.
9. **Public Gateway promotion evidence is stale for the current runtime.** Re-probe public metadata minimization and unauthenticated private endpoint `401` before declaring the external boundary freshly closed.
10. **Risk classifier qualification is incomplete.** Standard and guarded paths have live evidence. Approval-without-approval and forbidden-pattern fail-closed behavior require dedicated canonical live probes before being called end-to-end proven.

## Qualification actions opened by this audit

Two non-destructive fail-closed probes were issued against runtime commit `120359f4a620c6d163c06885a8802ea41eea186f`:

- `M-RISK-APPROVAL-BLOCK-20260909-1723`: a deletion-shaped command targeting a deliberately nonexistent temp path and omitting `riskApproval`; expected terminal state is `BLOCKED` before the runner is invoked.
- `M-RISK-FORBIDDEN-BLOCK-20260909-1723`: an encoded-command pattern; expected terminal state is `BLOCKED` regardless of approval.

These probes are safe because the expected classifier behavior prevents invocation; the first also targets a deliberately nonexistent audit path.

## Next hardening order

1. Confirm both risk probes fail closed as designed.
2. Re-run the Gateway public-minimization and private-401 checks against v0.14.0.
3. Provision a dedicated fine-grained GitHub credential for the Runtime Bus.
4. Establish a golden known-good recovery commit and recovery bootstrap independent of current `main`.
5. Move execution into a restricted Windows account, WSL/container or VM boundary.
6. Add isolated Git worktrees/runners for safe parallelism.
7. Continue chaos qualification: executor kill after ACK, mid-build kill, supervisor recovery, reboot mid-mission, extended network outage and unavailable credential.

## Operational conclusion

FactoryBridge is no longer a proof of concept. The core remote-execution control loop is functioning and durable enough for controlled development/diagnostic work. The principal remaining risks are isolation, credential/repository governance and completion of the external Gateway/risk-classifier qualification matrix—not basic transport or execution viability.
