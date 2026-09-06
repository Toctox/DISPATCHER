import test from "node:test";
import assert from "node:assert/strict";

import {Controller} from "../browser_extension/project_factory_bridge/controller.js";
import {RECOVERY_RELEASE} from "../browser_extension/project_factory_bridge/protocol.js";

const extensionId = "a".repeat(32);
const binding = {
  extensionId,
  browserInstanceId: "browser-test",
  tabId: 7,
  documentId: "document-test",
};

function request() {
  return {
    protocolVersion: 1,
    type: "CONTROL",
    control: RECOVERY_RELEASE,
    requestId: "recovery-binding-test",
    binding,
    reservationId: "reservation-1",
  };
}

function harness(activeReservation) {
  const data = {};
  if (activeReservation !== undefined) data.activeReservation = structuredClone(activeReservation);
  const api = {
    runtime: {id: extensionId},
    storage: {
      local: {
        async get(key) { return {[key]: structuredClone(data[key])}; },
        async set(values) { Object.assign(data, structuredClone(values)); },
        async remove(key) { delete data[key]; },
      },
    },
  };
  return {controller: new Controller(api, "browser-test"), data};
}

test("NEW_CHAT recovery is a safe no-op when extension reservation was never persisted", async () => {
  const h = harness(undefined);
  assert.equal((await h.controller.reply(request())).status, "OK");
  assert.equal(h.data.activeReservation, undefined);
});

test("NEW_CHAT recovery clears the exact extension reservation", async () => {
  const h = harness({reservationId: "reservation-1", binding, phase: "NEW_CHAT_ATTEMPTED"});
  assert.equal((await h.controller.reply(request())).status, "OK");
  assert.equal(h.data.activeReservation, undefined);
});

test("NEW_CHAT recovery still refuses any divergent extension reservation", async () => {
  const active = {reservationId: "reservation-1", binding, phase: "SEND_ATTEMPTED"};
  const h = harness(active);
  const result = await h.controller.reply(request());
  assert.equal(result.status, "ERROR");
  assert.equal(result.errorCode, "CHATGPT_RESERVATION_INVALID");
  assert.deepEqual(h.data.activeReservation, active);
});
