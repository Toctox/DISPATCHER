const $ = id => document.getElementById(id);
async function msg(value) {
  const r = await chrome.runtime.sendMessage(value);
  if (!r || r.status !== "OK") throw new Error(r?.error || "extension error");
  return r.value;
}
function elapsed(ms) {
  if (!ms) return "-";
  return Math.max(0, Math.floor((Date.now() - ms) / 1000)) + "s";
}
async function refresh() {
  try {
    const s = await msg({type: "GET_STATUS"});
    $("port").value = s.port || 18765;
    $("token").placeholder = s.tokenSaved ? "token salvo (deixe vazio para manter)" : "cole token.txt somente aqui";
    const hp = s.health?.payload || s.health;
    const r = s.activeRequest;
    $("status").textContent =
      `Agente: ${s.health?.ok ? "OK" : "OFFLINE"}\n` +
      `Token: ${s.tokenSaved ? "SALVO" : "NÃO CONFIGURADO"}\n` +
      `Automação: ${s.automationEnabled ? "ATIVA" : "DESATIVADA"}\n` +
      `Tab ID: ${s.enabledTabId ?? "-"}\n` +
      (r ? `Pedido: ${r.requestId}\nEstado: ${r.state}\nDecorrido: ${elapsed(r.startedAt || r.receivedAt)}\nErro: ${r.error || r.transportError || "-"}\n` : "Pedido: nenhum\n") +
      `Health: ${JSON.stringify(hp ?? {})}`;
  } catch (e) { $("status").textContent = String(e.message || e); }
}
$("save").onclick = async () => {
  try {
    await msg({type: "SAVE_CONFIG", token: $("token").value, port: Number($("port").value)});
    $("token").value = "";
    await refresh();
  } catch (e) { $("status").textContent = String(e.message || e); }
};
$("health").onclick = refresh;
$("enable").onclick = async () => {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.id) throw new Error("Nenhuma aba ativa");
    await msg({type: "ENABLE_TAB", tabId: tab.id});
    await refresh();
  } catch (e) { $("status").textContent = String(e.message || e); }
};
$("disable").onclick = async () => {
  try { await msg({type: "DISABLE"}); await refresh(); }
  catch (e) { $("status").textContent = String(e.message || e); }
};
refresh();
setInterval(refresh, 1500);
