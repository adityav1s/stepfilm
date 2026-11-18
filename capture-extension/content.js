// Stepfilm capture hook: describes each user action so the editor can write
// the tutorial step automatically. It never sends what the user typed: text
// field contents are only compared locally to notice that typing happened.
(() => {
  if (window.__stepfilmCaptureHookInstalled) return;
  window.__stepfilmCaptureHookInstalled = true;

  // Mask things that look personal even in visible labels and page titles.
  const redact = (value) => value
    .replace(/[\w.+-]+@[\w-]+(\.[\w-]+)+/g, "…")
    .replace(/\d[\d\s().-]{5,}\d/g, "…");
  const clean = (value, max = 80) => redact(String(value || "").replace(/\s+/g, " ").trim()).slice(0, max);
  const notify = (type, action) => chrome.runtime.sendMessage({ type, action }).catch(() => {});
  const INTERACTIVE = "a,button,input,select,textarea,summary,label,[role=button],[role=link],[role=tab],[role=menuitem],[role=menuitemcheckbox],[role=menuitemradio],[role=option],[role=checkbox],[role=radio],[role=switch],[role=combobox],[role=treeitem],[onclick],[tabindex]";
  const NON_TEXT_INPUTS = ["button", "submit", "reset", "checkbox", "radio", "image", "file", "range", "color", "hidden"];

  // Page address without the query string or fragment, which often hold
  // search terms, tokens, or record IDs.
  function safeUrl() {
    return `${location.origin}${location.pathname}`.slice(0, 500);
  }

  // Search pages usually repeat the query in the title; drop such titles.
  // Section anchors (#billing) and short tab IDs (?tab=members) stay, since
  // they are site vocabulary rather than something the user typed.
  const SEARCH_KEYS = /^(q|query|search|s|term|terms|keyword|keywords|k|text|search_query|find)$/i;
  function safeTitle() {
    const title = clean(document.title, 120);
    const lower = title.toLowerCase();
    const decode = (value) => { try { return decodeURIComponent(value.replace(/\+/g, " ")); } catch (_) { return value; } };
    const freeText = (value) => /\s|\d|@/.test(value) || value.length >= 16;
    const risky = [];
    const hashParams = location.hash.includes("=") ? [...new URLSearchParams(location.hash.slice(1).replace(/^[!/]+/, ""))] : [];
    for (const [key, value] of [...new URLSearchParams(location.search), ...hashParams]) {
      const text = decode(value).trim().toLowerCase();
      if (text.length >= 3 && (SEARCH_KEYS.test(key) || freeText(text))) risky.push(text);
    }
    const hash = decode(location.hash.slice(1)).trim().toLowerCase();
    if (hash.length >= 3 && (hash.includes("=") || freeText(hash))) risky.push(hash);
    return risky.some((value) => lower.includes(value)) ? "" : title;
  }

  // Guidde-style steps say what was typed ("Search for “espresso”"), except
  // in fields that can hold secrets. The owner can turn this off.
  let includeTyped = true;
  chrome.storage?.local.get("stepfilmIncludeTyped").then((saved) => { includeTyped = saved.stepfilmIncludeTyped !== false; }).catch(() => {});
  const SENSITIVE = /pass|pwd|card|cc-|cvv|cvc|csc|ssn|social|otp|one-time|code|pin|secret|token|key|iban|account|routing|tax|dob|birth|salary|security|answer|license|passport/i;
  function typedValue(field) {
    if (!includeTyped || field.tagName !== "INPUT" && field.tagName !== "TEXTAREA") return "";
    const type = (field.type || "text").toLowerCase();
    if (!["text", "search", "url", ""].includes(type) && field.tagName === "INPUT") return "";
    const hints = [field.name, field.id, field.getAttribute("autocomplete"), field.getAttribute("aria-label"), field.getAttribute("placeholder"), labelFor(field)].join(" ");
    if (SENSITIVE.test(hints) || /^(cc-|current-password|new-password|one-time-code|tel|email|bday|street-address|postal-code)/.test(field.getAttribute("autocomplete") || "")) return "";
    const value = String(field.value || "").trim();
    if (!value || value.length > 40 || /\n/.test(value)) return "";
    const cleaned = clean(value, 40);
    return cleaned.includes("…") ? "" : cleaned;
  }

  // Elements behind recent actions, so the background can re-measure them
  // after the screenshot (pages scroll or re-layout between the two).
  const refs = new Map();
  let nextRef = 1;
  function remember(element) {
    const ref = `r${nextRef++}`;
    refs.set(ref, element);
    if (refs.size > 40) refs.delete(refs.keys().next().value);
    return ref;
  }
  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== "stepfilm-locate") return;
    const element = refs.get(message.ref);
    if (!element || !element.isConnected) { sendResponse({ found: false }); return; }
    const rect = targetRect(element);
    const width = window.innerWidth || 1, height = window.innerHeight || 1;
    const box = (value, size) => Math.max(0, Math.min(1, value / size));
    const visible = rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < height && rect.left < width;
    sendResponse({ found: visible, rect: { x: box(rect.left, width), y: box(rect.top, height), width: box(rect.width, width), height: box(rect.height, height) } });
  });

  // The outermost editable element for inputs, textareas and rich editors.
  function textFieldOf(node) {
    if (!(node instanceof Element)) return null;
    const tag = node.tagName;
    if (tag === "TEXTAREA" || (tag === "INPUT" && !NON_TEXT_INPUTS.includes((node.type || "text").toLowerCase()))) return node;
    if (!node.isContentEditable) return null;
    let root = node;
    while (root.parentElement?.isContentEditable) root = root.parentElement;
    return root;
  }

  function labelFor(element) {
    if (element.labels?.length) {
      // A label that wraps its control would otherwise include the
      // control's own text (e.g. every option of a <select>).
      const copy = element.labels[0].cloneNode(true);
      copy.querySelectorAll("select,input,textarea,button,[contenteditable]").forEach((node) => node.remove());
      const text = clean(copy.textContent);
      if (text) return text;
    }
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy.split(/\s+/).map((id) => document.getElementById(id)?.innerText || "").join(" ");
      if (clean(text)) return clean(text);
    }
    return "";
  }

  function nameOf(element) {
    const tag = element.tagName.toLowerCase();
    // Never read what is inside a field or rich-text editor.
    const isField = ["input", "textarea", "select"].includes(tag) || element.isContentEditable;
    const candidates = [
      element.getAttribute("aria-label"),
      labelFor(element),
      isField ? "" : element.innerText,
      tag === "input" && ["button", "submit", "reset"].includes(element.type) ? element.value : "",
      element.getAttribute("alt"),
      element.querySelector?.("img[alt]")?.getAttribute("alt"),
      element.getAttribute("title"),
      element.getAttribute("placeholder"),
      element.getAttribute("aria-placeholder"),
      element.querySelector?.("svg title")?.textContent,
      isField ? element.getAttribute("name")?.replace(/[_-]+/g, " ") : "",
    ];
    for (const candidate of candidates) {
      const value = clean(candidate);
      if (value) return value.split(" ").length > 10 ? `${value.split(" ").slice(0, 10).join(" ")}…` : value;
    }
    // Unlabelled checkboxes/toggles in lists (e.g. a to-do item): use the row's text.
    if (["checkbox", "radio"].includes((element.type || "").toLowerCase()) || ["checkbox", "switch"].includes(element.getAttribute("role"))) {
      const row = element.closest("li,tr,[role=row],[role=listitem],[role=option]");
      const text = clean(row?.innerText, 60);
      if (text && text.split(" ").length <= 8) return text;
    }
    return "";
  }

  function roleOf(element) {
    const explicit = element.getAttribute("role");
    if (explicit) return explicit;
    const tag = element.tagName.toLowerCase();
    if (tag === "a") return "link";
    if (tag === "button" || tag === "summary") return "button";
    if (tag === "select") return "select";
    if (tag === "textarea") return "textbox";
    if (tag === "input") {
      const type = (element.type || "text").toLowerCase();
      if (["button", "submit", "reset", "image"].includes(type)) return "button";
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "file") return "file";
      if (type === "search") return "searchbox";
      return "textbox";
    }
    if (element.isContentEditable) return "textbox";
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (tag === "img") return "image";
    return "element";
  }

  // A tiny checkbox or radio is easier to spot with its label included.
  function targetRect(element) {
    const rect = element.getBoundingClientRect();
    const isToggle = ["checkbox", "radio"].includes((element.type || "").toLowerCase()) || ["checkbox", "switch", "radio"].includes(element.getAttribute("role"));
    let label = element.labels?.[0];
    // Unlabelled toggles in a list row (e.g. a to-do): the row's text is
    // what the step is about, so highlight the row and keep callouts off it.
    if (!label && isToggle) {
      const row = element.closest("li,tr,[role=row],[role=listitem]");
      const box = row?.getBoundingClientRect();
      if (box && box.height < 90 && box.width < window.innerWidth * 0.95) label = row;
    }
    if (!label || !isToggle) return rect;
    const other = label.getBoundingClientRect();
    if (!other.width || !other.height || Math.abs(other.top - rect.top) > 80) return rect;
    const left = Math.min(rect.left, other.left);
    const top = Math.min(rect.top, other.top);
    const right = Math.max(rect.right, other.right), bottom = Math.max(rect.bottom, other.bottom);
    return { left, top, right, bottom, width: right - left, height: bottom - top };
  }

  function describe(element, type, event) {
    const rect = targetRect(element);
    const width = window.innerWidth || 1;
    const height = window.innerHeight || 1;
    const box = (value, size) => Math.max(0, Math.min(1, value / size));
    const role = roleOf(element);
    const action = {
      type,
      role,
      name: nameOf(element),
      tag: element.tagName.toLowerCase(),
      rect: { x: box(rect.left, width), y: box(rect.top, height), width: box(rect.width, width), height: box(rect.height, height) },
      url: safeUrl(),
      pageTitle: safeTitle(),
      ref: remember(element),
      viewport: { w: width, h: height },
    };
    if (event && Number.isFinite(event.clientX) && (event.clientX || event.clientY)) action.point = { x: box(event.clientX, width), y: box(event.clientY, height) };
    if (role === "checkbox" || role === "switch" || role === "radio" || role === "menuitemcheckbox") action.checked = element.checked === true || element.getAttribute("aria-checked") === "true";
    if (element.tagName === "SELECT") action.value = clean(element.selectedOptions?.[0]?.innerText, 60);
    if (element.closest?.("nav,[role=navigation],[role=menubar]")) action.region = "navigation";
    else if (element.closest?.("[role=dialog],dialog")) action.region = "dialog";
    else if (element.closest?.("[role=menu],[role=listbox]")) action.region = "menu";
    return action;
  }

  // Typing is reported once, in order, before whatever the user does next.
  // The snapshots stay in this page and are only compared, never sent.
  const baseline = new WeakMap();
  const snapshot = (field) => (field.isContentEditable && field.tagName !== "INPUT" && field.tagName !== "TEXTAREA" ? field.innerText : field.value) || "";
  let typingTimer;
  function flushTyping(field = textFieldOf(document.activeElement)) {
    clearTimeout(typingTimer);
    if (!field || !baseline.has(field)) return;
    const now = snapshot(field);
    if (now === baseline.get(field)) return;
    baseline.set(field, now);
    if (!now.trim()) return;
    const action = describe(field, "type");
    const value = typedValue(field);
    if (value) action.value = value;
    notify("page-action", action);
  }
  // A short pause in typing captures the field with its text, before an
  // Enter press or click can navigate away.
  document.addEventListener("input", (event) => {
    const field = textFieldOf(event.target);
    if (!field) return;
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => flushTyping(field), 800);
  }, true);
  document.addEventListener("focusin", (event) => {
    const field = textFieldOf(event.target);
    if (field && !baseline.has(field)) baseline.set(field, snapshot(field));
  }, true);
  document.addEventListener("focusout", (event) => flushTyping(textFieldOf(event.target)), true);

  // pointerdown fires before the page reacts, so the screenshot still shows
  // the element the user is about to press.
  let lastPointer = 0;
  let lastEnter = 0;
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !event.isPrimary) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    flushTyping();
    if (textFieldOf(target) || target.closest("select")) return;
    let element = target.closest(INTERACTIVE) || target;
    // Clicking a label really operates its control (e.g. a checkbox).
    if (element.tagName === "LABEL" && element.control) {
      if (textFieldOf(element.control) || element.control.tagName === "SELECT") return;
      element = element.control;
    }
    lastPointer = Date.now();
    notify("page-action", describe(element, "click", event));
  }, true);

  // Keyboard activation (Enter/Space on a focused control) produces a click
  // with no pointer. Skip clicks the browser synthesizes for a pointer press
  // on a label or for Enter in a form field.
  document.addEventListener("click", (event) => {
    if (event.detail !== 0 || Date.now() - lastPointer < 400 || Date.now() - lastEnter < 600) return;
    const target = event.target instanceof Element ? event.target.closest(INTERACTIVE) || event.target : null;
    if (!target || textFieldOf(target)) return;
    const action = describe(target, "click");
    // This click has already toggled the control; report the state before it.
    if ("checked" in action && action.role !== "radio") action.checked = !action.checked;
    notify("page-action", action);
  }, true);

  document.addEventListener("change", (event) => {
    const element = event.target;
    if (!(element instanceof Element)) return;
    if (element.tagName === "SELECT") notify("page-action", describe(element, "select"));
    else if (element.tagName === "INPUT" && (element.type || "").toLowerCase() === "file") notify("page-action", describe(element, "upload"));
  }, true);

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing || event.shiftKey) return;
    const field = textFieldOf(event.target);
    if (!field || field.tagName !== "INPUT") return;
    flushTyping(field);
    lastEnter = Date.now();
    notify("page-action", describe(field, "submit"));
  }, true);

  notify("page-ready", { type: "navigate", url: safeUrl(), pageTitle: safeTitle() });
})();
