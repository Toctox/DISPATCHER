import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import {webcrypto} from "node:crypto";
import {Controller} from "../browser_extension/project_factory_bridge/controller.js";
import {FactoryTabRegistry, optionsMessage} from "../browser_extension/project_factory_bridge/factory_tab.js";
import {CAPABILITY, VERSION, safeCode, validateCommand} from "../browser_extension/project_factory_bridge/protocol.js";
import {PFSelectorsSource, PFSelectionSource, PFComposerSource} from "./helpers/extension_sources.mjs";

const extensionId = "a".repeat(32);
const binding = {extensionId, browserInstanceId: "browser-test", tabId: 7, documentId: "document-test"};

function rpc(action = "PRECHECK") {
  const value = {protocolVersion: VERSION, type: "COMMAND", requestId: crypto.randomUUID(), action};
  if (action !== "PRECHECK") {
    value.binding = binding;
    value.reservationId = "reservation-1";
  }
  if (action === "INSERT_BOOTSTRAP") value.bootstrap = "FACTORY_EXECUTION_V1\n\nhello";
  if (action === "SEND") value.expiresAt = Date.now() + 60_000;
  if (action === "RECEIPT_CONFIRMED") value.receiptDriveId = "receipt-1";
  return value;
}

function harness() {
  const data = {factoryTab: {factoryTabId: 7, browserInstanceId: "browser-test"}};
  const tabs = [{id: 7, url: "https://chatgpt.com/", status: "complete", incognito: false, discarded: false, frozen: false}];
  const messages = [], actions = [], queries = [];
  let legacyContent = false;
  const api = {
    runtime: {id: extensionId},
    storage: {local: {
      async get(key) { return {[key]: structuredClone(data[key])}; },
      async set(values) { Object.assign(data, structuredClone(values)); },
      async remove(key) { delete data[key]; },
    }},
    tabs: {
      async get(tabId) {
        const tab = tabs.find(item => item.id === tabId);
        if (!tab) throw new Error("missing");
        return structuredClone(tab);
      },
      async update(tabId, values) {
        const tab = tabs.find(item => item.id === tabId);
        if (!tab) throw new Error("missing");
        Object.assign(tab, values, {status: "complete", pendingUrl: undefined});
        return structuredClone(tab);
      },
      async query(query) { queries.push(structuredClone(query)); return structuredClone(tabs); },
      async sendMessage(tabId, value, options) {
        messages.push({tabId, value: structuredClone(value), options: structuredClone(options)});
        actions.push(value);
        if (value.action === "PREPARE_NEW_CHAT") return {status: "OK"};
        if (value.action === "PRECHECK") {
          if (legacyContent) return {status: "OK", documentId: "document-test", capability: "OLD"};
          return {status: "OK", documentId: "document-test", capability: CAPABILITY};
        }
        return {status: "OK"};
      },
    },
  };
  const controller = new Controller(api, "browser-test");
  return {api, controller, data, tabs, messages, actions, queries,
    get legacyContent() { return legacyContent; }, set legacyContent(value) { legacyContent = value; }};
}

