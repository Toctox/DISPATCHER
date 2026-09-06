import test from "node:test";
import assert from "node:assert/strict";
import {Controller} from "../browser_extension/project_factory_bridge/controller.js";
import {CAPABILITY} from "../browser_extension/project_factory_bridge/protocol.js";

const extensionId = "a".repeat(32);

function precheck(binding) {
  const value = {
    protocolVersion: 1,
    type: "COMMAND",
    requestId: crypto.randomUUID(),
    action: "PRECHECK",
  };
  if (binding) value.binding = binding;
  return value;
}

function harness({url = "https://chatgpt.com/c/old"} = {}) {
  const data = {
    factoryTab: {factoryTabId: 7, browserInstanceId: "browser-test"},
  };
  const updates = [];
  let tab = {
    id: 7,
    url,
    status: "complete",
    incognito: false,
    discarded: false,
    frozen: false,
    pendingUrl: undefined,
  };
  let documentId = "document-after-normalization";
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
        return structuredClone(tab);
      },
      async update(tabId, values) {
        assert.equal(tabId, 7);
        updates.push(structuredClone(values));
        tab = {...tab, url: values.url, status: "complete", pendingUrl: undefined};
        documentId = "document-after-normalization";
        return structuredClone(tab);
      },
      async sendMessage(tabId, value, options) {
        assert.equal(tabId, 7);
        assert.deepEqual(options, {frameId: 0});
        assert.equal(value.action, "PRECHECK");
        return {status: "OK", documentId, capability: CAPABILITY};
      },
    },
  };
  return {controller: new Controller(api, "browser-test"), updates};
}

test("unpinned PRECHECK normalizes a conversation Factory Tab before issuing binding", async () => {
  const h = harness();
  const result = await h.controller.reply(precheck());
  assert.equal(result.status, "OK");
  assert.deepEqual(h.updates, [{url: "https://chatgpt.com/"}]);
  assert.deepEqual(result.binding, {
    extensionId,
    browserInstanceId: "browser-test",
    tabId: 7,
    documentId: "document-after-normalization",
  });
});

test("unpinned PRECHECK does not navigate an already pristine root Factory Tab", async () => {
  const h = harness({url: "https://chatgpt.com/"});
  const result = await h.controller.reply(precheck());
  assert.equal(result.status, "OK");
  assert.deepEqual(h.updates, []);
});

test("pinned worker PRECHECK remains read-only", async () => {
  const h = harness({url: "https://chatgpt.com/"});
  const binding = {
    extensionId,
    browserInstanceId: "browser-test",
    tabId: 7,
    documentId: "document-after-normalization",
  };
  const result = await h.controller.reply(precheck(binding));
  assert.equal(result.status, "OK");
  assert.deepEqual(h.updates, []);
});
