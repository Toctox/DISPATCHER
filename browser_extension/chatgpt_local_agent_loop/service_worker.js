const DEFAULT_PORT = 18765;
const PROTOCOL_VERSION = "0.2.2";
const ALLOWED = new Set([
  "/health", "/v1/status", "/v1/processes",
  "/v1/exec", "/v1/process/start", "/v1/process/stop",
  "/v1/process/output", "/v1/file/read", "/v1/file/write", "/v1/file/list"
]);
const JOURNAL_LIMIT = 50;
const DEFAULT_PROCESS_TIMEOUT_MS = 5 * 60 * 1000;
const MIN_PROCESS_TIMEOUT_MS = 30 * 1000;
const MAX_PROCESS_TIMEOUT_MS = 30 * 60 * 1000;
const NO_PROGRESS_WARNING_MS = 60 * 1000;
const LONG_RUNNING_MS = 90 * 1000;

async function lockStorage() {
  try { await chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"}); } catch (_) {}
}
lockStorage();
chrome.runtime.onInstalled.addListener(lockStorage);
chrome.runtime.onStartup.addListener(lockStorage);

const validPort = v => {
  const n = Number(v);
  return Number.isInteger(n) && n >= 1024 && n <= 65535 ? n : DEFAULT_PORT;
};
const validId = v => typeof v === "string" && /^[A-Za-z0-9._:-]{1,128}$/.test(v);
const isoNow = () => new Date().toISOString();
const clamp = (n, min, max) => Math.max(min, Math.min(max, n));

function processTimeoutMs(req) {
  const seconds = Number(req?.timeoutSeconds);
  if (!Number.isFinite(seconds) || seconds <= 0) return DEFAULT_PROCESS_TIMEOUT_MS;
  return clamp(Math.round(seconds * 1000), MIN_PROCESS_TIMEOUT_MS, MAX_PROCESS_TIMEOUT_MS);
}

function trimValue(v, depth = 0) {
  if (depth > 6) return "[depth-truncated]";
  if (typeof v === "string") {
    const max = 12000;
    return v.length > max ? v.slice(0, max) + `\n...[truncated ${v.length - max} chars]` : v;
  }
  if (Array.isArray(v)) return v.slice(0, 100).map(x => trimValue(x, depth + 1));
  if (v && typeof v === "object") {
    const out = {};
    for (const [k, x] of Object.entries(v).slice(0, 120)) out[k] = trimValue(x, depth + 1);
    return out;
  }
  return v;
}

async function getState() {
  const d = await chrome.storage.local.get([
    "token", "port", "enabledTabId", "automationEnabled", "executedResults", "requestJournal"
  ]);
  return {
    token: typeof d.token === "string" ? d.token : "",
    port: validPort(d.port),
    enabledTabId: Number.isInteger(d.enabledTabId) ? d.enabledTabId : null,
    automationEnabled: d.automationEnabled === true,
    executedResults: d.executedResults && typeof d.executedResults === "object" ? d.executedResults : {},
    requestJournal: d.requestJournal && typeof d.requestJournal === "object" ? d.requestJournal : {}
  };
}

async function saveResult(id, result) {
  const s = await getState();
  const items = Object.entries(s.executedResults).filter(([k]) => k !== id).slice(-(JOURNAL_LIMIT - 1));
  items.push([id, {at: Date.now(), result}]);
  await chrome.storage.local.set({executedResults: Object.fromEntries(items)});
}

async function saveJournal(id, patch) {
  const s = await getState();
  const previous = s.requestJournal[id] || {};
  const next = {...previous, ...patch, requestId: id, updatedAt: Date.now()};
  const items = Object.entries(s.requestJournal).filter(([k]) => k !== id).slice(-(JOURNAL_LIMIT - 1));
  items.push([id, next]);
  await chrome.storage.local.set({requestJournal: Object.fromEntries(items)});
  return next;
}

async function localCall(path, method = "GET", body = null, timeoutMs = 20000) {
  if (!ALLOWED.has(path)) throw new Error("PATH_NOT_ALLOWED");
  const s = await getState();
  if (path !== "/health" && !s.token) throw new Error("TOKEN_NOT_CONFIGURED");
  const opt = {method, headers: {}};
  if (path !== "/health") opt.headers.Authorization = `Bearer ${s.token}`;
  if (body !== null) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  opt.signal = controller.signal;
  try {
    const r = await fetch(`http://127.0.0.1:${s.port}${path}`, opt);
    const txt = await r.text();
    let payload;
    try { payload = txt ? JSON.parse(txt) : {}; }
    catch (_) { payload = {raw: txt}; }
    return {ok: r.ok, httpStatus: r.status, payload: trimValue(payload)};
  } finally {
    clearTimeout(timer);
  }
}

function assertArmed(sender, state) {
  if (!state.automationEnabled || !sender.tab || sender.tab.id !== state.enabledTabId)
    throw new Error("TAB_NOT_ARMED");
}

async function finishRequest(entry, local) {
  const result = {
    id: entry.requestId,
    path: entry.path,
    method: entry.method,
    completedAt: isoNow(),
    local
  };
  await saveResult(entry.requestId, result);
  return saveJournal(entry.requestId, {
    state: "RESPONSE_READY", result, error: null, statusDetail: null, completedAt: Date.now()
  });
}

async function stopProcessRequest(entry, reason) {
  let stopResult = null;
  let stopError = null;
  if (entry.agentProcessId) {
    try {
      stopResult = await localCall("/v1/process/stop", "POST", {id: entry.agentProcessId}, 12000);
    } catch (e) {
      stopError = String(e?.message || e);
    }
  }
  return saveJournal(entry.requestId, {
    state: "STALLED",
    error: reason,
    statusDetail: reason,
    stoppedAt: Date.now(),
    stopResult: trimValue(stopResult),
    stopError
  });
}

async function refreshRequest(id) {
  const s = await getState();
  let entry = s.requestJournal[id];
  if (!entry) {
    const done = s.executedResults[id];
    if (done) return {requestId: id, state: "RESPONSE_READY", result: done.result, recovered: true, updatedAt: done.at};
    throw new Error("UNKNOWN_REQUEST_ID");
  }
  if (entry.state !== "EXECUTING") return entry;

  if (entry.mode === "process" && !entry.agentProcessId) {
    if (Date.now() - (entry.executionStartedAt || entry.startedAt || 0) > 30000)
      return saveJournal(id, {state: "STALLED", error: "PROCESS_START_RESPONSE_LOST", statusDetail: "PROCESS_START_RESPONSE_LOST"});
    return entry;
  }

  if (entry.agentProcessId) {
    try {
      const out = await localCall("/v1/process/output", "POST", {id: entry.agentProcessId, maxBytes: 65536}, 12000);
      const p = out.payload || {};
      const now = Date.now();
      const startedAt = entry.executionStartedAt || entry.startedAt || now;
      const elapsedMs = Math.max(0, now - startedAt);
      const timeoutMs = Number(entry.executionTimeoutMs) || DEFAULT_PROCESS_TIMEOUT_MS;

      if (out.ok && p.running === true) {
        const outputLength = String(p.stdout || "").length + String(p.stderr || "").length;
        const previousLength = Number(entry.lastOutputLength || 0);
        const progressed = outputLength > previousLength;
        const lastProgressAt = progressed ? now : (entry.lastProgressAt || startedAt);

        if (elapsedMs >= timeoutMs) {
          return stopProcessRequest(entry, `PROCESS_TIMEOUT_${Math.round(timeoutMs / 1000)}S`);
        }

        let statusDetail = null;
        if (now - lastProgressAt >= NO_PROGRESS_WARNING_MS) {
          statusDetail = `NO_PROGRESS_${Math.floor((now - lastProgressAt) / 1000)}S`;
        } else if (elapsedMs >= LONG_RUNNING_MS) {
          statusDetail = "LONG_RUNNING_WITH_PROGRESS";
        }

        return saveJournal(id, {
          state: "EXECUTING",
          lastAgentPollAt: now,
          pid: p.pid,
          transportError: null,
          elapsedMs,
          lastOutputLength: outputLength,
          lastProgressAt,
          statusDetail
        });
      }
      if (out.ok && p.running === false) {
        const payload = {
          ok: p.exitCode === 0,
          exitCode: p.exitCode,
          stdout: p.stdout || "",
          stderr: p.stderr || "",
          cwd: entry.cwd || null
        };
        return finishRequest(entry, {ok: p.exitCode === 0, httpStatus: 200, payload});
      }
      return saveJournal(id, {state: "STALLED", error: `PROCESS_OUTPUT_FAILED:${out.httpStatus}`, statusDetail: `PROCESS_OUTPUT_FAILED:${out.httpStatus}`});
    } catch (e) {
      const msg = String(e?.message || e);
      if (msg.includes("unknown process id"))
        return saveJournal(id, {state: "STALLED", error: "LOCAL_AGENT_PROCESS_LOST", statusDetail: "LOCAL_AGENT_PROCESS_LOST"});
      return saveJournal(id, {state: "EXECUTING", transportError: msg, statusDetail: `TRANSPORT_RETRY:${msg}`, lastAgentPollAt: Date.now()});
    }
  }
  return entry;
}

async function startRequest(message, sender) {
  const s = await getState();
  assertArmed(sender, s);
  const req = message.request || {};
  if (!validId(req.id)) throw new Error("INVALID_REQUEST_ID");
  if (!ALLOWED.has(req.path)) throw new Error("PATH_NOT_ALLOWED");

  if (s.executedResults[req.id])
    return {requestId: req.id, state: "RESPONSE_READY", duplicate: true, result: s.executedResults[req.id].result};
  if (s.requestJournal[req.id]) return refreshRequest(req.id);

  const method = req.method === "GET" ? "GET" : "POST";
  let entry = await saveJournal(req.id, {
    state: "RECEIVED", path: req.path, method, tabId: sender.tab.id,
    receivedAt: Date.now(), startedAt: Date.now(), cwd: req.body?.cwd || null, error: null,
    statusDetail: null
  });

  if (req.path === "/v1/exec" && method === "POST") {
    const timeoutMs = processTimeoutMs(req);
    entry = await saveJournal(req.id, {
      state: "EXECUTING", mode: "process", executionStartedAt: Date.now(),
      executionTimeoutMs: timeoutMs, timeoutSeconds: Math.round(timeoutMs / 1000),
      lastProgressAt: Date.now(), lastOutputLength: 0
    });
    try {
      const started = await localCall("/v1/process/start", "POST", req.body ?? {}, 15000);
      if (!started.ok || !started.payload?.id)
        return saveJournal(req.id, {state: "STALLED", error: `PROCESS_START_FAILED:${started.httpStatus}`, statusDetail: `PROCESS_START_FAILED:${started.httpStatus}`});
      return saveJournal(req.id, {
        state: "EXECUTING", agentProcessId: started.payload.id, pid: started.payload.pid,
        lastAgentPollAt: Date.now(), transportError: null, statusDetail: null
      });
    } catch (e) {
      return saveJournal(req.id, {state: "STALLED", error: `PROCESS_START_TRANSPORT:${String(e?.message || e)}`, statusDetail: "PROCESS_START_TRANSPORT"});
    }
  }

  entry = await saveJournal(req.id, {state: "EXECUTING", mode: "direct", executionStartedAt: Date.now()});
  try {
    const local = await localCall(req.path, method, req.body ?? null, 45000);
    return finishRequest(entry, local);
  } catch (e) {
    return saveJournal(req.id, {state: "STALLED", error: `DIRECT_CALL_FAILED:${String(e?.message || e)}`, statusDetail: "DIRECT_CALL_FAILED"});
  }
}

async function cancelRequest(id) {
  if (!validId(id)) throw new Error("INVALID_REQUEST_ID");
  const s = await getState();
  const entry = s.requestJournal[id];
  if (!entry) throw new Error("UNKNOWN_REQUEST_ID");
  if (entry.state !== "EXECUTING") return entry;
  return stopProcessRequest(entry, "CANCELLED_BY_USER");
}

function newestRequest(state) {
  const entries = Object.values(state.requestJournal).sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  return entries[0] || null;
}

chrome.runtime.onMessage.addListener((m, sender, sendResponse) => {
  (async () => {
    if (!m || typeof m.type !== "string") throw new Error("BAD_MESSAGE");

    if (m.type === "SAVE_CONFIG") {
      const update = {port: validPort(m.port)};
      if (typeof m.token === "string" && m.token.trim()) update.token = m.token.trim();
      await chrome.storage.local.set(update);
      const s = await getState();
      return {tokenSaved: Boolean(s.token), port: s.port};
    }

    if (m.type === "GET_STATUS") {
      let s = await getState();
      let health;
      try { health = await localCall("/health", "GET", null, 4000); }
      catch (e) { health = {ok: false, error: String(e?.message || e)}; }
      let active = newestRequest(s);
      if (active?.state === "EXECUTING") {
        try { active = await refreshRequest(active.requestId); } catch (_) {}
      }
      return {
        protocolVersion: PROTOCOL_VERSION,
        tokenSaved: Boolean(s.token), port: s.port, enabledTabId: s.enabledTabId,
        automationEnabled: s.automationEnabled, health, activeRequest: active,
        defaultProcessTimeoutSeconds: Math.round(DEFAULT_PROCESS_TIMEOUT_MS / 1000)
      };
    }

    if (m.type === "ENABLE_TAB") {
      const tabId = Number(m.tabId);
      if (!Number.isInteger(tabId)) throw new Error("INVALID_TAB_ID");
      const tab = await chrome.tabs.get(tabId);
      if (!tab.url || !tab.url.startsWith("https://chatgpt.com/")) throw new Error("NOT_CHATGPT_TAB");
      const s = await getState();
      if (!s.token) throw new Error("TOKEN_NOT_CONFIGURED");
      await chrome.storage.local.set({enabledTabId: tabId, automationEnabled: true});
      await chrome.tabs.sendMessage(tabId, {type: "ARM_NOW"});
      return {enabledTabId: tabId};
    }

    if (m.type === "DISABLE") {
      const s = await getState();
      await chrome.storage.local.set({enabledTabId: null, automationEnabled: false});
      if (s.enabledTabId) {
        try { await chrome.tabs.sendMessage(s.enabledTabId, {type: "DISARM"}); } catch (_) {}
      }
      return {disabled: true};
    }

    if (m.type === "CONTENT_READY") {
      const s = await getState();
      const armed = Boolean(s.automationEnabled && sender.tab && sender.tab.id === s.enabledTabId);
      const pending = Object.values(s.requestJournal)
        .filter(x => x.tabId === sender.tab?.id && x.state !== "SENT" && !(x.state === "STALLED" && x.reportedAt))
        .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))[0];
      return {armed, pendingRequestId: pending?.requestId || null, protocolVersion: PROTOCOL_VERSION};
    }

    if (m.type === "START_REQUEST") return startRequest(m, sender);

    if (m.type === "GET_REQUEST_STATUS") {
      const s = await getState();
      assertArmed(sender, s);
      if (!validId(m.requestId)) throw new Error("INVALID_REQUEST_ID");
      return refreshRequest(m.requestId);
    }

    if (m.type === "CANCEL_REQUEST") return cancelRequest(m.requestId);

    if (m.type === "MARK_SENDING") {
      const s = await getState(); assertArmed(sender, s);
      return saveJournal(m.requestId, {state: "SENDING", sendingAt: Date.now()});
    }

    if (m.type === "MARK_SENT") {
      const s = await getState(); assertArmed(sender, s);
      const current = s.requestJournal[m.requestId];
      if (m.preserveState === true && current?.state === "STALLED")
        return saveJournal(m.requestId, {reportedAt: Date.now()});
      return saveJournal(m.requestId, {state: "SENT", sentAt: Date.now()});
    }

    if (m.type === "EXECUTE") {
      const started = await startRequest({request: m.request}, sender);
      if (started.state === "RESPONSE_READY") return started.result;
      return {
        id: m.request?.id, path: m.request?.path, method: m.request?.method || "POST", completedAt: isoNow(),
        local: {ok: false, httpStatus: 202, payload: {ok: false, pending: true, state: started.state, requestId: m.request?.id}}
      };
    }

    throw new Error("UNKNOWN_MESSAGE");
  })().then(
    v => sendResponse({status: "OK", value: v}),
    e => sendResponse({status: "ERROR", error: String(e?.message || e)})
  );
  return true;
});
