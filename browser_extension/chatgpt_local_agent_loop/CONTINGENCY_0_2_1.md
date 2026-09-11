# Local Agent Loop 0.2.1 recovery behavior

This patch closes two failure modes discovered during live rollout:

1. A reloaded ChatGPT tab could start with an empty in-memory `seen` set and treat historical `LOCAL_AGENT_V1` blocks already present in the conversation as new commands. Existing blocks are now snapshotted before scanning begins.
2. A new content script could run against an old service worker. The content script now probes `GET_STATUS`, which exists in both versions, and requires the 0.2 worker's `activeRequest` field before arming. If incompatible, it displays `UPDATE_REQUIRED` and polls for recovery without executing commands.

Expected visible states include `READY`, `UPDATE_REQUIRED`, `CHANNEL_OFFLINE`, `RECOVERING_CHANNEL`, `EXECUTING`, `SENDING`, `SENT`, and `STALLED`.
