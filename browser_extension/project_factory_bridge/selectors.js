// Isolated world. Only controls needed for authentication and bootstrap submission.
globalThis.PFSelectors = Object.freeze({
  account: ['[data-testid="accounts-profile-button"]', 'button[aria-label="Open profile menu"]'],
  login: ['[data-testid="login-button"]', 'button[aria-label="Log in"]'],
  newChat: ['[data-testid="create-new-chat-button"]', 'a[aria-label="New chat"]', 'button[aria-label="New chat"]'],
  composer: ['textarea[data-testid="prompt-textarea"]', 'textarea#prompt-textarea',
    '[contenteditable="true"][data-testid="prompt-textarea"]', '#prompt-textarea[contenteditable="true"]'],
  send: ['button[data-testid="send-button"]', 'button[aria-label="Send prompt"]']
});

// Keep raw DOM matches distinct from usable controls. Responsive clones may have dimensions
// while an ancestor hides/inerts them or pointer events are disabled.
globalThis.PFSelection = (() => {
  function isUsable(element) {
    if (!element || !element.isConnected || element.hidden || element.disabled ||
        element.getAttribute("aria-disabled") === "true") return false;
    if (element.closest('[hidden],[aria-hidden="true"],[inert]')) return false;
    const style = getComputedStyle(element);
    if (style.display === "none" || style.visibility === "hidden" ||
        style.visibility === "collapse" || style.pointerEvents === "none") return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }
  function rawMatches(kind) {
    return [...new Set(PFSelectors[kind].flatMap(selector => [...document.querySelectorAll(selector)]))];
  }
  function candidates(kind) { return rawMatches(kind).filter(isUsable); }
  return Object.freeze({isUsable, rawMatches, candidates});
})();
