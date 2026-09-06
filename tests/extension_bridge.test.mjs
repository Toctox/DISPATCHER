import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import {createHmac} from "node:crypto";
import {Controller} from "../browser_extension/project_factory_bridge/controller.js";
import {optionsMessage} from "../browser_extension/project_factory_bridge/factory_tab.js";
import {ACTIONS, CAPABILITY, CONTROL, RECOVERY_RELEASE, proof, validateCommand} from "../browser_extension/project_factory_bridge/protocol.js";

const extensionId = "a".repeat(32);
const binding = {extensionId, browserInstanceId: "browser-test", tabId: 7, documentId: "document-test"};
function rpc(action = "PRECHECK", fields = {}) {
  const value = {protocolVersion: 1, type: "COMMAND", requestId: crypto.randomUUID(), action};
  if ([CONTROL, RECOVERY_RELEASE].includes(action)) { value.type = "CONTROL"; value.control = action; delete value.action; }
  if (action !== "PRECHECK") Object.assign(value, {binding, reservationId: "reservation-1"});
  if (action === "INSERT_BOOTSTRAP") value.bootstrap = "bootstrap\nexato ç";
  if (action === "SEND") value.expiresAt = Date.now() + 10000;
  return {...value, ...fields};
}
function harness() {
  const data = {factoryTab: {factoryTabId: 7, browserInstanceId: "browser-test"}};
  const h = {tabs: [{id: 7, windowId: 1, index: 0, url: "https://chatgpt.com/c/test"}],
    actions: [], errors: {}, data, tabReads: [], queries: [], messages: [], documentId: "document-test"};
  const api = {runtime: {id: extensionId}, storage: {local: {
    async get(key) { return {[key]: structuredClone(data[key])}; },
    async set(values) { Object.assign(data, structuredClone(values)); },
    async remove(key) { delete data[key]; }
  }}, tabs: {
    async query(options) { assert.deepEqual(options, {url: "https://chatgpt.com/*"}); h.queries.push(options); return h.tabs; },
    async get(tabId) {
      h.tabReads.push(tabId);
      const tab = h.tabs.find(tab => tab.id === tabId);
      if (!tab) throw new Error("closed");
      return tab;
    },
    async sendMessage(tabId, value, options) {
      assert.equal(tabId, h.data.factoryTab.factoryTabId);
      assert.deepEqual(options, {frameId: 0});
      h.actions.push(value);
      h.messages.push({tabId, value});
      if (value.action === "SEND") assert.equal(data.activeReservation.phase, "SEND_ATTEMPTED");
      if (h.errors[value.action]) return {status: "ERROR", errorCode: h.errors[value.action]};
      if (h.contentError) throw new Error("unavailable");
      return value.action === "PRECHECK" ? {status: "OK", documentId: h.documentId,
        ...h.legacyContent ? {} : {capability: CAPABILITY}} : {status: "OK"};
    },
    create() { assert.fail("No new tabs"); }, update() { assert.fail("No navigation"); },
    remove() { assert.fail("Do not close tabs"); }
  }};
  h.controller = new Controller(api, "browser-test");
  h.restart = () => new Controller(api, "browser-test");
  return h;
}

