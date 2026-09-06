import {Controller} from "./controller.js";
import {optionsMessage} from "./factory_tab.js";
import {CAPABILITY, VERSION, exactKeys, fail, proof, safeCode} from "./protocol.js";

let socket = null;
let connecting = false;
let connected = false;
const initialize = (async () => {
  // Pairing credentials must never be available to content scripts.
  await chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
  await chrome.storage.local.set({bridgeStatus: "DISCONNECTED"});
  const saved = await chrome.storage.session.get("browserInstanceId");
  const id = saved.browserInstanceId || crypto.randomUUID();
  await chrome.storage.session.set({browserInstanceId: id});
  return new Controller(chrome, id);
})();

async function connect() {
  if (socket || connecting) return;
  connecting = true;
  try {
    const controller = await initialize;
    const {pairing} = await chrome.storage.local.get("pairing");
    if (!pairing?.enabled || !/^[0-9a-f]{64}$/.test(pairing.secret) ||
        !Number.isInteger(pairing.port) || pairing.port < 1 || pairing.port > 65535) return;
    const current = new WebSocket(`ws://127.0.0.1:${pairing.port}/extension`);
    socket = current;
    let phase = "CHALLENGE";
    let nonce;
    let heartbeat = null;
    let lastPong = Date.now();
    const authDeadline = setTimeout(() => current.close(), 10000);
    current.onmessage = async event => {
      try {
        if (typeof event.data !== "string" || new TextEncoder().encode(event.data).length > 524288) fail();
        const value = JSON.parse(event.data);
        if (value.protocolVersion !== VERSION) fail("CHATGPT_EXTENSION_VERSION_MISMATCH");
        if (phase === "CHALLENGE") {
          if (!exactKeys(value, ["protocolVersion", "type", "nonce", "extensionId"]) ||
              value.type !== "CHALLENGE" || value.extensionId !== chrome.runtime.id) fail();
          nonce = value.nonce;
          phase = "AUTH";
          const signature = await proof(pairing.secret, "extension", nonce, chrome.runtime.id);
          current.send(JSON.stringify({protocolVersion: VERSION, type: "AUTH", proof: signature,
            capabilities: [CAPABILITY]}));
        } else if (phase === "AUTH") {
          if (!exactKeys(value, ["protocolVersion", "type", "proof"]) || value.type !== "READY" ||
              value.proof !== await proof(pairing.secret, "server-extension", nonce, chrome.runtime.id)) fail();
          phase = "READY";
          connected = true;
          clearTimeout(authDeadline);
          await chrome.storage.local.set({bridgeStatus: "CONNECTED"});
          heartbeat = setInterval(() => {
            if (Date.now() - lastPong > 45000) { current.close(); return; }
            if (current.readyState === WebSocket.OPEN) {
              current.send(JSON.stringify({protocolVersion: VERSION, type: "PING"}));
            }
          }, 20000);
        } else if (value.type === "PONG" && exactKeys(value, ["protocolVersion", "type"])) {
          lastPong = Date.now();
        } else {
          const response = await controller.reply(value);
          if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify(response));
        }
      } catch { current.close(1008, "CHATGPT_PROTOCOL_INVALID"); }
    };
    current.onerror = () => current.close();
    current.onclose = () => {
      clearTimeout(authDeadline);
      if (heartbeat) clearInterval(heartbeat);
      if (socket === current) { socket = null; connected = false; }
      // Reservation is intentionally not removed here.
      void chrome.storage.local.set({bridgeStatus: "DISCONNECTED"});
    };
  } finally { connecting = false; }
}

function reconnect() { void connect().catch(() => {}); }
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("options.html")) return false;
  initialize.then(controller => optionsMessage(controller, message, sender,
    chrome.runtime.getURL("options.html"))).then(value => sendResponse({status: "OK", ...value,
      bridgeStatus: connected ? "CONNECTED" : "DISCONNECTED"}),
    error => sendResponse({status: "ERROR", errorCode: safeCode(error)}));
  return true;
});
chrome.runtime.onInstalled.addListener(() => {
  void chrome.alarms.create("bridge-reconnect", {periodInMinutes: 0.5});
  reconnect();
});
chrome.runtime.onStartup.addListener(() => {
  void chrome.alarms.create("bridge-reconnect", {periodInMinutes: 0.5});
  reconnect();
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === "bridge-reconnect") reconnect(); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.pairing) {
    if (socket) socket.close();
    else reconnect();
  }
});
reconnect();
