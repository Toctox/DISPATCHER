(() => {
  let documentId = crypto.randomUUID();
  let reservationId = null;
  let phase = "IDLE";
  let expectedBootstrap = null;
  let busy = false;
  let allowedNewChatNavigation = false;
  const fail = code => { throw new Error(code); };
  const isNewChatPath = pathname => pathname === "/" || /^\/[A-Za-z]{2}(?:-[A-Za-z]{2})?\/$/.test(pathname);
  const composerMatches = (element, expected) => typeof PFComposer.matches === "function"
    ? PFComposer.matches(element, expected)
    : PFComposer.read(element) === expected;
  function invalidateBinding() {
    documentId = crypto.randomUUID();
    phase = "BINDING_CHANGED";
    expectedBootstrap = null;
    allowedNewChatNavigation = false;
  }
  const navigationSupported = typeof globalThis.navigation?.addEventListener === "function";
  if (navigationSupported) {
    navigation.addEventListener("navigate", event => {
      const destination = new URL(event.destination.url);
      if (phase === "IDLE" && event.destination.sameDocument &&
          destination.origin === location.origin && destination.pathname === location.pathname) {
        return;
      }
      if (allowedNewChatNavigation && phase === "NEW_CHAT_ATTEMPTED" &&
          event.destination.sameDocument && destination.origin === "https://chatgpt.com" &&
          isNewChatPath(destination.pathname) && !destination.search && !destination.hash &&
          ["push", "replace"].includes(event.navigationType)) {
        allowedNewChatNavigation = false;
        return;
      }
      invalidateBinding();
    });
  }
  addEventListener("pagehide", invalidateBinding);
  function requireBinding(value) {
    if (value.documentId !== documentId) fail("CHATGPT_BINDING_CHANGED");
  }
  const {candidates} = PFSelection;
  function control(kind) {
    const found = candidates(kind);
    if (found.length !== 1) fail("CHATGPT_SELECTOR_UNAVAILABLE");
    return found[0];
  }
  function precheck() {
    if (!navigationSupported) fail("CHATGPT_FACTORY_TAB_INVALID");
    if (location.origin !== "https://chatgpt.com" || candidates("login").length > 0 ||
        candidates("account").length < 1) fail("CHATGPT_AUTH_REQUIRED");
    control("newChat");
    control("composer");
  }
  function structuralDiagnostic() {
    const selectors = [
      "textarea", '[contenteditable="true"]', "button", '[role="button"]',
      "[data-testid]", "a[aria-label]"
    ];
    const seen = new Set();
    const controls = [];
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        if (seen.has(element)) continue;
        seen.add(element);
        const rect = element.getBoundingClientRect();
        controls.push({
          tag: element.tagName.toLowerCase(),
          id: element.id || null,
          role: element.getAttribute("role"),
          ariaLabel: element.getAttribute("aria-label"),
          dataTestId: element.getAttribute("data-testid"),
          contentEditable: element.getAttribute("contenteditable"),
          type: element.getAttribute("type"),
          name: element.getAttribute("name"),
          placeholder: ["TEXTAREA", "INPUT"].includes(element.tagName) ||
            element.getAttribute("contenteditable") === "true" ? element.getAttribute("placeholder") : null,
          usable: PFSelection.isUsable(element),
          rect: {
            x: Math.round(rect.x), y: Math.round(rect.y),
            width: Math.round(rect.width), height: Math.round(rect.height)
          }
        });
        if (controls.length >= 120) break;
      }
      if (controls.length >= 120) break;
    }
    return {
      origin: location.origin,
      pathname: location.pathname,
      readyState: document.readyState,
      selectorCounts: Object.fromEntries(Object.keys(PFSelectors).map(kind => [kind, {
        raw: PFSelection.rawMatches(kind).length,
        usable: PFSelection.candidates(kind).length
      }])),
      count: controls.length,
      controls
    };
  }
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  async function waitForControl(kind, timeoutMs) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const found = candidates(kind);
      if (found.length === 1) return found[0];
      await delay(100);
    }
    fail("CHATGPT_SELECTOR_UNAVAILABLE");
  }
  async function execute(value) {
    if (busy) fail("CHATGPT_TAB_BUSY");
    busy = true;
    try {
      if (!value || !["PRECHECK", "NEW_CHAT", "INSERT_BOOTSTRAP", "SEND", "DOM_DIAGNOSTIC_OPTIONS"].includes(value.action)) {
        fail("CHATGPT_PROTOCOL_INVALID");
      }
      if (value.action === "DOM_DIAGNOSTIC_OPTIONS") {
        return {status: "OK", diagnostic: structuralDiagnostic()};
      }
      if (value.action === "PRECHECK") {
        precheck();
        return {status: "OK", documentId, capability: "FACTORY_TAB_V1"};
      }
      requireBinding(value);
      if (value.action === "NEW_CHAT") {
        precheck();
        reservationId = value.reservationId;
        phase = "NEW_CHAT_ATTEMPTED";
        expectedBootstrap = null;
        if (isNewChatPath(location.pathname) && !location.search && !location.hash) {
          const composer = control("composer");
          if (PFComposer.read(composer) !== "" && typeof PFComposer.clear === "function") {
            PFComposer.clear(composer);
            await delay(100);
            requireBinding(value);
          }
          if (PFComposer.read(control("composer")) === "") {
            phase = "NEW_CHAT";
            return {status: "OK"};
          }
        }
        allowedNewChatNavigation = true;
        control("newChat").click();
        const deadline = Date.now() + 10000;
        while (Date.now() < deadline) {
          requireBinding(value);
          try {
            if (isNewChatPath(location.pathname)) {
              const composer = control("composer");
              if (PFComposer.read(composer) !== "" && typeof PFComposer.clear === "function") {
                PFComposer.clear(composer);
              }
              if (PFComposer.read(control("composer")) === "") {
                phase = "NEW_CHAT";
                allowedNewChatNavigation = false;
                return {status: "OK"};
              }
            }
          } catch (error) {
            if (!["CHATGPT_SELECTOR_UNAVAILABLE", "CHATGPT_BOOTSTRAP_MISMATCH"].includes(error.message)) throw error;
          }
          await delay(100);
        }
        fail("CHATGPT_NEW_CHAT_FAILED");
      }
      if (value.reservationId !== reservationId) fail("CHATGPT_RESERVATION_INVALID");
      if (value.action === "INSERT_BOOTSTRAP") {
        if (phase !== "NEW_CHAT") fail("CHATGPT_RESERVATION_INVALID");
        phase = "INSERT_BOOTSTRAP_ATTEMPTED";
        PFComposer.insert(control("composer"), value.bootstrap);
        await delay(100);
        requireBinding(value);
        if (!composerMatches(control("composer"), value.bootstrap)) fail("CHATGPT_BOOTSTRAP_MISMATCH");
        expectedBootstrap = value.bootstrap;
        phase = "INSERT_BOOTSTRAP";
        return {status: "OK"};
      }
      if (phase !== "INSERT_BOOTSTRAP") fail("CHATGPT_RESERVATION_INVALID");
      if (!composerMatches(control("composer"), expectedBootstrap)) fail("CHATGPT_BOOTSTRAP_MISMATCH");
      const button = await waitForControl("send", 5000);
      requireBinding(value);
      if (!Number.isSafeInteger(value.expiresAt) || Date.now() >= value.expiresAt) fail("CHATGPT_SEND_UNCERTAIN");
      phase = "SEND_ATTEMPTED";
      expectedBootstrap = null;
      button.click();
      return {status: "OK"};
    } finally { busy = false; }
  }
  chrome.runtime.onMessage.addListener((value, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || sender.tab) return false;
    execute(value).then(sendResponse, error => {
      const allowed = ["CHATGPT_PROTOCOL_INVALID", "CHATGPT_TAB_BUSY", "CHATGPT_AUTH_REQUIRED",
        "CHATGPT_SELECTOR_UNAVAILABLE", "CHATGPT_BINDING_CHANGED", "CHATGPT_NEW_CHAT_FAILED",
        "CHATGPT_RESERVATION_INVALID", "CHATGPT_BOOTSTRAP_MISMATCH", "CHATGPT_SEND_UNCERTAIN",
        "CHATGPT_FACTORY_TAB_INVALID"];
      sendResponse({status: "ERROR", errorCode: allowed.includes(error.message) ? error.message : "CHATGPT_SELECTOR_UNAVAILABLE"});
    });
    return true;
  });
})();
