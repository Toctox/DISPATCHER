const DEFAULT_PORT = 18765;
const ALLOWED = new Set([
  "/health", "/v1/status", "/v1/processes",
  "/v1/exec", "/v1/process/start", "/v1/process/stop",
  "/v1/process/output", "/v1/file/read", "/v1/file/write", "/v1/file/list"
]);

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

function trimValue(v, depth=0) {
  if (depth > 6) return "[depth-truncated]";
  if (typeof v === "string") {
    const max = 12000;
    return v.length > max ? v.slice(0,max) + `\n...[truncated ${v.length-max} chars]` : v;
  }
  if (Array.isArray(v)) return v.slice(0,100).map(x => trimValue(x, depth+1));
  if (v && typeof v === "object") {
    const out = {};
    for (const [k,x] of Object.entries(v).slice(0,120)) out[k] = trimValue(x, depth+1);
    return out;
  }
  return v;
}

async function getState() {
  const d = await chrome.storage.local.get([
    "token","port","enabledTabId","automationEnabled","executedResults"
  ]);
  return {
    token: typeof d.token === "string" ? d.token : "",
    port: validPort(d.port),
    enabledTabId: Number.isInteger(d.enabledTabId) ? d.enabledTabId : null,
    automationEnabled: d.automationEnabled === true,
    executedResults: d.executedResults && typeof d.executedResults === "object" ? d.executedResults : {}
  };
}

async function saveResult(id, result) {
  const s = await getState();
  const items = Object.entries(s.executedResults).filter(([k]) => k !== id).slice(-49);
  items.push([id, {at: Date.now(), result}]);
  await chrome.storage.local.set({executedResults: Object.fromEntries(items)});
}

async function localCall(path, method="GET", body=null) {
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
  const timer = setTimeout(() => controller.abort(), 650000);
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

async function execute(message, sender) {
  const s = await getState();
  if (!s.automationEnabled || !sender.tab || sender.tab.id !== s.enabledTabId)
    throw new Error("TAB_NOT_ARMED");

  const req = message.request || {};
  if (!validId(req.id)) throw new Error("INVALID_REQUEST_ID");
  if (!ALLOWED.has(req.path)) throw new Error("PATH_NOT_ALLOWED");

  if (s.executedResults[req.id]) {
    return {duplicate: true, ...s.executedResults[req.id].result};
  }

  const method = req.method === "GET" ? "GET" : "POST";
  const local = await localCall(req.path, method, req.body ?? null);
  const result = {
    id: req.id,
    path: req.path,
    method,
    completedAt: new Date().toISOString(),
    local
  };
  await saveResult(req.id, result);
  return result;
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
      const s = await getState();
      let health;
      try { health = await localCall("/health","GET"); }
      catch (e) { health = {ok:false,error:String(e.message || e)}; }
      return {
        tokenSaved: Boolean(s.token),
        port: s.port,
        enabledTabId: s.enabledTabId,
        automationEnabled: s.automationEnabled,
        health
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
      await chrome.tabs.sendMessage(tabId, {type:"ARM_NOW"});
      return {enabledTabId: tabId};
    }

    if (m.type === "DISABLE") {
      const s = await getState();
      await chrome.storage.local.set({enabledTabId:null, automationEnabled:false});
      if (s.enabledTabId) {
        try { await chrome.tabs.sendMessage(s.enabledTabId,{type:"DISARM"}); } catch (_) {}
      }
      return {disabled:true};
    }

    if (m.type === "EXECUTE") return await execute(m, sender);
    throw new Error("UNKNOWN_MESSAGE");
  })().then(
    v => sendResponse({status:"OK", value:v}),
    e => sendResponse({status:"ERROR", error:String(e?.message || e)})
  );
  return true;
});