test("closed factory tab blocked without falling back", async () => {
  const h = harness(); h.tabs = [];
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_NOT_FOUND");
});
test("multiple ChatGPT tabs are allowed; only registered factory tab is inspected", async () => {
  const h = harness(); h.tabs.push({id: 8, url: "https://chatgpt.com/"});
  assert.equal((await h.controller.reply(rpc())).status, "OK");
  assert.deepEqual(h.tabReads, [7, 7]);
  assert.equal(h.queries.length, 0);
});
test("registered factory tab precheck returns pinned identity", async () => {
  const h = harness(); assert.deepEqual((await h.controller.reply(rpc())).binding, binding);
  assert.equal(h.data.activeReservation, undefined);
});
for (const code of ["CHATGPT_AUTH_REQUIRED", "CHATGPT_SELECTOR_UNAVAILABLE"]) {
  test(`${code} remains blocked`, async () => {
    const h = harness(); h.errors.PRECHECK = code;
    assert.equal((await h.controller.reply(rpc())).errorCode, code);
    assert.equal(h.data.activeReservation, undefined);
  });
}
for (const field of ["documentId", "browserInstanceId", "tabId"]) {
  test(`changed ${field} rejected`, async () => {
    const h = harness();
    const changed = {...binding, [field]: field === "tabId" ? 8 : "changed"};
    assert.equal((await h.controller.reply(rpc("PRECHECK", {binding: changed}))).errorCode, "CHATGPT_BINDING_CHANGED");
    assert.equal(h.data.activeReservation, undefined);
  });
}
test("new chat, exact bootstrap, one send, persistent exclusion, receipt release without DOM", async () => {
  const h = harness();
  for (const action of ["NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]) {
    assert.equal((await h.controller.reply(rpc(action))).status, "OK");
  }
  assert.equal(h.actions.find(a => a.action === "INSERT_BOOTSTRAP").bootstrap, "bootstrap\nexato ç");
  assert.equal(h.actions.filter(a => a.action === "SEND").length, 1);
  assert.equal(h.data.activeReservation.phase, "SEND_ATTEMPTED");
  const restarted = h.restart();
  assert.equal((await restarted.reply(rpc("SEND"))).status, "ERROR");
  assert.equal((await restarted.reply(rpc())).errorCode, "CHATGPT_TAB_BUSY");
  assert.equal((await restarted.reply(rpc("NEW_CHAT", {reservationId: "second"}))).errorCode, "CHATGPT_TAB_BUSY");
  const before = h.actions.length;
  assert.equal((await restarted.reply(rpc(CONTROL))).status, "OK");
  assert.equal(h.actions.length, before);
  assert.equal(h.data.activeReservation, undefined);
});
test("ambiguous SEND preserves reservation across worker restart", async () => {
  const h = harness();
  await h.controller.execute(rpc("NEW_CHAT"));
  await h.controller.execute(rpc("INSERT_BOOTSTRAP"));
  h.errors.SEND = "CHATGPT_SEND_UNCERTAIN";
  assert.equal((await h.controller.reply(rpc("SEND"))).errorCode, "CHATGPT_SEND_UNCERTAIN");
  assert.equal((await h.restart().reply(rpc("SEND"))).status, "ERROR");
  assert.equal(h.actions.filter(a => a.action === "SEND").length, 1);
});
test("expired SEND never reaches page and remains reserved", async () => {
  const h = harness();
  await h.controller.execute(rpc("NEW_CHAT"));
  await h.controller.execute(rpc("INSERT_BOOTSTRAP"));
  assert.equal((await h.controller.reply(rpc("SEND", {expiresAt: 1}))).errorCode, "CHATGPT_SEND_UNCERTAIN");
  assert.equal(h.actions.filter(a => a.action === "SEND").length, 0);
  assert.equal(h.data.activeReservation.phase, "SEND_ATTEMPTED");
});
test("release cannot clear a different reservation or unsent attempt", async () => {
  const h = harness();
  await h.controller.execute(rpc("NEW_CHAT"));
  assert.equal((await h.controller.reply(rpc(CONTROL))).status, "ERROR");
  assert.ok(h.data.activeReservation);
});
test("explicit PRE-SEND recovery preserves pairing and Factory Tab and never contacts tabs", async () => {
  const h = harness();
  h.data.activeReservation = {reservationId: "reservation-1", binding, phase: "NEW_CHAT_ATTEMPTED"};
  h.data.pairing = {extensionSecret: "private-extension-secret"};
  h.data.controlSecret = "private-control-secret";
  // Recovery compares the old reservation, even if its tab/session no longer exists.
  h.tabs = [];
  h.data.factoryTab = {factoryTabId: 99, browserInstanceId: "new-session"};
  const expected = structuredClone(h.data); delete expected.activeReservation;
  assert.equal((await h.restart().reply(rpc(RECOVERY_RELEASE))).status, "OK");
  assert.deepEqual(h.data, expected);
  assert.deepEqual(h.actions, []); assert.deepEqual(h.tabReads, []); assert.deepEqual(h.queries, []);
  assert.equal((await h.controller.reply(rpc(RECOVERY_RELEASE))).errorCode, "CHATGPT_RESERVATION_INVALID");
});
for (const phase of ["NEW_CHAT", "INSERT_BOOTSTRAP_ATTEMPTED", "INSERT_BOOTSTRAP", "SEND_ATTEMPTED", "UNKNOWN"]) {
  test(`PRE-SEND recovery refuses phase ${phase}`, async () => {
    const h = harness();
    h.data.activeReservation = {reservationId: "reservation-1", binding, phase};
    const previous = structuredClone(h.data);
    assert.equal((await h.controller.reply(rpc(RECOVERY_RELEASE))).errorCode, "CHATGPT_RESERVATION_INVALID");
    assert.deepEqual(h.data, previous); assert.deepEqual(h.actions, []);
  });
}
for (const changed of [
  {reservationId: "other"}, {binding: {...binding, documentId: "other"}},
  {binding: {...binding, browserInstanceId: "other"}}, {binding: {...binding, tabId: 8}},
  {sendAttempted: true}, {extra: "unknown"}
]) {
  test(`PRE-SEND recovery refuses divergent reservation ${JSON.stringify(changed)}`, async () => {
    const h = harness();
    h.data.activeReservation = {reservationId: "reservation-1", binding, phase: "NEW_CHAT_ATTEMPTED", ...changed};
    const previous = structuredClone(h.data);
    assert.equal((await h.controller.reply(rpc(RECOVERY_RELEASE))).errorCode, "CHATGPT_RESERVATION_INVALID");
    assert.deepEqual(h.data, previous); assert.deepEqual(h.actions, []);
  });
}
test("PRE-SEND recovery refuses busy controller and never becomes a UI action", async () => {
  const h = harness();
  h.data.activeReservation = {reservationId: "reservation-1", binding, phase: "NEW_CHAT_ATTEMPTED"};
  h.controller.busy = true;
  assert.equal((await h.controller.reply(rpc(RECOVERY_RELEASE))).errorCode, "CHATGPT_TAB_BUSY");
  assert.ok(h.data.activeReservation);
  assert.throws(() => validateCommand({...rpc(RECOVERY_RELEASE), type: "COMMAND", action: RECOVERY_RELEASE}), /PROTOCOL_INVALID/);
  assert.throws(() => validateCommand({...rpc(RECOVERY_RELEASE), force: true}), /PROTOCOL_INVALID/);
  assert.deepEqual(ACTIONS, ["PRECHECK", "NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]);
});
test("parallel operation rejected", async () => {
  const h = harness();
  const first = h.controller.execute(rpc("NEW_CHAT"));
  await assert.rejects(h.controller.execute(rpc("NEW_CHAT")), /TAB_BUSY/);
  await first;
});
for (const action of ["GET_TEXT", "GET_HTML", "EVALUATE_JS", "EXECUTE_SCRIPT", "CLICK_SELECTOR", "CREATE_TAB"]) {
  test(`no generic ${action}`, () => assert.throws(() => validateCommand(rpc(action)), /PROTOCOL_INVALID/));
}
test("protocol rejects extra fields and incompatible version", () => {
  assert.deepEqual(ACTIONS, ["PRECHECK", "NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]);
  assert.throws(() => validateCommand(rpc("PRECHECK", {selector: "body"})), /PROTOCOL_INVALID/);
  assert.throws(() => validateCommand(rpc("PRECHECK", {protocolVersion: 2})), /VERSION_MISMATCH/);
  assert.throws(() => validateCommand(rpc("INSERT_BOOTSTRAP", {bootstrap: "x".repeat(65537)})), /BOOTSTRAP_MISMATCH/);
  assert.throws(() => validateCommand({...rpc(CONTROL), type: "COMMAND", action: CONTROL}), /PROTOCOL_INVALID/);
});
test("HMAC agrees with standard crypto and is role-bound", async () => {
  const secret = "ab".repeat(32), nonce = "cd".repeat(32);
  const payload = `PROJECT_FACTORY_BRIDGE_V1\nextension\n${nonce}\n${extensionId}`;
  const signature = await proof(secret, "extension", nonce, extensionId);
  assert.equal(signature, createHmac("sha256", Buffer.from(secret, "hex")).update(payload).digest("hex"));
  assert.notEqual(signature, await proof(secret, "worker", nonce, extensionId));
  assert.notEqual(signature, await proof(secret, "server-extension", nonce, extensionId));
});

function domHarness({editable = false, navigationAvailable = true} = {}) {
  const h = {listener: null, clicks: [], events: [], clock: 1000, throwOnSend: false,
    matches: new Map(), queries: [], navigationListeners: {}, lifecycleListeners: {}};
  class FakeElement {
    constructor() {
      this.isConnected = true;
      this.hidden = false;
      this.disabled = false;
      this.inert = false;
      this.attributes = {};
      this.parentElement = null;
      this.style = {display: "flex", visibility: "visible", pointerEvents: "auto", opacity: "1"};
      this.rect = {width: 40, height: 40};
    }
    getAttribute(name) { return this.attributes[name] ?? null; }
    closest(selector) {
      assert.equal(selector, '[hidden],[aria-hidden="true"],[inert]');
      for (let element = this; element; element = element.parentElement) {
        if (element.hidden || "hidden" in element.attributes || element.inert ||
            "inert" in element.attributes || element.getAttribute("aria-hidden") === "true") return element;
      }
      return null;
    }
    getBoundingClientRect() { return this.rect; }
    getClientRects() { return [this.rect]; }
    get offsetParent() { assert.fail("offsetParent must not determine usability"); }
  }
  class FakeTextarea extends FakeElement {
    constructor() { super(); this.tagName = "TEXTAREA"; this.current = ""; this.ownerDocument = doc; }
    get value() { return this.current; }
    set value(value) { this.current = value; }
    getAttribute(name) { return name === "contenteditable" && editable ? "true" : super.getAttribute(name); }
    focus() {}
    dispatchEvent(event) { h.events.push(event.type); }
  }
  const doc = {
    querySelectorAll(selector) {
      h.queries.push(selector);
      for (const [key, selectors] of Object.entries(h.context.PFSelectors)) {
        if (selectors.includes(selector)) {
          if (h.matches.has(selector)) return h.matches.get(selector);
          const found = h.controls[key];
          return !found ? [] : Array.isArray(found) ? found : [found];
        }
      }
      assert.fail(`Unapproved selector: ${selector}`);
    },
    get body() { assert.fail("No body or conversation scraping"); },
    getSelection() { return {removeAllRanges() {}, addRange() {}}; },
    createRange() { return {selectNodeContents(element) { assert.equal(element, h.composer); }}; },
    execCommand(action, ui, value) {
      assert.equal(action, "insertText"); assert.equal(ui, false);
      h.composer.current = value; return true;
    }
  };
  h.composer = new FakeTextarea();
  if (editable) {
    h.composer.tagName = "DIV";
    Object.defineProperty(h.composer, "innerText", {get() { return this.current; }});
  }
  function control(kind) {
    const element = new FakeElement();
    for (const property of ["innerText", "textContent", "innerHTML", "outerHTML"]) {
      Object.defineProperty(element, property, {get() { assert.fail("Only the composer may be read"); }});
    }
    element.click = () => {
      h.clicks.push(kind);
      if (kind === "newChat") { h.composer.current = ""; h.context.location.pathname = "/"; }
      if (kind === "send" && h.throwOnSend) throw new Error("ambiguous click");
    };
    return element;
  }
  h.createElement = () => new FakeElement();
  h.createControl = control;
  h.controls = {account: control("account"), newChat: control("newChat"), composer: h.composer, send: control("send")};
  const context = {crypto, TextEncoder, URL, document: doc, HTMLTextAreaElement: FakeTextarea,
    navigation: navigationAvailable ? {addEventListener(name, listener) { h.navigationListeners[name] = listener; }} : undefined,
    addEventListener(name, listener) { h.lifecycleListeners[name] = listener; },
    getComputedStyle: element => element.style,
    InputEvent: class {constructor(type, options) { this.type = type; Object.assign(this, options); }},
    Event: class {constructor(type) { this.type = type; }},
    location: {origin: "https://chatgpt.com", pathname: "/c/test"},
    Date: {now: () => h.clock}, setTimeout: (callback, ms) => {h.clock += ms; callback();},
    chrome: {runtime: {id: extensionId, onMessage: {addListener(listener) { h.listener = listener; }}}}};
  h.context = vm.createContext(context, {codeGeneration: {strings: false, wasm: false}});
  for (const file of ["selectors.js", "composer.js", "content.js"]) {
    vm.runInContext(fs.readFileSync(new URL(`../browser_extension/project_factory_bridge/${file}`, import.meta.url), "utf8"), h.context);
  }
  h.message = (value, sender = {id: extensionId}) => new Promise(resolve => {
    if (!h.listener(value, sender, resolve)) resolve(null);
  });
  h.navigate = (url, overrides = {}) => {
    h.navigationListeners.navigate({destination: {url, sameDocument: true}, navigationType: "push", ...overrides});
    h.context.location.origin = new URL(url).origin;
    h.context.location.pathname = new URL(url).pathname;
  };
  h.prepare = async bootstrap => {
    const precheck = await h.message({action: "PRECHECK"});
    const fields = {documentId: precheck.documentId, reservationId: "reservation-1"};
    assert.equal((await h.message({action: "NEW_CHAT", ...fields})).status, "OK");
    assert.equal((await h.message({action: "INSERT_BOOTSTRAP", bootstrap, ...fields})).status, "OK");
    return fields;
  };
  return h;
}
for (const editable of [false, true]) {
  test(`real content adapter ${editable ? "contenteditable" : "textarea"}: exact input, React events, one click`, async () => {
    const h = domHarness({editable});
    const bootstrap = "Linha ç\n\n  literal <b>texto</b> 😀";
    const fields = await h.prepare(bootstrap);
    assert.equal(h.composer.current, bootstrap);
    assert.deepEqual(h.events, ["input", "change"]);
    assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).status, "OK");
    assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).status, "ERROR");
    assert.deepEqual(h.clicks, ["newChat", "send"]);
  });
}
test("content authentication and missing selector fail closed", async () => {
  const h = domHarness();
  const account = h.controls.account; delete h.controls.account;
  assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_AUTH_REQUIRED");
  h.controls.account = account; delete h.controls.newChat;
  assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_SELECTOR_UNAVAILABLE");
});
test("composer change before SEND refuses click", async () => {
  const h = domHarness(); const fields = await h.prepare("exact"); h.composer.current = "modified";
  assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).errorCode, "CHATGPT_BOOTSTRAP_MISMATCH");
  assert.deepEqual(h.clicks, ["newChat"]);
});
test("content deadline refuses late SEND", async () => {
  const h = domHarness(); const fields = await h.prepare("exact");
  assert.equal((await h.message({action: "SEND", expiresAt: 1, ...fields})).errorCode, "CHATGPT_SEND_UNCERTAIN");
  assert.deepEqual(h.clicks, ["newChat"]);
});
test("uncertain click is never retried by content", async () => {
  const h = domHarness(); const fields = await h.prepare("exact"); h.throwOnSend = true;
  assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).status, "ERROR");
  assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).status, "ERROR");
  assert.deepEqual(h.clicks, ["newChat", "send"]);
});
test("content rejects page/foreign senders and unknown actions", async () => {
  const h = domHarness();
  assert.equal(await h.message({action: "PRECHECK"}, {id: "b".repeat(32)}), null);
  assert.equal(await h.message({action: "PRECHECK"}, {id: extensionId, tab: {id: 1}}), null);
  assert.equal((await h.message({action: "GET_TEXT"})).errorCode, "CHATGPT_PROTOCOL_INVALID");
});
test("document reload identity prevents sending into a new page", async () => {
  const h = domHarness(); const fields = await h.prepare("exact");
  assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields, documentId: "replacement"})).errorCode, "CHATGPT_BINDING_CHANGED");
  assert.deepEqual(h.clicks, ["newChat"]);
});
test("empty contenteditable placeholder BR is not bootstrap text", () => {
  const h = domHarness({editable: true});
  h.composer.current = "\n"; h.composer.textContent = "";
  assert.equal(h.context.PFComposer.read(h.composer), "");
  h.composer.textContent = "\n";
  assert.equal(h.context.PFComposer.read(h.composer), "\n");
});
test("NEW_CHAT waits for the UI control to clear the existing composer, never sends stale text", async () => {
  const h = domHarness();
  const precheck = await h.message({action: "PRECHECK"});
  h.composer.current = "old unsent text";
  h.controls.newChat.click = () => { h.clicks.push("newChat"); h.context.location.pathname = "/"; };
  const response = await h.message({action: "NEW_CHAT", documentId: precheck.documentId,
    reservationId: "reservation-1"});
  assert.equal(response.errorCode, "CHATGPT_NEW_CHAT_FAILED");
  assert.deepEqual(h.clicks, ["newChat"]);
});