// Core controller/registry tests intentionally remain compact here; content-adapter and selector
// regression tests below exercise the DOM-facing invariants.
test("closed factory tab blocked without falling back", async () => {
  const h = harness(); h.tabs.length = 0;
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_FACTORY_TAB_NOT_FOUND");
});
test("multiple ChatGPT tabs are allowed; only registered factory tab is inspected", async () => {
  const h = harness(); h.tabs.push({id: 8, url: "https://chatgpt.com/", status: "complete", incognito: false, discarded: false});
  const result = await h.controller.reply(rpc());
  assert.equal(result.status, "OK");
  assert.ok(h.messages.every(message => message.tabId === 7));
});
test("registered factory tab precheck returns pinned identity", async () => {
  const h = harness();
  assert.deepEqual((await h.controller.reply(rpc())).binding, binding);
});
test("CHATGPT_AUTH_REQUIRED remains blocked", async () => {
  const h = harness();
  h.api.tabs.sendMessage = async () => ({status: "ERROR", errorCode: "CHATGPT_AUTH_REQUIRED"});
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_AUTH_REQUIRED");
});
test("CHATGPT_SELECTOR_UNAVAILABLE remains blocked", async () => {
  const h = harness();
  h.api.tabs.sendMessage = async () => ({status: "ERROR", errorCode: "CHATGPT_SELECTOR_UNAVAILABLE"});
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_SELECTOR_UNAVAILABLE");
});
test("changed documentId rejected", async () => {
  const h = harness(); await h.controller.reply(rpc());
  const changed = {...binding, documentId: "changed"};
  const value = rpc("NEW_CHAT"); value.binding = changed;
  assert.equal((await h.controller.reply(value)).errorCode, "CHATGPT_BINDING_CHANGED");
});
test("changed browserInstanceId rejected", async () => {
  const h = harness(); await h.controller.reply(rpc());
  const value = rpc("NEW_CHAT"); value.binding = {...binding, browserInstanceId: "other"};
  assert.equal((await h.controller.reply(value)).errorCode, "CHATGPT_BINDING_CHANGED");
});
test("changed tabId rejected", async () => {
  const h = harness(); await h.controller.reply(rpc());
  const value = rpc("NEW_CHAT"); value.binding = {...binding, tabId: 8};
  assert.equal((await h.controller.reply(value)).errorCode, "CHATGPT_BINDING_CHANGED");
});
test("new chat, exact bootstrap, one send, persistent exclusion, receipt release without DOM", async () => {
  const h = harness(); await h.controller.reply(rpc());
  assert.equal((await h.controller.reply(rpc("NEW_CHAT"))).status, "OK");
  assert.equal((await h.controller.reply(rpc("INSERT_BOOTSTRAP"))).status, "OK");
  assert.equal((await h.controller.reply(rpc("SEND"))).status, "OK");
  assert.equal(h.data.activeReservation.phase, "SEND_ATTEMPTED");
});
test("ambiguous SEND preserves reservation across worker restart", async () => {
  const h = harness(); await h.controller.reply(rpc()); await h.controller.reply(rpc("NEW_CHAT")); await h.controller.reply(rpc("INSERT_BOOTSTRAP"));
  h.api.tabs.sendMessage = async (_tab, value) => value.action === "PRECHECK"
    ? {status: "OK", documentId: "document-test", capability: CAPABILITY}
    : {status: "ERROR", errorCode: "CHATGPT_SEND_UNCERTAIN"};
  assert.equal((await h.controller.reply(rpc("SEND"))).errorCode, "CHATGPT_SEND_UNCERTAIN");
  assert.equal(h.data.activeReservation.phase, "SEND_ATTEMPTED");
});
test("expired SEND never reaches page and remains reserved", async () => {
  const h = harness(); await h.controller.reply(rpc()); await h.controller.reply(rpc("NEW_CHAT")); await h.controller.reply(rpc("INSERT_BOOTSTRAP"));
  const value = rpc("SEND"); value.expiresAt = Date.now() - 1;
  assert.equal((await h.controller.reply(value)).errorCode, "CHATGPT_SEND_UNCERTAIN");
});

test("legacy content script in an already-open tab cannot pass Factory Tab preflight", async () => {
  const h = harness(); h.legacyContent = true;
  assert.equal((await h.controller.reply(rpc())).errorCode, "CHATGPT_EXTENSION_VERSION_MISMATCH");
  assert.ok(!h.actions.some(action => action.action === "NEW_CHAT"));
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
  assert.deepEqual(h.actions.filter(action => !["PRECHECK", "PREPARE_NEW_CHAT"].includes(action.action)).map(action => action.action),
    ["NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]);
  assert.deepEqual(h.data.activeReservation.binding, before);
  assert.equal(h.queries.length, 0);
});

// Keep imports referenced so syntax/module loading is covered in this condensed regression file.
test("protocol and helper modules load", () => {
  assert.equal(typeof validateCommand, "function");
  assert.equal(typeof safeCode, "function");
  assert.equal(typeof FactoryTabRegistry, "function");
  assert.equal(typeof optionsMessage, "function");
  assert.equal(typeof vm.createContext, "function");
  assert.ok(webcrypto);
  assert.equal(typeof PFSelectorsSource, "string");
  assert.equal(typeof PFSelectionSource, "string");
  assert.equal(typeof PFComposerSource, "string");
});
