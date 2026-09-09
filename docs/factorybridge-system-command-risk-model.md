# FactoryBridge — system.command risk model

Status: implementation for FACTORY_BUS_V2.

## Goal

Allow ChatGPT to use the Windows notebook as a practical execution motor without requiring a new hard-coded action for every PowerShell or CMD operation.

The command still travels through the canonical GitHub Issue #7 bus and therefore remains bound to:

- trusted author `Toctox`;
- `FACTORY_BUS_V2` marker and protocol;
- unique mission ID;
- exact installed FactoryBridge commit (`targetCommit`);
- RFC3339 issue/expiry window;
- SHA-256 canonical payload hash;
- durable local mission journal, checkpoint and evidence.

## Mission kind

`system.command`

For V2 compatibility, the structured command parameters are encoded as JSON inside `objective`. Because `objective` is already part of the canonical mission payload, every command byte is covered by `payloadHash`.

Example objective value:

```json
{"shell":"powershell","command":"Start-ScheduledTask -TaskName 'FactoryBridge Admin Poller'","workingDir":"C:\\Users\\user","timeoutSec":120}
```

Accepted fields:

- `shell`: `powershell` or `cmd`;
- `command`: command text, maximum 16 KiB;
- `workingDir`: optional; defaults to the configured Dispatcher work directory and then the user home directory;
- `timeoutSec`: optional, maximum 1800 seconds;
- `riskApproval`: omitted normally; set to literal `approved` only for a deliberately approved destructive/sensitive command.

Unknown fields are rejected.

## Risk levels

### standard — automatic

Read-only and ordinary commands that do not match a state-changing or destructive rule. Examples: directory listing, environment inspection, logs, ports, process queries, Git status/diff/log, test commands and diagnostics.

### guarded — automatic + audited

State-changing commands that are useful for normal development and operations. They run automatically but the classification is recorded in local evidence. Examples include:

- file creation, copy, move, rename and overwrite;
- process/service start and stop;
- scheduled task start/stop/register/update/enable/disable;
- ordinary Git add/commit/merge/rebase/checkout/switch/pull/push/fetch;
- common package install/update operations.

### approval — explicit approval required

These are not executed unless the objective contains `"riskApproval":"approved"`:

- file or directory deletion (`Remove-Item`, `del`, `rd`, `rmdir`, etc.);
- destructive/force Git operations such as `reset --hard`, `clean`, force-push and branch `-D`;
- registry deletion;
- service deletion;
- scheduled-task deletion;
- shutdown/restart;
- ACL/permission changes;
- firewall or endpoint-security configuration changes;
- global software uninstall.

A mission without approval returns `BLOCKED`; the runner is never invoked.

### forbidden — never automatic

`riskApproval` cannot override these rules:

- disk formatting, disk clearing or partition removal;
- filesystem-root deletion;
- disabling core endpoint-security protections or adding Defender exclusions;
- credential/LSASS extraction patterns;
- obfuscated/dynamically evaluated command payloads such as encoded PowerShell, Base64 execution or `Invoke-Expression`.

## Execution boundary

The runtime does not execute an arbitrary executable path from the mission. It chooses the executable itself:

- `powershell` -> `powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command <command>`
- `cmd` -> `cmd.exe /d /s /c <command>`

The command string is therefore flexible while the shell executable remains fixed.

## Evidence

Each command produces the standard FactoryBridge `Result` plus:

- `riskLevel`;
- `riskRule`;
- `riskReason`;
- `shell`;
- resolved `workingDir`;
- `riskApproved` or `requiresApproval` when applicable;
- stdout, stderr, exit code and duration.

The compact GitHub checkpoint does not publish the full command or full logs. Complete evidence remains local.

## Operational consequence

Once this FactoryBridge version is installed, common operational requests no longer require adding new Go mission kinds. ChatGPT can submit a hashed `system.command` mission and receive an ACK/CHECKPOINT through the same Issue #7 bus.

The existing self-update path remains separate and elevated. The Admin Poller is changed to one-minute cadence, and `RUN_FACTORY_BRIDGE_ADMIN_POLLER_NOW.cmd` can start it immediately during bootstrap.
