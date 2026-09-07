globalThis.PFComposer = Object.freeze({
  normalize(value) {
    return String(value ?? "")
      .replace(/\r\n?/g, "\n")
      .replace(/[\u200B\uFEFF]/g, "");
  },
  representations(element) {
    if (element.tagName === "TEXTAREA") return [this.normalize(element.value)];
    const contentEditable = element.getAttribute("contenteditable");
    const editable = element.isContentEditable === true ||
      contentEditable === "true" || contentEditable === "plaintext-only";
    if (!editable) throw new Error("CHATGPT_SELECTOR_UNAVAILABLE");
    const inner = this.normalize(element.innerText);
    const text = this.normalize(element.textContent);
    // ChatGPT/ProseMirror represents an empty composer as a placeholder paragraph
    // such as <p data-empty-paragraph="true"><br></p>. Treat that structure as
    // logically empty even when innerText exposes structural line breaks.
    if (text === "" && (inner.replace(/\n/g, "") === "" ||
        element.querySelector('p[data-empty-paragraph="true"]'))) return [""];
    return [...new Set([inner, text])];
  },
  read(element) {
    return this.representations(element)[0];
  },
  matches(element, expected) {
    const target = this.normalize(expected);
    return this.representations(element).some(observed =>
      observed === target || (!target.endsWith("\n") && observed === `${target}\n`)
    );
  },
  activate(element) {
    if (typeof element.click === "function") element.click();
    element.focus();
  },
  clear(element) {
    this.activate(element);
    if (element.tagName === "TEXTAREA") {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(element, "");
    } else {
      const selection = element.ownerDocument.getSelection();
      const range = element.ownerDocument.createRange();
      range.selectNodeContents(element);
      selection.removeAllRanges();
      selection.addRange(range);
      if (!element.ownerDocument.execCommand("delete", false, null)) {
        throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
      }
    }
    element.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "deleteContentBackward", data: null}));
    element.dispatchEvent(new Event("change", {bubbles: true}));
    if (this.read(element) !== "") throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
  },
  insert(element, bootstrap) {
    if (typeof bootstrap !== "string" || !bootstrap.length ||
        new TextEncoder().encode(bootstrap).length > 65536 || this.read(element) !== "") {
      throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
    }
    this.activate(element);
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
    if (!this.matches(element, bootstrap)) throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
  }
});
