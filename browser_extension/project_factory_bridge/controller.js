import {CAPABILITY, CONTROL, RECOVERY_RELEASE, VERSION, exactKeys, fail, sameBinding, safeCode, validateCommand} from "./protocol.js";
import {FactoryTabRegistry} from "./factory_tab.js";

export class Controller {
  constructor(api, instanceId) {
    this.api = api; this.instanceId = instanceId; this.busy = false;
    this.registry = new FactoryTabRegistry(api, instanceId);
  }
  async configureFactoryTab(action, tabId) {
    // Same exclusion as dispatch commands: registering cannot race NEW_CHAT/reservation persistence.
    if (this.busy) fail("CHATGPT_TAB_BUSY");
    this.busy = true;
    try {
      const {activeReservation} = await this.api.storage.local.get("activeReservation");
      if (activeReservation) fail("CHATGPT_TAB_BUSY");
      if (action === "REGISTER") await this.registry.register(tabId);
      else if (action === "CLEAR") await this.registry.clear();
      else fail("CHATGPT_PROTOCOL_INVALID");
      return await this.registry.status();
    } finally { this.busy = false; }
  }
  async content(tabId, command) {
    let value;
    try { value = await this.api.tabs.sendMessage(tabId, command, {frameId: 0}); }
    catch { fail("CHATGPT_FACTORY_TAB_INVALID"); }
    if (value?.status === "ERROR" && exactKeys(value, ["status", "errorCode"])) {
      fail(safeCode(new Error(value.errorCode)));
    }
    if (command.action === "PRECHECK" && value?.status === "OK" && value.capability !== CAPABILITY) {
      fail("CHATGPT_EXTENSION_VERSION_MISMATCH");
    }
    const keys = command.action === "PRECHECK" ? ["status", "documentId", "capability"] : ["status"];
    if (!exactKeys(value, keys) || value.status !== "OK") fail();
    return value;
  }
  async inspect(expected) {
    const registration = await this.registry.registered();
    if (expected && (expected.tabId !== registration.factoryTabId ||
        expected.browserInstanceId !== registration.browserInstanceId)) fail("CHATGPT_BINDING_CHANGED");
    const page = await this.content(registration.factoryTabId, {action: "PRECHECK"});
    const current = await this.registry.registered();
    if (current.factoryTabId !== registration.factoryTabId) fail("CHATGPT_BINDING_CHANGED");
    const binding = {extensionId: this.api.runtime.id, browserInstanceId: this.instanceId,
      tabId: registration.factoryTabId, documentId: page.documentId};
    if (expected && !sameBinding(binding, expected)) fail("CHATGPT_BINDING_CHANGED");
    if (!sameBinding(binding, binding)) fail();
    return binding;
  }
  async execute(value) {
    validateCommand(value);
    // Normalize internally only; reservation bookkeeping is a CONTROL frame, never a UI action.
    if (value.type === "CONTROL") value = {...value, action: value.control};
    if (this.busy) fail("CHATGPT_TAB_BUSY");
    this.busy = true;
    try {
      const {activeReservation: active} = await this.api.storage.local.get("activeReservation");
      if (value.action === RECOVERY_RELEASE) {
        // Only the authenticated bridge emits this after checking local/Drive evidence.
        // Compare the OLD pinned identity, not the current tab/session. No DOM or tab API.
        if (!exactKeys(active, ["reservationId", "binding", "phase"]) ||
            active.reservationId !== value.reservationId ||
            !sameBinding(active.binding, value.binding) ||
            active.binding.extensionId !== this.api.runtime.id ||
            active.phase !== "NEW_CHAT_ATTEMPTED") fail("CHATGPT_RESERVATION_INVALID");
        await this.api.storage.local.remove("activeReservation");
        return {};
      }
      if (["PRECHECK", "NEW_CHAT"].includes(value.action) && active) fail("CHATGPT_TAB_BUSY");
      if (value.action === "PRECHECK") return {binding: await this.inspect(value.binding)};
      if (value.action !== "NEW_CHAT") {
        if (!active || active.reservationId !== value.reservationId ||
            !sameBinding(active.binding, value.binding)) fail("CHATGPT_RESERVATION_INVALID");
        const expected = {INSERT_BOOTSTRAP: "NEW_CHAT", SEND: "INSERT_BOOTSTRAP",
          RECEIPT_CONFIRMED: "SEND_ATTEMPTED"}[value.action];
        if (active.phase !== expected) fail("CHATGPT_RESERVATION_INVALID");
      }
      if (value.action === CONTROL) {
        // Authenticated worker already validated the Drive receipt. No DOM work here.
        await this.api.storage.local.remove("activeReservation");
        return {};
      }
      await this.inspect(value.binding);
      const reservation = {reservationId: value.reservationId, binding: value.binding,
        phase: `${value.action}_ATTEMPTED`};
      await this.api.storage.local.set({activeReservation: reservation});
      // New tab/reload between inspect and this message is rejected by documentId in content.js.
      const command = {action: value.action, documentId: value.binding.documentId,
        reservationId: value.reservationId};
      if (value.action === "INSERT_BOOTSTRAP") command.bootstrap = value.bootstrap;
      if (value.action === "SEND") {
        if (Date.now() >= value.expiresAt) fail("CHATGPT_SEND_UNCERTAIN");
        command.expiresAt = value.expiresAt;
      }
      await this.content(value.binding.tabId, command);
      if (value.action !== "SEND") {
        reservation.phase = value.action;
        await this.api.storage.local.set({activeReservation: reservation});
      }
      return {};
    } finally { this.busy = false; }
  }
  async reply(value) {
    const result = {protocolVersion: VERSION, type: "RESULT", requestId: value?.requestId};
    try { return {...result, status: "OK", ...await this.execute(value)}; }
    catch (error) { return {...result, status: "ERROR", errorCode: safeCode(error)}; }
  }
}