for (const kind of ["account", "newChat"]) {
  test(`confirmed responsive ${kind} clones: two raw matches, one usable`, async () => {
    const h = domHarness();
    const live = h.controls[kind];
    live.rect = kind === "account" ? {width: 248, height: 52} : {width: 233, height: 36};
    const clone = h.createControl(kind);
    clone.style.pointerEvents = "none";
    clone.rect = kind === "account" ? {width: 40, height: 40} : {width: 36, height: 36};
    clone.parentElement = h.createElement();
    clone.parentElement.hidden = true;
    clone.click = () => assert.fail("Hidden responsive clone must not be clicked");
    h.controls[kind] = [clone, live];
    // Real incident: localized ARIA, with the stable data-testid still present.
    for (const selector of h.context.PFSelectors[kind].slice(1)) h.matches.set(selector, []);
    if (kind === "account") live.attributes["aria-label"] = "Abrir menu do perfil";
    assert.equal(h.context.PFSelection.rawMatches(kind).length, 2);
    const candidates = h.context.PFSelection.candidates(kind);
    assert.equal(candidates.length, 1);
    assert.equal(candidates[0], live);
    const precheck = await h.message({action: "PRECHECK"});
    assert.equal(precheck.status, "OK");
    assert.equal((await h.message({action: "NEW_CHAT", documentId: precheck.documentId,
      reservationId: "reservation-1"})).status, "OK");
    assert.deepEqual(h.clicks, ["newChat"]);
  });
}

