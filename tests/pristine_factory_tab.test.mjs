import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const extensionId = "a".repeat(32);
const composer = {current: ""};
const account = {};
const send = {click() { assert.fail("SEND is outside this regression test"); }};
const clicks = [];
const newChat = {click() { clicks.push("newChat"); }};
const controls = {account, newChat, composer, send, login: null};
let listener;

const context = vm.createContext({
  crypto,
  URL,
  TextEncoder,
  navigation: {addEventListener() {}},
  addEventListener() {},
  location: {
    origin: "https://chatgpt.com",
    pathname: "/",
    search: "",
    hash: "",
  },
  PFSelection: {
    candidates(kind) {
      const value = controls[kind];
      return value ? [value] : [];
    },
  },
  PFComposer: {
    read(element) { return element.current; },
    insert(element, value) { element.current = value; },
  },
  setTimeout(callback) { callback(); },
  chrome: {
    runtime: {
      id: extensionId,
      onMessage: {addListener(value) { listener = value; }},
    },
  },
}, {codeGeneration: {strings: false, wasm: false}});

vm.runInContext(
  fs.readFileSync(new URL("../browser_extension/project_factory_bridge/content.js", import.meta.url), "utf8"),
  context,
);

function message(value) {
  return new Promise(resolve => {
    const handled = listener(value, {id: extensionId}, resolve);
    if (!handled) resolve(null);
  });
}

test("pristine root Factory Tab is already NEW_CHAT and is not navigated", async () => {
  const preflight = await message({action: "PRECHECK"});
  assert.equal(preflight.status, "OK");
  const fields = {documentId: preflight.documentId, reservationId: "reservation-1"};

  assert.equal((await message({action: "NEW_CHAT", ...fields})).status, "OK");
  assert.deepEqual(clicks, []);

  assert.equal((await message({action: "INSERT_BOOTSTRAP", bootstrap: "exact", ...fields})).status, "OK");
  assert.equal(composer.current, "exact");
  assert.deepEqual(clicks, []);
});
