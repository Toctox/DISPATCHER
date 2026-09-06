import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const extensionId = "a".repeat(32);

function harness() {
  const composer = {current: ""};
  const account = {};
  const send = {click() { assert.fail("SEND is outside this regression test"); }};
  const clicks = [];
  let navigateListener;
  const location = {
    origin: "https://chatgpt.com",
    pathname: "/",
    search: "",
    hash: "",
  };
  const newChat = {click() {
    clicks.push("newChat");
    location.pathname = "/";
    location.search = "";
    location.hash = "";
    composer.current = "";
  }};
  const controls = {account, newChat, composer, send, login: null};
  let listener;

  const context = vm.createContext({
    crypto,
    URL,
    TextEncoder,
    navigation: {addEventListener(name, value) {
      if (name === "navigate") navigateListener = value;
    }},
    addEventListener() {},
    location,
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

  function sameRouteNavigate(url) {
    navigateListener({
      destination: {url, sameDocument: true},
      navigationType: "replace",
    });
    const parsed = new URL(url);
    location.origin = parsed.origin;
    location.pathname = parsed.pathname;
    location.search = parsed.search;
    location.hash = parsed.hash;
  }

  return {composer, clicks, message, sameRouteNavigate};
}

test("pristine root Factory Tab is already NEW_CHAT and is not navigated", async () => {
  const h = harness();
  const preflight = await h.message({action: "PRECHECK"});
  assert.equal(preflight.status, "OK");
  const fields = {documentId: preflight.documentId, reservationId: "reservation-1"};

  assert.equal((await h.message({action: "NEW_CHAT", ...fields})).status, "OK");
  assert.deepEqual(h.clicks, []);

  assert.equal((await h.message({action: "INSERT_BOOTSTRAP", bootstrap: "exact", ...fields})).status, "OK");
  assert.equal(h.composer.current, "exact");
  assert.deepEqual(h.clicks, []);
});

test("idle same-route SPA churn keeps binding valid until NEW_CHAT establishes the surface", async () => {
  const h = harness();
  const preflight = await h.message({action: "PRECHECK"});
  assert.equal(preflight.status, "OK");
  const fields = {documentId: preflight.documentId, reservationId: "reservation-1"};

  h.sameRouteNavigate("https://chatgpt.com/?temporary-chat=true");
  assert.equal((await h.message({action: "NEW_CHAT", ...fields})).status, "OK");
  assert.deepEqual(h.clicks, ["newChat"]);

  assert.equal((await h.message({action: "INSERT_BOOTSTRAP", bootstrap: "exact", ...fields})).status, "OK");
  assert.equal(h.composer.current, "exact");
});
