const MARKER = "LOCAL_CODEX_REQUEST_V1";
const RESULT_MARKER = "LOCAL_CODEX_RESULT_V1";
const PROCESSED = "data-chatops-codex-processed";

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "chatops-codex-toast" + (isError ? " error" : "");
  el.textContent = message;
  document.documentElement.appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

function assistantCodeBlocks() {
  return [...document.querySelectorAll("pre code")].filter((code) => {
    const message = code.closest('[data-message-author-role="assistant"]');
    return Boolean(message);
  });
}

function parseRequest(code) {
  const text = code.innerText.trim();
  if (!text.startsWith(MARKER)) return null;
  const raw = text.slice(MARKER.length).trim();
  if (!raw) return null;
  const request = JSON.parse(raw);
  if (!request || request.version !== "CHATOPS_NATIVE_V1") {
    throw new Error("Versão de protocolo inválida.");
  }
  return request;
}

function describe(request) {
  const operation = request.operation || "?";
  const sandbox = request.sandbox || "read-only";
  const workspace = request.workspace || "(padrão)";
  return [
    "Executar pedido local no Supremo?",
    "",
    "Operação: " + operation,
    "Sandbox: " + sandbox,
    "Workspace: " + workspace,
    "",
    "O host local não aceita shell arbitrário."
  ].join("\n");
}

function attachButton(code, request) {
  const pre = code.closest("pre");
  if (!pre || pre.getAttribute(PROCESSED) === "1") return;
  pre.setAttribute(PROCESSED, "1");

  const button = document.createElement("button");
  button.className = "chatops-codex-run";
  button.type = "button";
  button.textContent = "Executar no Supremo";

  button.addEventListener("click", async () => {
    if (!confirm(describe(request))) return;
    if (request.sandbox === "workspace-write") {
      const second = confirm(
        "Este pedido permite escrita no workspace local. Confirmar workspace-write?"
      );
      if (!second) return;
      request.confirmWrite = true;
    }

    button.disabled = true;
    button.textContent = "Enviando…";

    try {
      const reply = await chrome.runtime.sendMessage({
        type: "chatops.native.request",
        payload: request
      });
      if (!reply?.ok) throw new Error(reply?.error || "Falha ao enviar.");
      button.textContent = "Enviado";
      toast("Pedido enviado ao Codex local.");
    } catch (error) {
      button.disabled = false;
      button.textContent = "Tentar novamente";
      toast(String(error), true);
    }
  });

  pre.insertAdjacentElement("afterend", button);
}

function scan() {
  for (const code of assistantCodeBlocks()) {
    try {
      const request = parseRequest(code);
      if (request) attachButton(code, request);
    } catch (error) {
      console.warn("ChatOps Codex request ignored:", error);
    }
  }
}

function formatResult(payload) {
  const result = {
    version: payload.version,
    requestId: payload.requestId,
    jobId: payload.jobId,
    status: payload.status,
    exitCode: payload.exitCode ?? null,
    sandbox: payload.sandbox || null,
    workspace: payload.workspace || null,
    finalMessage: payload.finalMessage || "",
    error: payload.error || null,
    startedAt: payload.startedAt || null,
    finishedAt: payload.finishedAt || null
  };
  return RESULT_MARKER + "\n" + JSON.stringify(result, null, 2);
}

function composerElement() {
  return (
    document.querySelector("#prompt-textarea") ||
    document.querySelector('textarea[data-id="root"]') ||
    document.querySelector('form textarea') ||
    document.querySelector('form [contenteditable="true"]')
  );
}

function composerHasText(el) {
  if (!el) return false;
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
    return Boolean(el.value.trim());
  }
  return Boolean((el.innerText || el.textContent || "").trim());
}

function setComposer(text) {
  const el = composerElement();
  if (!el) return false;
  if (composerHasText(el)) return false;

  el.focus();
  if (el instanceof HTMLTextAreaElement || el instanceof HTMLInputElement) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, text);
    else el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }

  document.execCommand("insertText", false, text);
  el.dispatchEvent(new InputEvent("input", {
    bubbles: true,
    inputType: "insertText",
    data: text
  }));
  return true;
}

async function copyFallback(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Resultado copiado. O compositor já continha texto.");
  } catch {
    toast("Resultado pronto, mas não foi possível preencher o compositor.", true);
  }
}

chrome.runtime.onMessage.addListener((message) => {
  if (!message) return;
  if (message.type === "chatops.native.error") {
    toast("Host local: " + message.error, true);
    return;
  }
  if (message.type !== "chatops.native.message") return;

  const payload = message.payload || {};
  if (payload.type === "codex.accepted") {
    toast("Codex iniciou: " + (payload.jobId || ""));
    return;
  }
  if (payload.type !== "codex.result" && payload.type !== "codex.status") return;

  const text = formatResult(payload);
  if (setComposer(text)) {
    toast("Resultado colocado no compositor. Revise e envie.");
  } else {
    copyFallback(text);
  }
});

const observer = new MutationObserver(() => scan());
observer.observe(document.documentElement, { childList: true, subtree: true });
scan();
