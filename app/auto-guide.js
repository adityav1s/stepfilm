// Turns recorded browser actions into a finished, editable tutorial without
// any AI service: step instructions, descriptions, a highlight on the element
// that was used, and a zoom toward it for the video export.
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StepfilmAutoGuide = api;
})(typeof self === "object" ? self : globalThis, function () {
  const DEFAULT_TITLES = new Set(["", "Browser workflow", "Captured workflow", "Untitled guide"]);
  const clamp = (value, min, max) => Math.max(min, Math.min(max, Number(value) || 0));

  function quoteName(value, max = 60) {
    let text = String(value || "").replace(/\s+/g, " ").replace(/["“”<>`*_\[\]]/g, "").trim();
    if (text.length > max) text = `${text.slice(0, max - 1).trimEnd()}…`;
    return text;
  }

  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return ""; }
  }

  // "Inbox (3) - Gmail" -> "Gmail"; "Settings · Acme" -> "Acme".
  function siteName(action) {
    const title = quoteName(action?.pageTitle, 120);
    const parts = title.split(/\s+[-|·—–]\s+/).map((part) => part.trim()).filter(Boolean);
    if (parts.length > 1) return parts[parts.length - 1].slice(0, 40);
    return hostOf(action?.url) || title.slice(0, 40);
  }

  function pageName(action) {
    const title = quoteName(action?.pageTitle, 120);
    const parts = title.split(/\s+[-|·—–]\s+/).map((part) => part.trim()).filter(Boolean);
    return (parts[0] || hostOf(action?.url) || "").slice(0, 60);
  }

  function formatTime(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function stepCopy(action, step = {}) {
    const copy = writeStep(action, step);
    // Descriptions add where the step happens instead of repeating it.
    if (action && !["navigate", "scene"].includes(action.type)) copy.description = contextOf(action);
    return copy;
  }

  function contextOf(action) {
    const page = pageName(action);
    if (action.region === "dialog") return page ? `In the dialog on the ${page} page.` : "In the dialog.";
    if (action.region === "menu") return page ? `In the open menu on the ${page} page.` : "In the open menu.";
    if (action.region === "navigation") return page ? `In the navigation on the ${page} page.` : "In the navigation.";
    return page ? `On the ${page} page.` : "";
  }

  function placeOf(url) {
    try {
      const parsed = new URL(url);
      const path = parsed.pathname.replace(/\/+$/, "");
      const place = `${parsed.hostname.replace(/^www\./, "")}${path.length > 1 && path.length <= 30 ? path : ""}`;
      return place;
    } catch (_) { return ""; }
  }

  function writeStep(action, step = {}) {
    const name = quoteName(action?.name);
    const q = name ? `“${name}”` : "";
    const page = pageName(action);
    const onPage = page ? ` on the ${page} page` : "";
    const role = action?.role || "element";
    switch (action?.type) {
      case "navigate": {
        const where = placeOf(action.url);
        if (action.start) return { text: `Go to ${where || page || "the starting page"}`, description: page ? `Start on the ${page} page.` : "" };
        return { text: page ? `The ${page} page opens` : "The next page opens", description: "" };
      }
      case "type": {
        const search = role === "searchbox" || /search/i.test(name);
        if (search) return { text: q ? `Type your search in ${q}` : "Type your search", description: `Enter a search term${onPage}.` };
        return { text: q ? `Fill in the ${q} field` : "Fill in the highlighted field", description: q ? `Enter the required information in the ${q} field${onPage}.` : `Enter the required information${onPage}.` };
      }
      case "select": {
        const value = quoteName(action.value, 40);
        if (value) return { text: q ? `Choose “${value}” from ${q}` : `Choose “${value}”`, description: `Pick “${value}” from the ${name ? `${name} ` : ""}menu${onPage}.` };
        return { text: q ? `Choose an option from ${q}` : "Choose an option", description: `Pick the option you need${onPage}.` };
      }
      case "submit":
        return { text: q ? `Press Enter in ${q}` : "Press Enter to continue", description: q ? `Submit the ${q} field by pressing Enter.` : "Press Enter to submit." };
      case "upload":
        return { text: q ? `Upload a file with ${q}` : "Upload your file", description: `Choose the file to upload${onPage}.` };
      case "scene":
        return { text: "", description: "" };
      case "click":
      default: {
        if (!action) return { text: "", description: "" };
        const inDialog = action.region === "dialog" ? " in the dialog" : "";
        if (role === "checkbox" || role === "switch" || role === "menuitemcheckbox") {
          // The click is recorded before the page toggles the control.
          const turningOn = action.checked !== true;
          const verb = role === "switch" ? (turningOn ? "Turn on" : "Turn off") : (turningOn ? "Check" : "Uncheck");
          return { text: q ? `${verb} ${q}` : `${verb} the highlighted option`, description: `${verb} ${q || "the option"}${inDialog || onPage}.` };
        }
        if (role === "radio" || role === "menuitemradio" || role === "option") return { text: q ? `Select ${q}` : "Select the highlighted option", description: `Select ${q || "this option"}${inDialog || onPage}.` };
        if (role === "tab") return { text: q ? `Open the ${q} tab` : "Open the highlighted tab", description: `Switch to the ${name || "highlighted"} tab${onPage}.` };
        if (role === "menuitem") return { text: q ? `Choose ${q}` : "Choose the highlighted menu item", description: `Choose ${q || "this item"} from the menu${onPage}.` };
        if (role === "file") return { text: q ? `Click ${q} to choose a file` : "Click to choose a file", description: `Open the file picker${onPage}.` };
        if (role === "textbox" || role === "combobox" || role === "searchbox") return { text: q ? `Click the ${q} field` : "Click the highlighted field", description: `Place the cursor in ${q ? `the ${q} field` : "the field"}${onPage}.` };
        if (role === "link") {
          if (action.region === "navigation") return { text: q ? `Open ${q} from the menu` : "Open the highlighted menu link", description: `Use the navigation to open ${q || "this section"}.` };
          return { text: q ? `Click the ${q} link` : "Click the highlighted link", description: `Follow the ${q ? `${q} ` : ""}link${onPage}.` };
        }
        if (role === "button") return { text: q ? `Click ${q}` : "Click the highlighted button", description: `Click the ${q ? `${q} ` : ""}button${inDialog || onPage}.` };
        return { text: q ? `Click ${q}` : "Click the highlighted area", description: q ? `Click ${q}${inDialog || onPage}.` : `Click the highlighted area${onPage}.` };
      }
    }
  }

  function highlightFor(action) {
    const rect = action?.rect;
    if (!rect || !(rect.width > 0) || !(rect.height > 0) || action.type === "navigate" || action.type === "scene") return null;
    // Ignore boxes that cover most of the page (e.g. a click on the body).
    if (rect.width > 0.85 && rect.height > 0.6) {
      if (!action.point) return null;
      return { x: clamp(action.point.x - 0.04, 0, 0.92), y: clamp(action.point.y - 0.04, 0, 0.92), width: 0.08, height: 0.08 };
    }
    const pad = 0.008;
    const width = clamp(rect.width + pad * 2, 0.03, 1);
    const height = clamp(rect.height + pad * 2, 0.03, 1);
    return { x: clamp(rect.x - pad, 0, 1 - width), y: clamp(rect.y - pad, 0, 1 - height), width, height };
  }

  // Click point: where the pointer went down, or the middle of the target.
  function clickPointFor(action, box) {
    if (action?.point && action.type === "click") return { x: clamp(action.point.x, 0, 1), y: clamp(action.point.y, 0, 1) };
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  }

  // A callout bubble beside the target: right if there is room, else left,
  // else below, else above. Sizes are fractions of the screenshot.
  function calloutBoxFor(box) {
    const width = 0.26, height = 0.085, gap = 0.015;
    const middleY = clamp(box.y + box.height / 2 - height / 2, 0.01, 0.99 - height);
    if (box.x + box.width + gap + width <= 0.99) return { x: box.x + box.width + gap, y: middleY, width, height };
    if (box.x - gap - width >= 0.01) return { x: box.x - gap - width, y: middleY, width, height };
    const x = clamp(box.x + box.width / 2 - width / 2, 0.01, 0.99 - width);
    if (box.y + box.height + gap + height <= 0.99) return { x, y: box.y + box.height + gap, width, height };
    return { x, y: clamp(box.y - gap - height, 0.01, 0.99 - height), width, height };
  }

  // Guidde-style step annotations: spotlight on the element, a click marker
  // where the user pressed, and a callout that repeats the instruction.
  // "click" annotations are circles centred in their box; renderers draw a
  // ring with radius ~1.8% of the image width.
  function actionAnnotations(action, box, text) {
    const point = clickPointFor(action, box);
    const items = [{ id: makeId(), type: "highlight", ...box, text: "", generated: true, origin: "action" }];
    if (["click", "submit", "upload"].includes(action.type)) items.push({ id: makeId(), type: "click", x: clamp(point.x - 0.015, 0, 0.97), y: clamp(point.y - 0.015, 0, 0.97), width: 0.03, height: 0.03, text: "", generated: true, origin: "action" });
    if (String(text || "").trim()) items.push({ id: makeId(), type: "callout", ...calloutBoxFor(box), text: String(text).slice(0, 140), generated: true, origin: "action" });
    return items;
  }

  function introFor(title) {
    const goal = String(title || "").trim();
    if (/^how to /i.test(goal)) return `In this guide, you'll learn ${goal.charAt(0).toLowerCase()}${goal.slice(1)}.`;
    return goal ? `This guide walks you through ${goal}.` : "This guide walks you through the workflow step by step.";
  }

  // Zoom toward small targets for the video; keep the frame inside the image.
  function focusFor(box) {
    if (!box) return { x: 0.5, y: 0.5, scale: 1 };
    const size = Math.max(box.width, box.height);
    const scale = size < 0.12 ? 1.6 : size < 0.3 ? 1.3 : 1;
    if (scale === 1) return { x: 0.5, y: 0.5, scale: 1 };
    const half = 0.5 / scale;
    return { x: clamp(box.x + box.width / 2, half, 1 - half), y: clamp(box.y + box.height / 2, half, 1 - half), scale };
  }

  function guideTitle(steps) {
    const actions = steps.map((step) => step.action).filter(Boolean);
    const first = actions.find((action) => action.pageTitle || action.url);
    const site = first ? siteName(first) : "";
    // The last named button usually completes the task ("Create project").
    const generic = /^(go|apply|confirm|ok|okay|cancel|close|next|back|previous|continue|done|yes|no|got it|dismiss|skip|menu|more|search|submit|x|×|open menu|accept|accept all|allow|later|not now)$/i;
    const finale = [...actions].reverse().find((action) => {
      const name = quoteName(action.name, 40);
      return action.type === "click" && action.role === "button" && name && !generic.test(name) && name.split(" ").length <= 5;
    });
    if (finale) {
      const goal = quoteName(finale.name, 40);
      let phrase = goal.charAt(0).toLowerCase() + goal.slice(1);
      // "Create project" -> "create a project"; leave "Create projects" or "Add to cart" alone.
      const words = phrase.split(" ");
      if (words.length === 2 && /^(create|add|invite|schedule|start|book|open|upload|build|make|set up|share|publish|send|new)$/i.test(words[0]) && !/s$/i.test(words[1]) && !/^(a|an|the|your|my)$/i.test(words[1])) {
        phrase = `${words[0] === "new" ? "create" : words[0]} ${/^[aeiou]/i.test(words[1]) ? "an" : "a"} ${words[1]}`;
      }
      return site ? `How to ${phrase} in ${site}` : `How to ${phrase}`;
    }
    return site ? `${site} walkthrough` : "";
  }

  function makeId() { return `annotation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`; }

  // Fills in steps that have no authored text yet. Steps the user already
  // wrote are left alone, so this is safe to run again.
  function build(capture) {
    if (!capture || !Array.isArray(capture.steps)) return capture;
    let written = 0;
    let highlighted = 0;
    let scenes = 0;
    const steps = capture.steps.map((original) => {
      const step = { ...original };
      const action = step.action;
      if (!action || step.kind === "video") return step;
      if (action.type === "scene") scenes++;
      const copy = stepCopy(action, step);
      if (!String(step.text || "").trim() && copy.text) { step.text = copy.text; written++; }
      if (!String(step.description || "").trim() && copy.description) step.description = copy.description;
      const box = highlightFor(action);
      const annotations = Array.isArray(step.annotations) ? step.annotations : [];
      if (box && !annotations.some((annotation) => annotation.origin === "action")) {
        step.annotations = [...annotations, ...actionAnnotations(action, box, step.text)];
        highlighted++;
      }
      if (box && (!step.focus || Number(step.focus.scale) === 1)) step.focus = focusFor(box);
      step.autoGenerated = true;
      return step;
    });
    const title = DEFAULT_TITLES.has(String(capture.title || "").trim()) ? (guideTitle(steps) || capture.title) : capture.title;
    const intro = String(capture.intro || "").trim() ? capture.intro : introFor(title);
    const outro = String(capture.outro || "").trim() ? capture.outro : "That's it — you've completed every step.";
    return { ...capture, title, intro, outro, steps, autoGuide: { written, highlighted, scenes, total: steps.length } };
  }

  return { build, stepCopy, highlightFor, focusFor, guideTitle, siteName, pageName, formatTime, actionAnnotations, calloutBoxFor, introFor };
});
