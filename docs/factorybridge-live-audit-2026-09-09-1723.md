# FactoryBridge — Live Audit — 2026-09-09 17:23 BRT

Status: **runtime v0.14.0 promoted and live-proven; core Runtime Bus healthy; system.command standard/guarded/approval-block/forbidden-block paths have live evidence. External Gateway boundary and OS isolation remain open.**

## Canonical runtime

- Repository: `Toctox/DISPATCHER`
- Canonical bus: Issue #7
- Installed FactoryBridge runtime source commit: `120359f4a620c6d163c06885a8802ea41eea186f`
- FactoryBridge version: `0.14.0`
- Repository `main` may advance with documentation-only commits without changing the installed runtime binary; runtime identity is therefore tracked separately from repository-head identity.
- ProjectHub authorized commit used by the latest full-cycle proofs: `6494e7b51aae694f4559f836199cb78212976edc`

## Fresh evidence

`U-VERSION-ALIGN-20260909-1703` returned `UPDATE_RESULT: DONE` for commit `120359f4a620c6d163c06885a8802ea41eea186f`; tests, build, promotion and post-update health validation passed.

`M-VERSION-CONFIRM-20260909-1708` returned `ACK: ACCEPTED` and `CHECKPOINT: DONE` with `bridgeVersion: 0.14.0`, proving the promoted binary is the runtime actually processing V2 missions.

Risk-classifier live probes on v0.14.0 also completed as designed:

- `M-RISK-APPROVAL-BLOCK-20260909-1723` -> `ACK: ACCEPTED` -> `CHECKPOINT: BLOCKED` in 53 ms, with summary `command requires explicit riskApproval=approved: file or directory deletion requires explicit approval`.
- `M-RISK-FORBIDDEN-BLOCK-20260909-1723` -> `ACK: ACCEPTED` -> `CHECKPOINT: BLOCKED` in 25 ms, with summary `command blocked: obfuscated or dynamically evaluated command text is not accepted`.

The approval probe targeted a deliberately nonexistent temp path and omitted approval; the forbidden probe used an encoded-command pattern. Both were rejected before useful command execution, providing end-to-end fail-closed evidence without destructive effects.

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
10. Runtime version metadata is aligned with the deployed v0.14 feature set.
11. `system.command` standard execution is live-proven.
12. `system.command` guarded execution is live-proven.
13. Approval-class commands without explicit approval are live-proven to fail closed.
14. Forbidden encoded/obfuscated command patterns are live-proven to fail closed.

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
10. **Explicit approval execution itself is not yet live-qualified.** The no-approval block is proven; a deliberately safe, reversible approval-class live test should be designed before claiming the approved-destructive execution branch is end-to-end proven.

## Next hardening order

1. Re-run the Gateway public-minimization and private-401 checks against v0.14.0.
2. Design one reversible approval-class execution test if live proof of the approved branch is operationally necessary.
3. Provision a dedicated fine-grained GitHub credential for the Runtime Bus.
4. Establish a golden known-good recovery commit and recovery bootstrap independent of current `main`.
5. Move execution into a restricted Windows account, WSL/container or VM boundary.
6. Add isolated Git worktrees/runners for safe parallelism.
7. Continue chaos qualification: executor kill after ACK, mid-build kill, supervisor recovery, reboot mid-mission, extended network outage and unavailable credential.

## Operational conclusion

FactoryBridge is no longer a proof of concept. The core remote-execution control loop is functioning and durable enough for controlled development and diagnostic work. The risk classifier now has live evidence for automatic standard/guarded execution and fail-closed approval/forbidden decisions. The principal remaining risks are OS isolation, credential/repository governance, external Gateway revalidation and completion of the broader chaos matrix—not basic transport or execution viability.
