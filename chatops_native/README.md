# ChatOps Native Codex Bridge

Zero-cost local bridge from an explicit ChatGPT message in Chrome to the already-installed Codex CLI on the same Windows computer.

## Architecture

```text
ChatGPT page in Chrome
        |
        | explicit LOCAL_CODEX_REQUEST_V1 block
        v
Manifest V3 extension
        |
        | chrome.runtime.connectNative()
        v
ChatOpsCodexHost.exe
        |
        | direct process invocation, no shell
        v
codex.exe exec
        |
        v
LOCAL_CODEX_RESULT_V1 is placed in the ChatGPT composer
```

No OpenAI API key, VPS, inbound port, Tailscale, Cloudflare Tunnel, arbitrary PowerShell, or shell command endpoint is required.

## Security boundary

The native host accepts only:

- `ping`
- `codex.exec`
- `codex.status`
- `codex.cancel`

`codex.exec`:

- discovers `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` dynamically;
- validates the candidate with `codex.exe --version`;
- uses `codex exec --ephemeral`;
- defaults to `--sandbox read-only`;
- passes the prompt over stdin;
- never uses `--yolo`, `--full-auto`, `--dangerously-bypass-approvals-and-sandbox`, or an arbitrary shell;
- permits `workspace-write` only when the Chrome confirmation path sets `confirmWrite=true`;
- caps request size, timeout, and returned output.

The extension includes a fixed public development key, giving it the stable unpacked-extension ID:

```text
mhjghnfpggpfmcoheapfibohfeiipmhn
```

The Native Messaging manifest is registered under HKCU only and its `allowed_origins` contains exactly that extension ID. Chrome Native Messaging does not permit wildcards in this field.

The extension never auto-sends the result back to ChatGPT. It places `LOCAL_CODEX_RESULT_V1` in an empty composer; the user reviews and sends it. If the composer is occupied, it attempts to copy the result instead.

## Install on Windows

From this directory:

```powershell
.\install.ps1 -RunSmoke
```

The installer:

1. builds `ChatOpsCodexHost.exe` using the installed Go toolchain;
2. copies the unpacked extension to `%LOCALAPPDATA%\ChatOpsCodex\extension`;
3. validates discovery of the installed Codex CLI;
4. optionally runs the harmless `HOST_CHATGPT_TO_CODEX_OK` smoke;
5. registers the Native Messaging host under HKCU for the fixed extension ID.

Then perform the only manual Chrome step:

1. open `chrome://extensions`;
2. enable **Developer mode**;
3. choose **Load unpacked**;
4. select `%LOCALAPPDATA%\ChatOpsCodex\extension`;
5. verify Chrome shows extension ID `mhjghnfpggpfmcoheapfibohfeiipmhn`;
6. reload the ChatGPT tab.

No administrator privilege is required.

## Harmless first request

Ask ChatGPT to emit exactly this block:

```text
LOCAL_CODEX_REQUEST_V1
{
  "version": "CHATOPS_NATIVE_V1",
  "requestId": "SMOKE-001",
  "operation": "codex.exec",
  "prompt": "Não modifique arquivos. Não execute comandos. Não use ferramentas. Responda somente: HOST_CHATGPT_TO_CODEX_OK",
  "sandbox": "read-only",
  "timeoutSec": 120
}
```

The extension adds **Executar no Supremo** beneath an assistant-authored request block. It will not add the button to a user-authored block.

After confirmation, the host starts Codex. When the result arrives, the extension places a structured block in the composer:

```text
LOCAL_CODEX_RESULT_V1
{
  "version": "CHATOPS_NATIVE_V1",
  "requestId": "SMOKE-001",
  "jobId": "...",
  "status": "succeeded",
  "exitCode": 0,
  "finalMessage": "HOST_CHATGPT_TO_CODEX_OK"
}
```

Review and send it so the assistant can continue from the local result.

## Protocol

Example read-only request:

```json
{
  "version": "CHATOPS_NATIVE_V1",
  "requestId": "REQ-001",
  "operation": "codex.exec",
  "workspace": "C:\\work\\repo",
  "sandbox": "read-only",
  "timeoutSec": 180,
  "prompt": "Inspect the repository and report the current state. Do not modify files."
}
```

For a write-capable request, the assistant may set `"sandbox":"workspace-write"`, but the extension asks for a second confirmation and adds `confirmWrite=true` only after that confirmation.

## Local paths

Installed files:

```text
%LOCALAPPDATA%\ChatOpsCodex\
  ChatOpsCodexHost.exe
  com.toctox.chatops_codex.json
  install-state.json
  extension\
  runs\
```

Registry:

```text
HKCU\Software\Google\Chrome\NativeMessagingHosts\com.toctox.chatops_codex
```

## Uninstall

Disable/remove the Chrome extension, then:

```powershell
.\uninstall.ps1 -RemoveFiles
```

This removes only the per-user Native Messaging registration and the `%LOCALAPPDATA%\ChatOpsCodex` files.
