"use strict";
/* A live JSON tree. update(value) reconciles the new payload into the existing DOM, key by key: values that changed are rewritten in
   place and flash once; nothing is duplicated and nothing jumps. Only expanded branches are kept in the DOM, so a 300 KB payload
   costs what is on screen. Expanded/collapsed state is remembered by path. */

class LiveJson {
  constructor(host, { openDepth = 2 } = {}) {
    this.host = host; this.openDepth = openDepth; this.open = new Map(); this.flashes = []; this.root = null; this.filter = "";
    host.addEventListener("click", e => {
      const tw = e.target.closest(".tw"); if (!tw) return;
      const node = tw._node; if (!node) return;
      node.isOpen = !node.isOpen; this.open.set(node.path, node.isOpen); node.sync(); if (node.isOpen) node.fill(node.value, true);
    });
  }
  clear() { this.host.replaceChildren(); this.root = null; }
  update(value) {
    this.flashes = [];
    if (this.root && this.root.matches(value)) this.root.set(value, false);
    else { this.host.replaceChildren(); this.root = this.make("", "", value, 0, true); this.host.append(this.root.el); this.root.set(value, true); }
    if (this.flashes.length) {
      void this.host.offsetWidth;                                  // one reflow restarts every flash animation together
      for (const n of this.flashes) n.classList.add("flash");
    }
  }
  make(key, path, value, depth, isRoot = false) {
    const view = this; const isContainer = value !== null && typeof value === "object";
    const node = { key, path, depth, value, kids: new Map(), isContainer, isArray: Array.isArray(value), el: document.createElement("div") };
    node.el.className = "jn" + (isRoot ? " root" : "");
    const row = document.createElement("div"); row.className = "jrow"; node.el.append(row);
    node.keyEl = document.createElement("span");
    if (isContainer) {
      node.tw = document.createElement("span"); node.tw.className = "tw"; node.tw._node = node;
      node.isOpen = view.open.has(path) ? view.open.get(path) : depth < view.openDepth;
      node.openEl = document.createElement("span"); node.openEl.className = "jp";
      node.sumEl = document.createElement("span"); node.sumEl.className = "jsum";
      node.body = document.createElement("div"); node.close = document.createElement("div"); node.close.className = "jrow jp";
      node.el.append(node.body, node.close);
      row.append(node.tw);
    } else { const sp = document.createElement("span"); sp.className = "tw"; sp.innerHTML = "&nbsp;"; row.append(sp); }
    if (key !== "") { node.keyEl.className = "jk"; node.keyEl.textContent = typeof key === "number" ? String(key) : JSON.stringify(key); row.append(node.keyEl, Object.assign(document.createElement("span"), { className: "jp", textContent: ": " })); }
    if (isContainer) {
      row.append(node.openEl, node.sumEl);
      node.sync = () => {
        node.tw.textContent = node.isOpen ? "▾" : "▸"; node.openEl.textContent = node.isArray ? "[" : "{";
        node.body.style.display = node.isOpen ? "" : "none"; node.close.style.display = node.isOpen ? "" : "none";
        const n = node.isArray ? node.value.length : Object.keys(node.value).length;
        node.sumEl.textContent = node.isOpen ? "" : ` ${n} ${node.isArray ? "items" : "keys"} ${node.isArray ? "]" : "}"}`;
        node.close.textContent = node.isArray ? "]" : "}";
      };
    } else { node.valEl = document.createElement("span"); row.append(node.valEl); node.text = null; }
    node.matches = v => (v !== null && typeof v === "object") === node.isContainer && (!node.isContainer || Array.isArray(v) === node.isArray);
    node.fill = (v, force) => { if (node.isOpen) view.fillKids(node, v); };
    node.set = (v, fresh) => {
      node.value = v;
      if (node.isContainer) { node.sync(); if (node.isOpen) view.fillKids(node, v); }
      else {
        const text = typeof v === "string" ? JSON.stringify(v) : String(v);
        if (text !== node.text) {
          node.valEl.textContent = text; node.valEl.className = typeof v === "string" ? "js" : typeof v === "number" ? "jnum" : typeof v === "boolean" ? "jb" : "jnull";
          if (!fresh && node.text !== null) { node.valEl.classList.remove("flash"); view.flashes.push(node.valEl); }
          node.text = text;
        }
      }
    };
    return node;
  }
  fillKids(node, value) {
    const keys = node.isArray ? value.map((_, i) => i) : Object.keys(value), seen = new Set();
    let anchor = null, out = 0;
    for (const k of keys) {
      const v = value[k]; seen.add(k); let kid = node.kids.get(k);
      if (kid && !kid.matches(v)) { kid.el.remove(); node.kids.delete(k); kid = null; }
      if (!kid) { kid = this.make(node.isArray ? k : k, node.path + "/" + k, v, node.depth + 1); node.kids.set(k, kid); node.body.append(kid.el); kid.set(v, true); }
      else kid.set(v, false);
      const want = node.body.children[out];
      if (want !== kid.el) node.body.insertBefore(kid.el, want || null);
      out++;
      if (!node.isArray && out < keys.length) { /* object keys keep source order */ }
    }
    for (const [k, kid] of node.kids) if (!seen.has(k)) { kid.el.remove(); node.kids.delete(k); }
  }
}

/* Pretty, highlighted text for the "raw text" mode (capped so a huge payload cannot freeze the page). */
function jsonText(value, cap = 400000) {
  const text = JSON.stringify(value, null, 2);
  return text.length > cap ? text.slice(0, cap) + `\n… (${text.length - cap} more characters; use the tree view to browse the rest)` : text;
}
