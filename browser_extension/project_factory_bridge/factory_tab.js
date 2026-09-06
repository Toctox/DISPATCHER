import {exactKeys, fail, safeCode} from "./protocol.js";

function hasChatGPTOrigin(tab) {
  try {
    const url = new URL(tab.url);
    return url.origin === "https://chatgpt.com" && !url.username && !url.password;
  } catch { return false; }
}
function isPristineRoute(tab) {
  try {
    const url = new URL(tab.url);
    return url.origin === "https://chatgpt.com" && url.pathname === "/" &&
      !url.search && !url.hash && !url.username && !url.password;
  } catch { return false; }
}
export function isChatGPTTab(tab) {
  return hasChatGPTOrigin(tab) && !tab.incognito && !tab.discarded && !tab.frozen &&
    tab.status !== "loading" && !tab.pendingUrl;
}

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export class FactoryTabRegistry {
  constructor(api, instanceId) { this.api = api; this.instanceId = instanceId; }
  async rawTab(tabId) {
    let tab;
    try { tab = await this.api.tabs.get(tabId); }
    catch { fail("CHATGPT_FACTORY_TAB_NOT_FOUND"); }
    if (!tab || tab.id !== tabId) fail("CHATGPT_FACTORY_TAB_NOT_FOUND");
    return tab;
  }
  async tab(tabId) {
    const tab = await this.rawTab(tabId);
    if (!isChatGPTTab(tab)) fail("CHATGPT_FACTORY_TAB_INVALID");
    return tab;
  }
  async identity() {
    const {factoryTab} = await this.api.storage.local.get("factoryTab");
    if (!factoryTab) fail("CHATGPT_FACTORY_TAB_NOT_CONFIGURED");
    if (!exactKeys(factoryTab, ["factoryTabId", "browserInstanceId"]) ||
        !Number.isSafeInteger(factoryTab.factoryTabId) || factoryTab.factoryTabId < 0 ||
        factoryTab.browserInstanceId !== this.instanceId) fail("CHATGPT_FACTORY_TAB_INVALID");
    return factoryTab;
  }
  async registered() {
    const factoryTab = await this.identity();
    await this.tab(factoryTab.factoryTabId);
    return factoryTab;
  }
  async prepareRoot() {
    const registration = await this.identity();
    let tab = await this.tab(registration.factoryTabId);
    if (isPristineRoute(tab) || tab.status !== "complete") return registration;

    // Normalize only before an unpinned PRECHECK, while no reservation exists. A full
    // document navigation is safe here because no documentId has been issued yet.
    try {
      await this.api.tabs.update(registration.factoryTabId, {url: "https://chatgpt.com/"});
    } catch { fail("CHATGPT_FACTORY_TAB_INVALID"); }

    const deadline = Date.now() + 15000;
    while (Date.now() < deadline) {
      tab = await this.rawTab(registration.factoryTabId);
      if (isPristineRoute(tab) && isChatGPTTab(tab)) return registration;
      await delay(100);
    }
    fail("CHATGPT_FACTORY_TAB_INVALID");
  }
  async list() {
    // Options-only discovery. No automatic selection, titles, conversation URLs or active-tab hints.
    return (await this.api.tabs.query({url: "https://chatgpt.com/*"}))
      .filter(tab => Number.isSafeInteger(tab.id) && tab.id >= 0 && isChatGPTTab(tab))
      .map(tab => ({factoryTabId: tab.id, windowId: tab.windowId, position: tab.index + 1}));
  }
  async status() {
    const {factoryTab} = await this.api.storage.local.get("factoryTab");
    const {activeReservation} = await this.api.storage.local.get("activeReservation");
    let originValid = false, errorCode = "";
    if (factoryTab) {
      if (Number.isSafeInteger(factoryTab.factoryTabId)) {
        try { originValid = hasChatGPTOrigin(await this.api.tabs.get(factoryTab.factoryTabId)); }
        catch { /* Closed tabs have no current origin. */ }
      }
      try { await this.registered(); }
      catch (error) { errorCode = safeCode(error); }
    }
    return {configured: Boolean(factoryTab),
      factoryTabId: Number.isSafeInteger(factoryTab?.factoryTabId) ? factoryTab.factoryTabId : null,
      originValid, reserved: Boolean(activeReservation), errorCode};
  }
  async register(tabId) {
    if (!Number.isSafeInteger(tabId) || tabId < 0) fail("CHATGPT_FACTORY_TAB_INVALID");
    await this.tab(tabId);
    // Never persist a conversation URL as identity. Session identity prevents ID reuse on restart.
    await this.api.storage.local.set({factoryTab: {factoryTabId: tabId, browserInstanceId: this.instanceId}});
  }
  async clear() { await this.api.storage.local.remove("factoryTab"); }
}

export async function optionsMessage(controller, message, sender, optionsUrl) {
  // This channel is private to the extension's Options page, not a bridge/UI command.
  if (sender?.id !== controller.api.runtime.id || sender.url !== optionsUrl ||
      message?.type !== "FACTORY_TAB_OPTIONS") fail("CHATGPT_PROTOCOL_INVALID");
  const keys = ["type", "action"];
  if (message.action === "REGISTER") keys.push("factoryTabId");
  if (!exactKeys(message, keys)) fail("CHATGPT_PROTOCOL_INVALID");
  if (message.action === "LIST") return {tabs: await controller.registry.list()};
  if (message.action === "STATUS") return await controller.registry.status();
  if (!["REGISTER", "CLEAR"].includes(message.action)) fail("CHATGPT_PROTOCOL_INVALID");
  return await controller.configureFactoryTab(message.action, message.factoryTabId);
}
