export const VERSION = 1;
export const CAPABILITY = "FACTORY_TAB_V1";
export const ACTIONS = Object.freeze(["PRECHECK", "NEW_CHAT", "INSERT_BOOTSTRAP", "SEND"]);
export const CONTROL = "RECEIPT_CONFIRMED";
export const RECOVERY_RELEASE = "PRE_SEND_RECOVERY_RELEASE";
export const RECOVERY_RELEASE_BOOTSTRAP = "PRE_BOOTSTRAP_RECOVERY_RELEASE";
export const CODES = new Set([
  "CHATGPT_BRIDGE_UNAVAILABLE", "CHATGPT_EXTENSION_NOT_PAIRED",
  "CHATGPT_EXTENSION_VERSION_MISMATCH", "CHATGPT_FACTORY_TAB_NOT_CONFIGURED",
  "CHATGPT_FACTORY_TAB_NOT_FOUND", "CHATGPT_FACTORY_TAB_INVALID",
  "CHATGPT_AUTH_REQUIRED", "CHATGPT_SELECTOR_UNAVAILABLE", "CHATGPT_TAB_BUSY",
  "CHATGPT_BINDING_CHANGED", "CHATGPT_PROTOCOL_INVALID", "CHATGPT_BOOTSTRAP_MISMATCH",
  "CHATGPT_SEND_UNCERTAIN", "CHATGPT_NEW_CHAT_FAILED", "CHATGPT_RESERVATION_INVALID"
]);
export function fail(code = "CHATGPT_PROTOCOL_INVALID") { throw new Error(code); }
export function safeCode(error) {
  return CODES.has(error?.message) ? error.message : "CHATGPT_BRIDGE_UNAVAILABLE";
}
export function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join(",") === [...keys].sort().join(",");
}
export function validBinding(value) {
  return exactKeys(value, ["extensionId", "browserInstanceId", "tabId", "documentId"]) &&
    /^[a-p]{32}$/.test(value.extensionId) && Number.isSafeInteger(value.tabId) && value.tabId >= 0 &&
    [value.browserInstanceId, value.documentId].every(v => typeof v === "string" && /^[a-zA-Z0-9-]{1,80}$/.test(v));
}
export function sameBinding(a, b) {
  return validBinding(a) && validBinding(b) && Object.keys(a).every(k => a[k] === b[k]);
}
export function validateCommand(value) {
  if (value?.protocolVersion !== VERSION) fail("CHATGPT_EXTENSION_VERSION_MISMATCH");
  const action = value.type === "CONTROL" ? value.control : value.action;
  if (!((value.type === "COMMAND" && ACTIONS.includes(action)) ||
        (value.type === "CONTROL" && [CONTROL, RECOVERY_RELEASE, RECOVERY_RELEASE_BOOTSTRAP].includes(action)))) fail();
  const keys = ["protocolVersion", "type", "requestId", value.type === "CONTROL" ? "control" : "action"];
  if (action !== "PRECHECK") keys.push("binding", "reservationId");
  else if ("binding" in value) keys.push("binding");
  if (action === "INSERT_BOOTSTRAP") keys.push("bootstrap");
  if (action === "SEND") keys.push("expiresAt");
  if (!exactKeys(value, keys) || typeof value.requestId !== "string" ||
      !/^[a-zA-Z0-9-]{1,80}$/.test(value.requestId)) fail();
  if ("binding" in value && !validBinding(value.binding)) fail();
  if (action !== "PRECHECK" && (typeof value.reservationId !== "string" ||
      !/^[a-zA-Z0-9-]{1,80}$/.test(value.reservationId))) fail();
  if (action === "INSERT_BOOTSTRAP" && (typeof value.bootstrap !== "string" ||
      !value.bootstrap.length || new TextEncoder().encode(value.bootstrap).length > 65536)) {
    fail("CHATGPT_BOOTSTRAP_MISMATCH");
  }
  if (action === "SEND" && (!Number.isSafeInteger(value.expiresAt) || value.expiresAt <= 0)) fail();
  return value;
}
export async function proof(secret, role, nonce, extensionId) {
  if (!/^[0-9a-f]{64}$/.test(secret) || !/^[0-9a-f]{64}$/.test(nonce)) fail();
  const bytes = Uint8Array.from(secret.match(/../g), hex => parseInt(hex, 16));
  const key = await crypto.subtle.importKey("raw", bytes, {name: "HMAC", hash: "SHA-256"}, false, ["sign"]);
  const payload = new TextEncoder().encode(`PROJECT_FACTORY_BRIDGE_V1\n${role}\n${nonce}\n${extensionId}`);
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, payload));
  return [...signature].map(byte => byte.toString(16).padStart(2, "0")).join("");
}
