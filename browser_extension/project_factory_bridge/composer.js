globalThis.PFComposer = Object.freeze({
  normalize(value) {
    return String(value ?? "")
      .replace(/\r\n?/g, "\n")
      .replace(/[\u200B\uFEFF]/g, "");
  },
  domRepresentation(element) {
    if (!element?.childNodes || typeof element.childNodes[Symbol.iterator] !== "function") return null;
    const blockTags = new Set(["P", "DIV", "LI", "PRE", "BLOCKQUOTE"]);
    const readNode = node => {
      if (!node) return "";
      if (node.nodeType === 3) return node.nodeValue || "";
      if (node.nodeType !== 1) return "";
      const tag = String(node.tagName || "").toUpperCase();
      if (tag === "BR") {
        const className = typeof node.getAttribute === "function" ? (node.getAttribute("class") || "") : "";
        if (className.split(/\s+/).includes("ProseMirror-trailingBreak")) return "";
        return "\n";
      }
      let value = "";
      if (node.childNodes && typeof node.childNodes[Symbol.iterator] === "function") {
        for (const child of node.childNodes) value += readNode(child);
      }
      return value;
    };
    const children = Array.from(element.childNodes);
    const hasBlockChildren = children.some(node =>
      node?.nodeType === 1 && blockTags.has(String(node.tagName || "").toUpperCase())
    );
    if (!hasBlockChildren) return this.normalize(readNode(element));
    const parts = children.map(node => {
      const value = this.normalize(readNode(node));
      const isBlock = node?.nodeType === 1 && blockTags.has(String(node.tagName || "").toUpperCase());
      return isBlock ? value.replace(/\n$/, "") : value;
    });
    return this.normalize(parts.join("\n"));
  },
  representations(element) {
    if (element.tagName === "TEXTAREA") return [this.normalize(element.value)];
    const contentEditable = element.getAttribute("contenteditable");
    const editable = element.isContentEditable === true ||
      contentEditable === "true" || contentEditable === "plaintext-only";
    if (!editable) throw new Error("CHATGPT_SELECTOR_UNAVAILABLE");
    const inner = this.normalize(element.innerText);
    const text = this.normalize(element.textContent);
    const dom = this.domRepresentation(element);
    // ChatGPT/ProseMirror represents an empty composer as a structural paragraph:
    // <p data-empty-paragraph="true" ...><br ...></p>. It is logically empty.
    const hasEmptyParagraph = typeof element.querySelector === "function" &&
      Boolean(element.querySelector('p[data-empty-paragraph="true"]'));
    if (text === "" && (inner.replace(/\n/g, "") === "" || hasEmptyParagraph)) return [""];
    return [...new Set([inner, text, dom].filter(value => value !== null))];
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
    if (!this.matches(element, bootstrap)) throw new Error("CHATGPT_BOOTSTRAP_MISMATCH");
  }
});
