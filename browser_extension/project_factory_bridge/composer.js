globalThis.PFComposer = Object.freeze({
  read(element) {
    if (element.tagName === "TEXTAREA") return element.value;
    const contentEditable = element.getAttribute("contenteditable");
    const editable = element.isContentEditable === true ||
      contentEditable === "true" || contentEditable === "plaintext-only";
    if (editable) {
      const text = element.innerText;
      // A solitary editor placeholder BR isn't user text. Read only this composer.
      if (text === "\n" && element.textContent === "") return "";
      return text;
    }
    throw new Error("CHATGPT_SELECTOR_UNAVAILABLE");
  },
  insert(element, bootstrap) {
    if (typeof bootstrap !== "string" || !bootstrap.length ||
        new TextEncoder().encode(bootstrap).length > 65536 || this.read(element) !== "") {
      throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
    }
    element.focus();
    if (element.tagName === "TEXTAREA") {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(element, bootstrap);
    } else {
      const selection = element.ownerDocument.getSelection();
      const range = element.ownerDocument.createRange();
      range.selectNodeContents(element);
      selection.removeAllRanges();
      selection.addRange(range);
      // Fixed native editing operation, not an executable payload or arbitrary DOM command.
      if (!element.ownerDocument.execCommand("insertText", false, bootstrap)) {
        throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
      }
    }
    element.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: bootstrap}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
    if (this.read(element) !== bootstrap) throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
  }
});