const unusableCases = [
  ["disconnected", element => { element.isConnected = false; }],
  ["hidden", element => { element.hidden = true; }],
  ["disabled", element => { element.disabled = true; }],
  ["aria-disabled=true", element => { element.attributes["aria-disabled"] = "true"; }],
  ["pointer-events:none", element => { element.style.pointerEvents = "none"; }],
  ["display:none", element => { element.style.display = "none"; }],
  ["visibility:hidden", element => { element.style.visibility = "hidden"; }],
  ["visibility:collapse", element => { element.style.visibility = "collapse"; }],
  ["zero width", element => { element.rect.width = 0; }],
  ["zero height", element => { element.rect.height = 0; }],
  ["negative width", element => { element.rect.width = -1; }],
  ["negative height", element => { element.rect.height = -1; }],
  ["self aria-hidden", element => { element.attributes["aria-hidden"] = "true"; }],
  ["self inert", element => { element.inert = true; }],
  ["hidden ancestor", (element, h) => {
    element.parentElement = h.createElement(); element.parentElement.hidden = true;
  }],
  ["aria-hidden ancestor", (element, h) => {
    element.parentElement = h.createElement(); element.parentElement.attributes["aria-hidden"] = "true";
  }],
  ["inert ancestor", (element, h) => {
    element.parentElement = h.createElement(); element.parentElement.inert = true;
  }]
];
for (const [name, makeUnusable] of unusableCases) {
  test(`isUsable excludes ${name} from every selector category`, () => {
    const h = domHarness();
    const element = h.createElement();
    makeUnusable(element, h);
    assert.equal(h.context.PFSelection.isUsable(element), false);
    for (const kind of Object.keys(h.context.PFSelectors)) {
      h.controls[kind] = element;
      assert.equal(h.context.PFSelection.rawMatches(kind).length, 1);
      assert.equal(h.context.PFSelection.candidates(kind).length, 0);
    }
  });
}
test("null is unusable; a fixed-position element with no offsetParent is usable", () => {
  const h = domHarness();
  assert.equal(h.context.PFSelection.isUsable(null), false);
  const element = h.createElement();
  element.style.position = "fixed";
  Object.defineProperty(element, "offsetParent", {value: null});
  assert.equal(h.context.PFSelection.isUsable(element), true);
});
test("selectors matching the same element are deduplicated before uniqueness checks", async () => {
  const h = domHarness();
  for (const kind of ["account", "newChat", "composer", "send"]) {
    const element = h.controls[kind];
    for (const selector of h.context.PFSelectors[kind]) h.matches.set(selector, [element]);
    assert.equal(h.context.PFSelection.rawMatches(kind).length, 1);
    assert.equal(h.context.PFSelection.candidates(kind).length, 1);
  }
  const fields = await h.prepare("exact");
  assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).status, "OK");
  assert.deepEqual(h.clicks, ["newChat", "send"]);
});
test("multiple usable account indicators remain authentication evidence", async () => {
  const h = domHarness();
  h.controls.account = [h.controls.account, h.createControl("account")];
  assert.equal((await h.message({action: "PRECHECK"})).status, "OK");
  assert.equal(h.context.PFSelection.candidates("account").length, 2);
});
test("zero usable accounts requires auth even when raw matches exist", async () => {
  const h = domHarness();
  h.controls.account.style.pointerEvents = "none";
  assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_AUTH_REQUIRED");
});
test("usable login requires auth even with account evidence", async () => {
  const h = domHarness(); h.controls.login = h.createControl("login");
  assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_AUTH_REQUIRED");
});
test("hidden login clone is not evidence of unauthenticated state", async () => {
  const h = domHarness(); h.controls.login = h.createControl("login");
  h.controls.login.parentElement = h.createElement();
  h.controls.login.parentElement.attributes["aria-hidden"] = "true";
  assert.equal((await h.message({action: "PRECHECK"})).status, "OK");
});
for (const kind of ["newChat", "composer"]) {
  test(`${kind} with multiple usable controls fails closed without a click`, async () => {
    const h = domHarness();
    h.controls[kind] = [h.controls[kind], h.createControl(kind)];
    assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_SELECTOR_UNAVAILABLE");
    assert.deepEqual(h.clicks, []);
  });
  test(`${kind} remains required when the only raw match is unusable`, async () => {
    const h = domHarness(); h.controls[kind].disabled = true;
    assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_SELECTOR_UNAVAILABLE");
  });
}
test("a missing composer blocks precheck", async () => {
  const h = domHarness(); delete h.controls.composer;
  assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_SELECTOR_UNAVAILABLE");
});
for (const mode of ["multiple usable", "zero usable"]) {
  test(`SEND with ${mode} controls fails closed without retry`, async () => {
    const h = domHarness(); const fields = await h.prepare("exact");
    const live = h.controls.send;
    if (mode === "multiple usable") h.controls.send = [live, h.createControl("send")];
    else live.style.pointerEvents = "none";
    assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).errorCode, "CHATGPT_SELECTOR_UNAVAILABLE");
    h.controls.send = live; live.style.pointerEvents = "auto";
    assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).errorCode, "CHATGPT_RESERVATION_INVALID");
    assert.deepEqual(h.clicks, ["newChat"]);
  });
}
test("SEND ignores a hidden clone and attempts the usable control exactly once", async () => {
  const h = domHarness(); const fields = await h.prepare("exact");
  const live = h.controls.send, clone = h.createControl("send");
  clone.parentElement = h.createElement(); clone.parentElement.inert = true;
  clone.click = () => assert.fail("Hidden SEND must never be clicked");
  h.controls.send = [clone, live];
  assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).status, "OK");
  assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).status, "ERROR");
  assert.deepEqual(h.clicks, ["newChat", "send"]);
});

