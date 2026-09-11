(() => {
  const CLAIM_PREFIX = "local-agent-delivery-claim-";
  const RESULT_PREFIX = "LOCAL_AGENT_RESULT_V1";

  function safeId(requestId) {
    return CLAIM_PREFIX + String(requestId || "").replace(/[^A-Za-z0-9._:-]/g, "_");
  }

  function parseRequestId(text) {
    const normalized = String(text || "").replace(/\r\n?/g, "\n").trim();
    if (!normalized.startsWith(RESULT_PREFIX + "\n")) return null;
    const lines = normalized.split("\n");
    if (lines[0] !== RESULT_PREFIX) return null;
    const end = lines.lastIndexOf("END_LOCAL_AGENT_RESULT_V1");
    if (end < 2) return null;
    try {
      const payload = JSON.parse(lines.slice(1, end).join("\n"));
      return typeof payload?.requestId === "string" ? payload.requestId : null;
    } catch (_) {
      return null;
    }
  }

  function hasClaim(requestId) {
    return Boolean(document.getElementById(safeId(requestId)));
  }

  function addClaim(requestId, source = "send") {
    if (!requestId || hasClaim(requestId)) return false;
    const marker = document.createElement("meta");
    marker.id = safeId(requestId);
    marker.dataset.localAgentDeliveryClaim = requestId;
    marker.dataset.source = source;
    marker.dataset.claimedAt = String(Date.now());
    document.documentElement.appendChild(marker);
    return true;
  }

  function composer() {
    for (const selector of [
      'textarea[data-testid="prompt-textarea"]',
      'textarea#prompt-textarea',
      '[data-testid="prompt-textarea"][contenteditable]',
      '#prompt-textarea[contenteditable]'
    ]) {
      const el = document.querySelector(selector);
      if (el && el.isConnected) return el;
    }
    return null;
  }

  function composerText(el) {
    if (!el) return "";
    return el.tagName === "TEXTAREA" ? el.value : (el.innerText || el.textContent || "");
  }

  function clearComposer(el) {
    if (!el) return;
    try {
      el.focus();
      if (el.tagName === "TEXTAREA") {
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
        if (setter) setter.call(el, "");
        else el.value = "";
      } else {
        const selection = document.getSelection();
        const range = document.createRange();
        range.selectNodeContents(el);
        selection.removeAllRanges();
        selection.addRange(range);
        document.execCommand("insertText", false, "");
      }
      el.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "deleteContent", data: null}));
      el.dispatchEvent(new Event("change", {bubbles: true}));
    } catch (_) {}
  }

  function claimOrBlock(event) {
    const el = composer();
    const text = composerText(el);
    const requestId = parseRequestId(text);
    if (!requestId) return;

    if (addClaim(requestId, "send")) return;

    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    clearComposer(el);

    const badge = document.getElementById("local-agent-loop-status");
    if (badge) badge.textContent = `Local Agent: DELIVERY_SUPPRESSED\n${requestId}\nresultado duplicado bloqueado`;
  }

  document.addEventListener("click", event => {
    const target = event.target instanceof Element ? event.target.closest("button") : null;
    if (!target) return;
    if (
      target.matches('button[data-testid="send-button"]') ||
      target.matches('button#composer-submit-button') ||
      target.getAttribute("aria-label") === "Send prompt" ||
      target.getAttribute("aria-label") === "Enviar prompt"
    ) claimOrBlock(event);
  }, true);

  document.addEventListener("keydown", event => {
    if (event.key !== "Enter" || event.shiftKey) return;
    const el = composer();
    if (!el) return;
    if (event.target === el || el.contains(event.target)) claimOrBlock(event);
  }, true);

  function snapshotDeliveredResults() {
    const roots = [
      ...document.querySelectorAll('[data-message-author-role="user"]'),
      ...document.querySelectorAll('article[data-turn="user"]')
    ];
    for (const root of roots) {
      const requestId = parseRequestId(root.textContent || "");
      if (requestId) addClaim(requestId, "history");
    }
  }

  snapshotDeliveredResults();
  new MutationObserver(snapshotDeliveredResults).observe(document.documentElement, {
    subtree: true,
    childList: true
  });
})();
