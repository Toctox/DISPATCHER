const HOST = "com.toctox.chatops_codex";
let nativePort = null;

function connectNative() {
  if (nativePort) return nativePort;
  nativePort = chrome.runtime.connectNative(HOST);

  nativePort.onMessage.addListener(async (message) => {
    const tabs = await chrome.tabs.query({
      url: ["https://chatgpt.com/*", "https://chat.openai.com/*"]
    });
    for (const tab of tabs) {
      if (!tab.id) continue;
      chrome.tabs.sendMessage(tab.id, {
        type: "chatops.native.message",
        payload: message
      }).catch(() => {});
    }
  });

  nativePort.onDisconnect.addListener(() => {
    const detail = chrome.runtime.lastError?.message || "Native host disconnected";
    nativePort = null;
    chrome.tabs.query({
      url: ["https://chatgpt.com/*", "https://chat.openai.com/*"]
    }).then((tabs) => {
      for (const tab of tabs) {
        if (!tab.id) continue;
        chrome.tabs.sendMessage(tab.id, {
          type: "chatops.native.error",
          error: detail
        }).catch(() => {});
      }
    });
  });

  return nativePort;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "chatops.native.request") return;

  try {
    const port = connectNative();
    port.postMessage(message.payload);
    sendResponse({ ok: true });
  } catch (error) {
    sendResponse({ ok: false, error: String(error) });
  }
  return true;
});
