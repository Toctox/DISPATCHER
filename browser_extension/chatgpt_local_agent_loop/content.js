(() => {
  let armed = false;
  let busy = false;
  const seen = new Set();
  let scanTimer = null;

  const assistantRoots = () => [
    ...document.querySelectorAll('[data-message-author-role="assistant"]'),
    ...document.querySelectorAll('article[data-turn="assistant"]')
  ];

  function blockTexts() {
    const out = [];
    for (const root of assistantRoots()) {
      const nodes = root.querySelectorAll("pre code, pre");
      for (const n of nodes) {
        const text = String(n.textContent || "").replace(/\r\n?/g,"\n").trim();
        if (text.startsWith("LOCAL_AGENT_V1")) out.push(text);
      }
    }
    return [...new Set(out)];
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
    const selectors = [
      'textarea[data-testid="prompt-textarea"]',
      'textarea#prompt-textarea',
      '[data-testid="prompt-textarea"][contenteditable]',
      '#prompt-textarea[contenteditable]'
    ];
    for (const s of selectors) {
      for (const el of document.querySelectorAll(s)) {
        if (el && el.isConnected && !el.disabled && getComputedStyle(el).display !== "none") return el;
      }
    }
    return null;
  }

  function composerText(el) {
    if (!el) return "";
    return el.tagName === "TEXTAREA" ? el.value : (el.innerText || el.textContent || "");
  }

  function insert(el, text) {
    el.focus();
    if (el.tagName === "TEXTAREA") {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value").set;
      setter.call(el, text);
    } else {
      const selection = document.getSelection();
      const range = document.createRange();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
      if (!document.execCommand("insertText", false, text)) throw new Error("COMPOSER_INSERT_FAILED");
    }
    el.dispatchEvent(new InputEvent("input",{bubbles:true,inputType:"insertText",data:text}));
    el.dispatchEvent(new Event("change",{bubbles:true}));
  }

  const sleep = ms => new Promise(r => setTimeout(r,ms));

  async function waitComposer() {
    const deadline = Date.now()+30000;
    while (Date.now()<deadline) {
      const el = composer();
      const stop = document.querySelector('button[data-testid="stop-button"], button[aria-label*="Stop"], button[aria-label*="Parar"]');
      if (el && !stop && composerText(el).trim()==="") return el;
      await sleep(250);
    }
    throw new Error("COMPOSER_NOT_READY");
  }

  async function submit(text) {
    const el = await waitComposer();
    insert(el,text);
    await sleep(150);
    const sendSelectors = [
      'button[data-testid="send-button"]',
      'button#composer-submit-button',
      'button[aria-label="Send prompt"]',
      'button[aria-label="Enviar prompt"]'
    ];
    for (const s of sendSelectors) {
      const b = document.querySelector(s);
      if (b && !b.disabled && b.getAttribute("aria-disabled")!=="true") {
        b.click();
        return;
      }
    }
    el.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",code:"Enter",bubbles:true,cancelable:true}));
    el.dispatchEvent(new KeyboardEvent("keyup",{key:"Enter",code:"Enter",bubbles:true,cancelable:true}));
  }

  function resultMessage(request, response) {
    let payload = {
      protocol:"LOCAL_AGENT_RESULT_V1",
      requestId:request.id,
      response
    };
    let raw = JSON.stringify(payload);
    if (raw.length > 28000) {
      payload = {
        protocol:"LOCAL_AGENT_RESULT_V1",
        requestId:request.id,
        response:{status:"ERROR",error:"RESULT_TOO_LARGE_AFTER_TRUNCATION"}
      };
      raw = JSON.stringify(payload);
    }
    return `LOCAL_AGENT_RESULT_V1\n${raw}\nEND_LOCAL_AGENT_RESULT_V1`;
  }

  async function processRequest(req, textKey) {
    busy = true;
    seen.add(textKey);
    try {
      const response = await chrome.runtime.sendMessage({type:"EXECUTE",request:req});
      await submit(resultMessage(req,response));
    } catch (e) {
      await submit(resultMessage(req,{status:"ERROR",error:String(e?.message || e)}));
    } finally {
      busy = false;
      scheduleScan();
    }
  }

  async function scan() {
    scanTimer = null;
    if (!armed || busy) return;
    for (const text of blockTexts()) {
      if (seen.has(text)) continue;
      let req;
      try { req = parseBlock(text); }
      catch (_) { continue; } // streaming/incomplete block: retry on next mutation
      if (!req) continue;
      await processRequest(req,text);
      return;
    }
  }

  function scheduleScan() {
    if (scanTimer) clearTimeout(scanTimer);
    scanTimer = setTimeout(scan,500);
  }

  chrome.runtime.onMessage.addListener((m,_sender,sendResponse) => {
    if (m?.type === "ARM_NOW") {
      // Ignore every command already rendered before explicit arming.
      for (const text of blockTexts()) seen.add(text);
      armed = true;
      scheduleScan();
      sendResponse({ok:true,ignoredExisting:seen.size});
      return;
    }
    if (m?.type === "DISARM") {
      armed = false;
      sendResponse({ok:true});
      return;
    }
  });

  new MutationObserver(scheduleScan).observe(document.documentElement,{subtree:true,childList:true,characterData:true});
})();