test("no factory registration blocks even if one eligible tab exists", async () => {
  const h = harness(); delete h.data.factoryTab;
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_NOT_CONFIGURED");
  assert.equal(h.actions.length, 0);
  assert.equal(h.queries.length, 0);
});
for (const url of ["https://example.org/", "http://chatgpt.com/", "https://chatgpt.com.evil.invalid/", "https://chatgpt.com:444/", "https://name@chatgpt.com/"]) {
  test(`factory tab with invalid origin/authority ${url} is blocked`, async () => {
    const h = harness(); h.tabs[0].url = url;
    h.tabs.push({id: 8, url: "https://chatgpt.com/"});
    assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_INVALID");
    assert.equal(h.actions.length, 0);
    assert.deepEqual(h.tabReads, [7]);
  });
}
for (const field of ["pendingUrl", "status", "incognito", "discarded"]) {
  test(`factory tab ${field} state fails closed`, async () => {
    const h = harness();
    h.tabs[0][field] = {pendingUrl: "https://chatgpt.com/c/pending", status: "loading", incognito: true, discarded: true}[field];
    assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_INVALID");
    assert.equal(h.actions.length, 0);
  });
}
test("missing content script is structured and never tries the operator tab", async () => {
  const h = harness(); h.contentError = true;
  h.tabs.push({id: 8, url: "https://chatgpt.com/", active: true});
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_INVALID");
  assert.ok(h.messages.every(message => message.tabId === 7));
});
test("legacy content script in an already-open tab cannot pass Factory Tab preflight", async () => {
  const h = harness(); h.legacyContent = true;
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_EXTENSION_VERSION_MISMATCH");
  assert.ok(!h.actions.some(action => action.action === "NEW_CHAT"));
});
test("content without navigation protection cannot pass preflight", async () => {
  const h = domHarness({navigationAvailable: false});
  assert.equal((await h.message({action: "PRECHECK"})).errorCode, "CHATGPT_FACTORY_TAB_INVALID");
  assert.deepEqual(h.clicks, []);
});
test("operator tabs, order, activity and recency never change the dispatch target", async () => {
  const h = harness();
  h.tabs.unshift({id: 99, url: "https://chatgpt.com/c/operator", active: true, lastAccessed: Date.now()});
  h.tabs.push({id: 101, url: "https://chatgpt.com/c/another"});
  const before = (await h.controller.reply(rpc())).binding;
  await h.controller.execute(rpc("NEW_CHAT"));
  h.tabs[0].url = "https://chatgpt.com/c/operator-next";
  h.tabs[0].active = false; h.tabs[2].active = true;
  h.tabs.reverse();
  await h.controller.execute(rpc("INSERT_BOOTSTRAP"));
  await h.controller.execute(rpc("SEND"));
  assert.ok(h.messages.every(message => message.tabId === 7));
  assert.deepEqual(h.actions.filter(action => action.action !== "PRECHECK").map(action => action.action),
    ["NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]);
  assert.deepEqual(h.data.activeReservation.binding, before);
  assert.equal(h.queries.length, 0);
});
test("closed registered tab doesn't get replaced by an eligible operator tab", async () => {
  const h = harness(); h.tabs = [{id: 8, url: "https://chatgpt.com/", active: true}];
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_NOT_FOUND");
  assert.equal(h.data.factoryTab.factoryTabId, 7);
  assert.equal(h.messages.length, 0);
});
test("browser restart invalidates registration even if the numeric Tab ID was reused", async () => {
  const h = harness(); h.data.factoryTab.browserInstanceId = "old-browser";
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_INVALID");
  assert.equal(h.actions.length, 0);
});
test("registration changed between preflight and worker blocks the pinned binding", async () => {
  const h = harness(); h.tabs.push({id: 8, windowId: 1, index: 1, url: "https://chatgpt.com/"});
  await h.controller.execute(rpc());
  await h.controller.configureFactoryTab("REGISTER", 8);
  assert.equal((await h.controller.reply(rpc("NEW_CHAT"))).errorCode, "CHATGPT_BINDING_CHANGED");
  assert.ok(!h.actions.some(action => action.action === "NEW_CHAT"));
});
test("reload/new content identity between actions fails closed and preserves reservation", async () => {
  const h = harness(); await h.controller.execute(rpc("NEW_CHAT"));
  h.documentId = "new-document";
  assert.equal((await h.controller.reply(rpc("INSERT_BOOTSTRAP"))).errorCode, "CHATGPT_BINDING_CHANGED");
  assert.equal(h.data.activeReservation.phase, "NEW_CHAT");
  assert.ok(!h.actions.some(action => action.action === "INSERT_BOOTSTRAP"));
});

const optionsUrl = `chrome-extension://${extensionId}/options.html`;
function option(h, action, fields = {}, sender = {id: extensionId, url: optionsUrl}) {
  return optionsMessage(h.controller, {type: "FACTORY_TAB_OPTIONS", action, ...fields}, sender, optionsUrl);
}
test("Options discovery never registers automatically and returns only IDs/position", async () => {
  const h = harness(); delete h.data.factoryTab;
  h.tabs[0].title = "sensitive title";
  const response = await option(h, "LIST");
  assert.deepEqual(response, {tabs: [{factoryTabId: 7, windowId: 1, position: 1}]});
  assert.equal(h.data.factoryTab, undefined);
  assert.equal(h.messages.length, 0);
  assert.deepEqual(await option(h, "STATUS"), {configured: false, factoryTabId: null,
    originValid: false, reserved: false, errorCode: ""});
});
test("explicit registration persists only ID/session, can be replaced or cleared while idle", async () => {
  const h = harness(); delete h.data.factoryTab;
  h.tabs.push({id: 8, windowId: 1, index: 1, url: "https://chatgpt.com/c/not-canonical"});
  await option(h, "REGISTER", {factoryTabId: 8});
  assert.deepEqual(h.data.factoryTab, {factoryTabId: 8, browserInstanceId: "browser-test"});
  assert.equal((await h.restart().reply(rpc())).binding.tabId, 8);
  const status = await option(h, "STATUS");
  assert.equal(status.configured, true); assert.equal(status.originValid, true); assert.equal(status.factoryTabId, 8);
  await option(h, "REGISTER", {factoryTabId: 7});
  assert.equal(h.data.factoryTab.factoryTabId, 7);
  await option(h, "CLEAR");
  assert.equal(h.data.factoryTab, undefined);
  assert.equal(h.data.activeReservation, undefined);
});
for (const id of [undefined, null, "7", -1, 7.5, true, 555]) {
  test(`explicit registration rejects absent, invalid or missing ID ${String(id)}`, async () => {
    const h = harness();
    await assert.rejects(option(h, "REGISTER", {factoryTabId: id}), /FACTORY_TAB/);
    assert.equal(h.data.factoryTab.factoryTabId, 7);
  });
}
for (const phase of ["NEW_CHAT_ATTEMPTED", "NEW_CHAT", "INSERT_BOOTSTRAP", "SEND_ATTEMPTED", "UNKNOWN_LEGACY"]) {
  test(`cannot clear or replace factory registration with ${phase} reservation`, async () => {
    const h = harness(); h.data.activeReservation = {reservationId: "reservation-1", binding, phase};
    const original = structuredClone(h.data);
    await assert.rejects(option(h, "CLEAR"), /TAB_BUSY/);
    await assert.rejects(option(h, "REGISTER", {factoryTabId: 8}), /TAB_BUSY/);
    assert.deepEqual(h.data, original);
  });
}
test("registration and dispatch commands share exclusion even before the reservation write", async () => {
  const h = harness();
  const dispatch = h.controller.execute(rpc("NEW_CHAT"));
  await assert.rejects(option(h, "CLEAR"), /TAB_BUSY/);
  await dispatch;
  assert.equal(h.data.factoryTab.factoryTabId, 7);
});
test("receipt release preserves registration and then permits explicit clear", async () => {
  const h = harness();
  for (const action of ["NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]) await h.controller.execute(rpc(action));
  await h.controller.execute(rpc(CONTROL));
  assert.deepEqual(h.data.factoryTab, {factoryTabId: 7, browserInstanceId: "browser-test"});
  assert.equal(h.data.activeReservation, undefined);
  await option(h, "CLEAR");
  assert.equal(h.data.factoryTab, undefined);
});
for (const sender of [{id: extensionId, url: "https://chatgpt.com/"}, {id: "b".repeat(32), url: optionsUrl},
  {id: extensionId, url: optionsUrl + "?forged"}]) {
  test(`registration channel rejects non-Options sender ${sender.url}`, async () => {
    const h = harness(); await assert.rejects(option(h, "CLEAR", {}, sender), /PROTOCOL_INVALID/);
    assert.equal(h.data.factoryTab.factoryTabId, 7);
  });
}
test("registration is not a WebSocket UI action and Options rejects extra fields", async () => {
  const h = harness();
  assert.throws(() => validateCommand(rpc("REGISTER")), /PROTOCOL_INVALID/);
  await assert.rejects(option(h, "REGISTER", {factoryTabId: 7, url: "https://chatgpt.com/"}), /PROTOCOL_INVALID/);
});

test("the authorized NEW_CHAT same-document transition preserves the pinned identity", async () => {
  const h = domHarness();
  const before = await h.message({action: "PRECHECK"});
  h.controls.newChat.click = () => {
    h.clicks.push("newChat"); h.composer.current = ""; h.navigate("https://chatgpt.com/");
  };
  const fields = {documentId: before.documentId, reservationId: "reservation-1"};
  assert.equal((await h.message({action: "NEW_CHAT", ...fields})).status, "OK");
  assert.equal((await h.message({action: "INSERT_BOOTSTRAP", bootstrap: "exact", ...fields})).status, "OK");
  assert.equal((await h.message({action: "PRECHECK"})).documentId, before.documentId);
});
test("operator navigates away during NEW_CHAT: BINDING_CHANGED, no bootstrap or SEND", async () => {
  const h = domHarness(); const before = await h.message({action: "PRECHECK"});
  h.controls.newChat.click = () => {
    h.clicks.push("newChat"); h.navigate("https://chatgpt.com/");
    h.navigate("https://chatgpt.com/c/operator");
  };
  const fields = {documentId: before.documentId, reservationId: "reservation-1"};
  assert.equal((await h.message({action: "NEW_CHAT", ...fields})).errorCode, "CHATGPT_BINDING_CHANGED");
  assert.equal((await h.message({action: "INSERT_BOOTSTRAP", bootstrap: "exact", ...fields})).errorCode, "CHATGPT_BINDING_CHANGED");
  assert.deepEqual(h.clicks, ["newChat"]);
});
test("SPA navigation between preflight and claim invalidates even without a reload", async () => {
  const h = domHarness(); const before = await h.message({action: "PRECHECK"});
  h.navigate("https://chatgpt.com/c/changed");
  assert.equal((await h.message({action: "NEW_CHAT", documentId: before.documentId,
    reservationId: "reservation-1"})).errorCode, "CHATGPT_BINDING_CHANGED");
  assert.deepEqual(h.clicks, []);
});
for (const kind of ["push", "replace", "traverse", "reload", "pagehide"]) {
  test(`factory ${kind} after insertion never authorizes SEND, including navigate-away-and-back`, async () => {
    const h = domHarness(); const fields = await h.prepare("exact");
    if (kind === "pagehide") h.lifecycleListeners.pagehide();
    else {
      h.navigate("https://chatgpt.com/c/other", {navigationType: kind});
      h.navigate("https://chatgpt.com/");
    }
    assert.equal((await h.message({action: "SEND", expiresAt: 10000, ...fields})).errorCode, "CHATGPT_BINDING_CHANGED");
    assert.deepEqual(h.clicks, ["newChat"]);
  });
}
test("navigation during asynchronous composer insertion invalidates the result", async () => {
  const h = domHarness(); const before = await h.message({action: "PRECHECK"});
  const fields = {documentId: before.documentId, reservationId: "reservation-1"};
  await h.message({action: "NEW_CHAT", ...fields});
  h.context.setTimeout = callback => { h.navigate("https://chatgpt.com/c/other"); callback(); };
  assert.equal((await h.message({action: "INSERT_BOOTSTRAP", bootstrap: "exact", ...fields})).errorCode, "CHATGPT_BINDING_CHANGED");
  assert.deepEqual(h.clicks, ["newChat"]);
});
test("navigation in a separate operator document cannot invalidate factory binding", async () => {
  const factory = domHarness(), operator = domHarness();
  const fields = await factory.prepare("exact"); operator.navigate("https://chatgpt.com/c/operator-next");
  assert.equal((await factory.message({action: "SEND", expiresAt: 10000, ...fields})).status, "OK");
  assert.deepEqual(operator.clicks, []);
});

async function optionsHarness(h) {
  class Element {
    constructor() { this.value = ""; this.textContent = ""; this.disabled = false; this.children = []; this.listeners = {}; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
  }
  const ids = ["status", "factory-message", "factory-tab-choice", "bridge-status", "factory-status",
    "factory-tab-id", "factory-origin-valid", "factory-reserved", "register-factory-tab",
    "clear-factory-tab", "factory-tab-form", "refresh-factory-tabs", "extension-id", "port", "secret", "pairing-form"];
  const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
  const api = h.controller.api;
  api.storage.local.get = async keys => Object.fromEntries((Array.isArray(keys) ? keys : [keys])
    .map(key => [key, structuredClone(h.data[key])]));
  api.storage.local.setAccessLevel = async value => assert.equal(value.accessLevel, "TRUSTED_CONTEXTS");
  api.storage.onChanged = {addListener(listener) { h.storageListener = listener; }};
  h.optionRequests = [];
  api.runtime.sendMessage = async message => {
    h.optionRequests.push(message);
    try { return {status: "OK", ...await optionsMessage(h.controller, message,
      {id: extensionId, url: optionsUrl}, optionsUrl), bridgeStatus: "CONNECTED"}; }
    catch (error) { return {status: "ERROR", errorCode: error.message}; }
  };
  const context = vm.createContext({chrome: api, document: {
    getElementById(id) { assert.ok(elements[id], id); return elements[id]; },
    createElement(tag) { assert.equal(tag, "option"); return new Element(); }
  }}, {codeGeneration: {strings: false, wasm: false}});
  const source = fs.readFileSync(new URL("../browser_extension/project_factory_bridge/options.js", import.meta.url), "utf8");
  await vm.runInContext(`(async () => {${source}\n})()`, context);
  return {elements, async trigger(id, type) {
    elements[id].listeners[type]({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
  }};
}
test("Options UI has no implicit choice and only registers the explicitly selected ID", async () => {
  const h = harness(); delete h.data.factoryTab;
  const ui = await optionsHarness(h);
  assert.equal(ui.elements["factory-tab-choice"].value, "");
  assert.equal(ui.elements["factory-status"].textContent, "NOT CONFIGURED");
  assert.equal(ui.elements["bridge-status"].textContent, "CONNECTED");
  assert.deepEqual(h.optionRequests.map(value => value.action), ["LIST", "STATUS"]);
  await ui.trigger("factory-tab-form", "submit");
  assert.equal(h.data.factoryTab, undefined);
  assert.equal(ui.elements["factory-message"].textContent, "CHATGPT_FACTORY_TAB_NOT_CONFIGURED");
  ui.elements["factory-tab-choice"].value = "7";
  await ui.trigger("factory-tab-form", "submit");
  assert.deepEqual(h.data.factoryTab, {factoryTabId: 7, browserInstanceId: "browser-test"});
  assert.equal(ui.elements["factory-status"].textContent, "CONFIGURED");
  assert.equal(ui.elements["factory-tab-id"].textContent, 7);
  assert.equal(ui.elements["factory-origin-valid"].textContent, "yes");
  await ui.trigger("clear-factory-tab", "click");
  assert.equal(h.data.factoryTab, undefined);
  assert.equal(ui.elements["factory-status"].textContent, "NOT CONFIGURED");
});
test("Options UI disables replacement/clear with a reservation and never displays saved secret", async () => {
  const h = harness();
  h.data.activeReservation = {binding, phase: "SEND_ATTEMPTED", reservationId: "reservation-1"};
  const secret = "ab".repeat(32); h.data.pairing = {port: 8765, secret, enabled: true};
  const ui = await optionsHarness(h);
  assert.equal(ui.elements.secret.value, "");
  assert.equal(ui.elements["register-factory-tab"].disabled, true);
  assert.equal(ui.elements["clear-factory-tab"].disabled, true);
  assert.ok(Object.values(ui.elements).every(element => !String(element.textContent).includes(secret)));
  await ui.trigger("clear-factory-tab", "click"); // Direct invocation still hits the backend guard.
  assert.equal(ui.elements["factory-message"].textContent, "CHATGPT_TAB_BUSY");
  assert.equal(h.data.factoryTab.factoryTabId, 7);
  assert.equal(h.data.activeReservation.phase, "SEND_ATTEMPTED");
  ui.elements.secret.value = secret;
  await ui.trigger("pairing-form", "submit");
  assert.equal(ui.elements.secret.value, "");
  assert.equal(h.data.activeReservation.phase, "SEND_ATTEMPTED");
});
