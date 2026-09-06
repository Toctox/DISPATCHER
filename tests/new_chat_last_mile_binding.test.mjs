import test from "node:test";
import assert from "node:assert/strict";
import {Controller} from "../browser_extension/project_factory_bridge/controller.js";
import {CAPABILITY} from "../browser_extension/project_factory_bridge/protocol.js";

const extensionId = "a".repeat(32);
const binding = {
  extensionId,
  browserInstanceId: "browser-test",
  tabId: 7,
  documentId: "document-test",
};

function rpc(action = "PRECHECK") {
  const value = {
    protocolVersion: 1,
    type: "COMMAND",
    requestId: crypto.randomUUID(),
    action,
  };
  if (action !== "PRECHECK") {
    value.binding = binding;
    value.reservationId = "reservation-1";
  }
  return value;
}

function harness({newChatError = ""} = {}) {
  const data = {
    factoryTab: {factoryTabId: 7, browserInstanceId: "browser-test"},
  };
  const actions = [];
  const api = {
    runtime: {id: extensionId},
    storage: {local: {
      async get(key) { return {[key]: structuredClone(data[key])}; },
      async set(values) { Object.assign(data, structuredClone(values)); },
      async remove(key) { delete data[key]; },
    }},
    tabs: {
      async get(tabId) {
        assert.equal(tabId, 7);
        return {id: 7, url: "https://chatgpt.com/", status: "complete", incognito: false, discarded: false};
      },
      async sendMessage(tabId, value, options) {
        assert.equal(tabId, 7);
        assert.deepEqual(options, {frameId: 0});
        actions.push(value.action);
        if (value.action === "PRECHECK") {
          return {status: "OK", documentId: "document-test", capability: CAPABILITY};
        }
        if (value.action === "NEW_CHAT" && newChatError) {
          return {status: "ERROR", errorCode: newChatError};
        }
        return {status: "OK"};
      },
    },
  };
  return {controller: new Controller(api, "browser-test"), actions, data};
}

test("NEW_CHAT uses pinned preflight without a redundant second PRECHECK", async () => {
  const h = harness();
  assert.equal((await h.controller.reply(rpc("PRECHECK"))).status, "OK");
  assert.equal((await h.controller.reply(rpc("NEW_CHAT"))).status, "OK");
  assert.deepEqual(h.actions, ["PRECHECK", "NEW_CHAT"]);
  assert.equal(h.data.activeReservation.phase, "NEW_CHAT");
});

test("content remains the final documentId guard and preserves recovery evidence", async () => {
  const h = harness({newChatError: "CHATGPT_BINDING_CHANGED"});
  assert.equal((await h.controller.reply(rpc("PRECHECK"))).status, "OK");
  const result = await h.controller.reply(rpc("NEW_CHAT"));
  assert.equal(result.errorCode, "CHATGPT_BINDING_CHANGED");
  assert.deepEqual(h.actions, ["PRECHECK", "NEW_CHAT"]);
  assert.equal(h.data.activeReservation.phase, "NEW_CHAT_ATTEMPTED");
  assert.deepEqual(h.data.activeReservation.binding, binding);
});
