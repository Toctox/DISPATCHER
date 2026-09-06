const status = document.getElementById("status");
const factoryMessage = document.getElementById("factory-message");
const choice = document.getElementById("factory-tab-choice");
async function factoryRequest(action, fields = {}) {
  const value = await chrome.runtime.sendMessage({type: "FACTORY_TAB_OPTIONS", action, ...fields});
  if (value?.status !== "OK") throw new Error(value?.errorCode || "CHATGPT_BRIDGE_UNAVAILABLE");
  return value;
}
async function refreshFactoryStatus() {
  const value = await factoryRequest("STATUS");
  document.getElementById("bridge-status").textContent = value.bridgeStatus;
  document.getElementById("factory-status").textContent = value.configured ? "CONFIGURED" : "NOT CONFIGURED";
  document.getElementById("factory-tab-id").textContent = value.factoryTabId ?? "—";
  document.getElementById("factory-origin-valid").textContent = value.originValid ? "yes" : "no";
  document.getElementById("factory-reserved").textContent = value.reserved ? "yes" : "no";
  document.getElementById("register-factory-tab").disabled = value.reserved;
  document.getElementById("clear-factory-tab").disabled = value.reserved;
  factoryMessage.textContent = value.errorCode || "";
}
async function refreshFactoryTabs() {
  const value = await factoryRequest("LIST");
  choice.replaceChildren();
  const placeholder = document.createElement("option");
  placeholder.value = ""; placeholder.textContent = "Selecione uma aba por Tab ID";
  choice.append(placeholder);
  for (const tab of value.tabs) {
    const option = document.createElement("option");
    option.value = String(tab.factoryTabId);
    option.textContent = `Tab ID ${tab.factoryTabId} — janela ${tab.windowId}, posição ${tab.position}`;
    choice.append(option);
  }
  choice.value = ""; // Explicit choice every time, including when only one tab exists.
  await refreshFactoryStatus();
}
async function factoryUI(operation) {
  try { await operation(); }
  catch (error) { factoryMessage.textContent = error.message; }
}
document.getElementById("factory-tab-form").addEventListener("submit", event => {
  event.preventDefault();
  void factoryUI(async () => {
    if (choice.value === "") throw new Error("CHATGPT_FACTORY_TAB_NOT_CONFIGURED");
    await factoryRequest("REGISTER", {factoryTabId: Number(choice.value)});
    await refreshFactoryStatus();
  });
});
document.getElementById("clear-factory-tab").addEventListener("click", () => {
  void factoryUI(async () => { await factoryRequest("CLEAR"); await refreshFactoryStatus(); });
});
document.getElementById("refresh-factory-tabs").addEventListener("click", () => {
  void factoryUI(refreshFactoryTabs);
});
document.getElementById("extension-id").textContent = chrome.runtime.id;
await chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
const saved = await chrome.storage.local.get(["pairing", "bridgeStatus"]);
document.getElementById("port").value = saved.pairing?.port || 8765;
status.textContent = saved.bridgeStatus || "NOT_PAIRED";
document.getElementById("pairing-form").addEventListener("submit", async event => {
  event.preventDefault();
  const port = Number(document.getElementById("port").value);
  const input = document.getElementById("secret");
  const secret = input.value.trim();
  if (!Number.isInteger(port) || port < 1 || port > 65535 || !/^[0-9a-f]{64}$/.test(secret)) {
    status.textContent = "PAIRING_INVALID";
    return;
  }
  await chrome.storage.local.set({pairing: {port, secret, enabled: true}});
  input.value = "";
  status.textContent = "PAIRING_SAVED — aguardando conexão (até 30 segundos)";
});
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.bridgeStatus) status.textContent = changes.bridgeStatus.newValue;
  if (area === "local" && (changes.bridgeStatus || changes.factoryTab || changes.activeReservation)) {
    void factoryUI(refreshFactoryStatus);
  }
});
await factoryUI(refreshFactoryTabs);
