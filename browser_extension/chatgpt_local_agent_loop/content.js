(() => {
  let armed = false;
  let busy = false;
  let protocolCompatible = false;
  let recoveryTimer = null;
  const seen = new Set();
  let scanTimer = null;
  let currentRequestId = null;
  let currentStartedAt = null;
  let currentState = "IDLE";
  let currentDetail = "";
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const assistantRoots = () => [
    ...document.querySelectorAll('[data-message-author-role="assistant"]'),
    ...document.querySelectorAll('article[data-turn="assistant"]')
  ];

  function badge() {
    let el = document.getElementById("local-agent-loop-status");
    if (!el) {
      el = document.createElement("div");
      el.id = "local-agent-loop-status";
      Object.assign(el.style, {
        position: "fixed", right: "14px", bottom: "14px", zIndex: "2147483647",
        padding: "7px 10px", borderRadius: "8px", background: "rgba(20,20,20,.88)",
        color: "#eee", font: "12px/1.25 system-ui,sans-serif", boxShadow: "0 2px 12px rgba(0,0,0,.35)",
        pointerEvents: "none", whiteSpace: "pre-wrap", maxWidth: "340px"
      });
      document.documentElement.appendChild(el);
    }
    return el;
  }

  function setState(state, detail = currentDetail) {
    currentState = state;
    currentDetail = detail || "";
    const elapsed = currentStartedAt ? Math.max(0, Math.floor((Date.now() - currentStartedAt) / 1000)) : 0;
    badge().textContent = `Local Agent: ${state}` +
      (currentRequestId ? `\n${currentRequestId}` : "") +
      (currentStartedAt ? ` · ${elapsed}s` : "") +
      (currentDetail ? `\n${currentDetail}` : "");
  }
  setInterval(() => setState(currentState, currentDetail), 1000);

  function blockTexts() {
    const out = [];
    for (const root of assistantRoots()) {
      for (const n of root.querySelectorAll("pre code, pre")) {
        const text = String(n.textContent || "").replace(/\r\n?/g, "\n").trim();
        if (text.startsWith("LOCAL_AGENT_V1")) out.push(text);
      }
    }
    return [...new Set(out)];
  }

  function snapshotExistingBlocks() {
    for (const text of blockTexts()) seen.add(text);
  }

  function parseBlock(text) {
    const lines = text.split("\n");
    if (lines.shift()?.trim() !== "LOCAL_AGENT_V1") return null;
    const raw = lines.join("\n").trim();
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== "object" || typeof obj.id !== "string" || typeof obj.path !== "string")
      throw new Error("INVALID_LOCAL_AGENT_BLOCK");
    return obj;
  }

  function composer() {
    for (const s of [
      'textarea[data-testid="prompt-textarea"]', 'textarea#prompt-textarea',
      '[data-testid="prompt-textarea"][contenteditable]', '#prompt-textarea[contenteditable]'
    ]) {
      for (const el of document.querySelectorAll(s)) {
        if (el && el.isConnected && !el.disabled && getComputedStyle(el).display !== "none") return el;
      }
    }
    return null;
  }

  const composerText = el => !el ? "" : el.tagName === "TEXTAREA" ? el.value : (el.innerText || el.textContent || "");

  function insert(el, text) {
    el.focus();
    if (el.tagName === "TEXTAREA") {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(el, text);
    } else {
      const selection = document.getSelection();
      const range = document.createRange();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
      if (!document.execCommand("insertText", false, text)) throw new Error("COMPOSER_INSERT_FAILED");
    }
    el.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: text}));
    el.dispatchEvent(new Event("change", {bubbles: true}));
  }

  async function waitComposer() {
    const deadline = Date.now() + 60000;
    while (Date.now() < deadline) {
      const el = composer();
      const stop = document.querySelector('button[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="Parar"]');
      if (el && !stop && composerText(el).trim() === "") return el;
      setState("WAITING_COMPOSER", currentDetail);
      await sleep(250);
    }
    throw new Error("COMPOSER_NOT_READY");
  }

  async function submit(text) {
    setState("SENDING", currentDetail);
    const el = await waitComposer();
    insert(el, text);
    await sleep(150);
    for (const s of [
      'button[data-testid="send-button"]', 'button#composer-submit-button',
      'button[aria-label="Send prompt"]', 'button[aria-label="Enviar prompt"]'
    ]) {
      const b = document.querySelector(s);
      if (b && !b.disabled && b.getAttribute("aria-disabled") !== "true") { b.click(); return; }
    }
    el.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", code: "Enter", bubbles: true, cancelable: true}));
    el.dispatchEvent(new KeyboardEvent("keyup", {key: "Enter", code: "Enter", bubbles: true, cancelable: true}));
  }

  function resultMessage(requestId, response) {
    let payload = {protocol: "LOCAL_AGENT_RESULT_V1", requestId, response};
    let raw = JSON.stringify(payload);
    if (raw.length > 28000) {
      payload = {protocol: "LOCAL_AGENT_RESULT_V1", requestId, response: {status: "ERROR", error: "RESULT_TOO_LARGE_AFTER_TRUNCATION"}};
      raw = JSON.stringify(payload);
    }
    return `LOCAL_AGENT_RESULT_V1\n${raw}\nEND_LOCAL_AGENT_RESULT_V1`;
  }

  async function runtimeMessage(message, retries = 6) {
    let last;
    for (let i = 0; i < retries; i++) {
      try {
        const r = await chrome.runtime.sendMessage(message);
        if (!r || r.status !== "OK") throw new Error(r?.error || "EXTENSION_CHANNEL_ERROR");
        return r.value;
      } catch (e) {
        last = e;
        setState("RECOVERING_CHANNEL", String(e?.message || e));
        await sleep(Math.min(5000, 500 * (i + 1)));
      }
    }
    throw last || new Error("EXTENSION_CHANNEL_ERROR");
  }

  async function checkProtocol() {
    try {
      const status = await runtimeMessage({type: "GET_STATUS"}, 1);
      protocolCompatible = Object.prototype.hasOwnProperty.call(status || {}, "activeRequest");
      if (!protocolCompatible) {
        armed = false;
        setState("UPDATE_REQUIRED", "content 0.2.1 / service worker antigo");
      }
      return protocolCompatible;
    } catch (e) {
      protocolCompatible = false;
      armed = false;
      setState("CHANNEL_OFFLINE", String(e?.message || e));
      return false;
    }
  }

  function startRecoveryLoop() {
    if (recoveryTimer) return;
    recoveryTimer = setInterval(async () => {
      if (protocolCompatible) {
        clearInterval(recoveryTimer);
        recoveryTimer = null;
        return;
      }
      if (await checkProtocol()) {
        clearInterval(recoveryTimer);
        recoveryTimer = null;
        await initializeReadyState(true);
      }
    }, 3000);
  }

  async function deliverRequest(requestId) {
    currentRequestId = requestId;
    if (!currentStartedAt) currentStartedAt = Date.now();
    const deadline = Date.now() + 2 * 60 * 60 * 1000;
    while (Date.now() < deadline) {
      let st;
      try { st = await runtimeMessage({type: "GET_REQUEST_STATUS", requestId}, 3); }
      catch (e) { setState("RECOVERING_CHANNEL", String(e?.message || e)); await sleep(1500); continue; }
      setState(st.state || "UNKNOWN", st.error || st.transportError || "");

      if (st.state === "RESPONSE_READY" || st.state === "SENDING") {
        await runtimeMessage({type: "MARK_SENDING", requestId}, 3);
        await submit(resultMessage(requestId, {status: "OK", value: st.result}));
        await runtimeMessage({type: "MARK_SENT", requestId}, 3);
        setState("SENT", "");
        return;
      }
      if (st.state === "SENT") { setState("SENT", ""); return; }
      if (st.state === "STALLED") {
        await submit(resultMessage(requestId, {status: "ERROR", error: st.error || "REQUEST_STALLED"}));
        await runtimeMessage({type: "MARK_SENT", requestId, preserveState: true}, 3);
        setState("STALLED", st.error || "");
        return;
      }
      await sleep(1000);
    }
    await submit(resultMessage(requestId, {status: "ERROR", error: "REQUEST_WATCHDOG_TIMEOUT"}));
    setState("STALLED", "REQUEST_WATCHDOG_TIMEOUT");
  }

  async function processRequest(req, textKey) {
    busy = true;
    seen.add(textKey);
    currentRequestId = req.id;
    currentStartedAt = Date.now();
    setState("RECEIVED", "");
    try {
      if (!protocolCompatible && !(await checkProtocol())) throw new Error("UPDATE_REQUIRED");
      const started = await runtimeMessage({type: "START_REQUEST", request: req}, 6);
      setState(started.state || "EXECUTING", started.error || "");
      await deliverRequest(req.id);
    } catch (e) {
      const msg = String(e?.message || e);
      setState("STALLED", msg);
      try { await submit(resultMessage(req.id, {status: "ERROR", error: msg})); } catch (_) {}
    } finally {
      busy = false;
      currentRequestId = null;
      currentStartedAt = null;
      scheduleScan();
    }
  }

  async function scan() {
    scanTimer = null;
    if (!armed || busy || !protocolCompatible) return;
    for (const text of blockTexts()) {
      if (seen.has(text)) continue;
      let req;
      try { req = parseBlock(text); } catch (_) { continue; }
      if (!req) continue;
      await processRequest(req, text);
      return;
    }
  }

  function scheduleScan() {
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, 500);
  }

  async function initializeReadyState(fromRecovery = false) {
    snapshotExistingBlocks();
    try {
      const r = await runtimeMessage({type: "CONTENT_READY"}, 3);
      armed = Boolean(r?.armed);
      setState(armed ? "READY" : "DISARMED", fromRecovery ? "protocolo recuperado" : "");
      if (armed && r?.pendingRequestId) {
        busy = true;
        currentRequestId = r.pendingRequestId;
        currentStartedAt = Date.now();
        try { await deliverRequest(r.pendingRequestId); }
        finally { busy = false; currentRequestId = null; currentStartedAt = null; scheduleScan(); }
      } else if (armed) {
        scheduleScan();
      }
    } catch (e) {
      setState("CHANNEL_OFFLINE", String(e?.message || e));
      protocolCompatible = false;
      armed = false;
      startRecoveryLoop();
    }
  }

  chrome.runtime.onMessage.addListener((m, _sender, sendResponse) => {
    if (m?.type === "ARM_NOW") {
      if (!protocolCompatible) {
        setState("UPDATE_REQUIRED", "service worker incompatível");
        sendResponse({ok: false, error: "PROTOCOL_MISMATCH"});
        startRecoveryLoop();
        return;
      }
      snapshotExistingBlocks();
      armed = true;
      setState("READY", "");
      scheduleScan();
      sendResponse({ok: true, ignoredExisting: seen.size});
      return;
    }
    if (m?.type === "DISARM") {
      armed = false;
      setState("DISARMED", "");
      sendResponse({ok: true});
      return;
    }
  });

  new MutationObserver(scheduleScan).observe(document.documentElement, {subtree: true, childList: true, characterData: true});

  (async () => {
    snapshotExistingBlocks();
    if (await checkProtocol()) await initializeReadyState(false);
    else startRecoveryLoop();
  })();
})();
