# FactoryBridge — system.command risk model

Status: **deployed and live-proven for FACTORY_BUS_V2 on installed runtime v0.14.0, source commit `120359f4a620c6d163c06885a8802ea41eea186f`. Standard and guarded execution paths plus approval-without-approval and forbidden fail-closed paths have canonical live evidence in Issue #7.**

## Goal

Allow ChatGPT to use the Windows notebook as a practical execution motor without requiring a new hard-coded action for every PowerShell or CMD operation.

The command still travels through canonical GitHub Issue #7 and remains bound to trusted author, `FACTORY_BUS_V2`, unique mission ID, exact installed FactoryBridge commit, RFC3339 issue/expiry window, SHA-256 canonical payload hash and durable local mission evidence.

## Mission kind

`system.command`

Structured command parameters are encoded as JSON inside `objective`. Because `objective` is part of the canonical mission payload, every command byte is covered by `payloadHash`.

Accepted fields:

- `shell`: `powershell` or `cmd`;
- `command`: command text, maximum 16 KiB;
- `workingDir`: optional; defaults to configured Dispatcher work directory and then user home;
- `timeoutSec`: optional, maximum 1800 seconds;
- `riskApproval`: omitted normally; literal `approved` only for a deliberately approved approval-class operation.

Unknown fields are rejected.

## Risk levels

### standard — automatic

Read-only and ordinary commands that do not match a state-changing or destructive rule. Examples include directory listing, environment inspection, logs, ports, process queries, Git status/diff/log, tests and diagnostics.

Live proof: `M-SYSCMD-STANDARD-20260909-1652` returned `ACK: ACCEPTED` and `CHECKPOINT: DONE`; it reported PowerShell `5.1.22621.4249`, Git `2.55.0.windows.5` and the live state of `FactoryBridge Admin Poller`.

### guarded — automatic + audited

State-changing commands useful for normal development and operations. They run automatically and classification is recorded in evidence. Examples include file creation/copy/move/rename/overwrite, process or service start/stop, scheduled-task changes, ordinary Git mutations and common package install/update operations.

Live proof: `M-VERSION-POLLER-20260909-1704` executed `Start-ScheduledTask -TaskName 'FactoryBridge Admin Poller'` and returned `CHECKPOINT: DONE` with `risk=guarded`.

### approval — explicit approval required

Examples include file or directory deletion, destructive/force Git operations, registry/service/scheduled-task deletion, shutdown/restart, ACL changes, firewall/security configuration changes and global software uninstall.

Without `"riskApproval":"approved"`, the runner is not invoked and the mission returns `BLOCKED`.

Live fail-closed proof: `M-RISK-APPROVAL-BLOCK-20260909-1723` targeted a deliberately nonexistent temporary path with `Remove-Item` and omitted approval. Runtime v0.14.0 returned `CHECKPOINT: BLOCKED` in 53 ms with `command requires explicit riskApproval=approved: file or directory deletion requires explicit approval`.

This proves the **no-approval block branch** end to end. It does not by itself prove execution of an approval-class command after explicit approval; that branch should be qualified only with a deliberately safe, reversible scenario if such proof becomes necessary.

### forbidden — never automatic

`riskApproval` cannot override forbidden patterns such as disk formatting/clearing, filesystem-root deletion, disabling core endpoint-security protections, credential/LSASS extraction or obfuscated/dynamically evaluated command payloads such as encoded PowerShell, Base64 execution or `Invoke-Expression`.

Live fail-closed proof: `M-RISK-FORBIDDEN-BLOCK-20260909-1723` contained an encoded-command pattern. Runtime v0.14.0 returned `CHECKPOINT: BLOCKED` in 25 ms with `command blocked: obfuscated or dynamically evaluated command text is not accepted`.

## Execution boundary

The runtime does not execute an arbitrary executable path supplied by a mission. It selects the executable:

- `powershell` -> `powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command <command>`
- `cmd` -> `cmd.exe /d /s /c <command>`

The command string is flexible while the shell executable remains fixed.

## Evidence

Each command produces standard FactoryBridge result data plus risk level/rule/reason, shell, resolved working directory, approval flags when applicable, stdout, stderr, exit code and duration. GitHub receives a compact checkpoint; complete evidence remains local.

## Operational consequence

Common diagnostic and operational requests no longer require a new Go mission kind. ChatGPT can submit a hashed `system.command` mission and receive ACK/CHECKPOINT through Issue #7 subject to the classifier and fixed-shell boundary.

The self-update path remains separate and elevated. Admin polling/self-update authorization continues through `FACTORY_ADMIN_V1` and has independent `UPDATE_RESULT` evidence.

## Remaining boundary

The risk classifier is a policy layer, not an OS sandbox. Builds, tests and approved commands execute with the Windows identity running FactoryBridge. Isolation through a restricted account, WSL/container or VM remains the major security hardening step.
