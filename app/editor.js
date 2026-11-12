const get = (id) => document.getElementById(id);
const ui = Object.fromEntries(
  ["voice", "saveState", "title", "instructions", "open", "openProject", "file", "projectFile", "export", "download", "saveProject",
   "status", "workspace", "count", "steps", "image", "sceneTitle", "narration", "charCount", "preview", "redact", "marker", "callout", "highlight", "arrow", "blur", "annotationHint", "annotationLayer", "annotationText", "annotationApply", "annotationDelete", "annotationUndo", "annotationRedo", "redactionHint", "redactionCanvas", "result", "video", "playbackStatus", "guidePreview", "copyGuide", "moveUp", "moveDown", "duplicate", "remove"]
    .map((id) => [id, get(id)]),
);
let guide;
let selected = 0;
let videoUrl;
let previewUrl;
let previewAudio;
let previewRequest;
const STORAGE_KEY = "stepfilm-draft";
let redacting = false;
let redactionStart;
let annotationMode;
let annotationStart;
let highlightJustBaked = false;
let selectedAnnotation;
let annotationHistory = [];
let annotationFuture = [];

function status(message, error = false) {
  ui.status.textContent = message;
  ui.status.classList.toggle("error", error);
}

async function api(path, body, type = "application/json") {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": type },
    body: type === "application/json" ? JSON.stringify(body) : body,
  };
  const response = await fetch(path, options);
  if (!response.ok) {
    const details = await response.json().catch(() => ({}));
    throw new Error(details.error || "The local editor could not complete the request.");
  }
  return response;
}

function clearVideo() {
  if (!videoUrl) return;
  ui.video.pause();
  ui.video.removeAttribute("src");
  ui.video.load();
  ui.video.onloadedmetadata = null;
  ui.video.onerror = null;
  ui.playbackStatus.textContent = "";
  ui.playbackStatus.classList.remove("error");
  ui.download.removeAttribute("href");
  URL.revokeObjectURL(videoUrl);
  videoUrl = undefined;
  ui.result.hidden = true;
  ui.download.hidden = true;
}

function stopPreview() {
  previewRequest = undefined;
  previewAudio?.pause();
  previewAudio = undefined;
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = undefined;
  ui.preview.textContent = "Play narration";
}

function cancelTools() {
  annotationMode = undefined;
  annotationStart = undefined;
  redacting = false;
  ui.redact.classList.remove("active");
  ui.redact.textContent = "Redact pixels";
  ui.redactionHint.hidden = true;
  ui.redactionCanvas.style.pointerEvents = "none";
  ui.redactionCanvas.classList.remove("is-active");
  ui.image.style.cursor = "default";
  for (const button of [ui.marker, ui.callout, ui.highlight, ui.arrow, ui.blur]) button.classList.remove("active");
  ui.annotationHint.textContent = "Choose a tool, then place it on the screenshot.";
}

function currentAnnotations() { return guide?.steps[selected]?.annotations || []; }
function snapshotAnnotations() { return JSON.parse(JSON.stringify(currentAnnotations())); }
function recordAnnotationChange() { annotationHistory.push(snapshotAnnotations()); if (annotationHistory.length > 30) annotationHistory.shift(); annotationFuture = []; }
function restoreAnnotations(value) { guide.steps[selected].annotations = JSON.parse(JSON.stringify(value)); selectedAnnotation = undefined; renderAnnotations(); persist(); clearVideo(); updateAnnotationControls(); }
function updateAnnotationControls() {
  const annotation = currentAnnotations().find((item) => item.id === selectedAnnotation);
  ui.annotationText.disabled = !annotation;
  ui.annotationText.placeholder = annotation ? "Edit selected marker or callout text" : "Select an annotation to edit its text";
  ui.annotationText.value = annotation?.text || "";
  ui.annotationDelete.disabled = !annotation;
  ui.annotationApply.disabled = !annotation;
  ui.annotationUndo.disabled = !annotationHistory.length;
  ui.annotationRedo.disabled = !annotationFuture.length;
}
function renderAnnotations() {
  ui.annotationLayer.replaceChildren();
  for (const annotation of currentAnnotations()) {
    const object = document.createElement("div");
    object.className = `annotation-object annotation-${annotation.type}${annotation.id === selectedAnnotation ? " selected" : ""}`;
    object.dataset.annotationId = annotation.id;
    object.style.left = `${annotation.x * 100}%`; object.style.top = `${annotation.y * 100}%`;
    object.style.width = `${annotation.width * 100}%`; object.style.height = `${annotation.height * 100}%`;
    if (annotation.type === "arrow") {
      const angle = Math.atan2(annotation.height, annotation.width) * 180 / Math.PI;
      object.style.transform = `rotate(${angle}deg)`;
      object.style.width = `${Math.max(annotation.width, 0.05) * 100}%`;
    } else if (annotation.type === "marker") object.textContent = annotation.text || "1";
    else if (annotation.type === "callout") object.textContent = annotation.text || "Explain this";
    const handle = document.createElement("span"); handle.className = "resize-handle"; handle.title = "Resize annotation"; object.append(handle);
    object.addEventListener("pointerdown", (event) => beginAnnotationDrag(event, annotation.id, event.target === handle));
    ui.annotationLayer.append(object);
  }
  updateAnnotationControls();
}
function beginAnnotationDrag(event, id, resizing) {
  event.preventDefault(); event.stopPropagation(); selectedAnnotation = id; renderAnnotations();
  const start = { x: event.clientX, y: event.clientY, annotations: snapshotAnnotations() };
  const layer = ui.annotationLayer.getBoundingClientRect();
  const move = (moveEvent) => {
    const annotation = currentAnnotations().find((item) => item.id === id); if (!annotation) return;
    const dx = (moveEvent.clientX - start.x) / layer.width, dy = (moveEvent.clientY - start.y) / layer.height;
    if (resizing) { annotation.width = Math.max(0.02, Math.min(1 - annotation.x, start.annotations.find((item) => item.id === id).width + dx)); annotation.height = Math.max(0.02, Math.min(1 - annotation.y, start.annotations.find((item) => item.id === id).height + dy)); }
    else { annotation.x = Math.max(0, Math.min(1 - annotation.width, start.annotations.find((item) => item.id === id).x + dx)); annotation.y = Math.max(0, Math.min(1 - annotation.height, start.annotations.find((item) => item.id === id).y + dy)); }
    renderAnnotations();
  };
  const stop = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", stop); annotationHistory.push(start.annotations); annotationFuture = []; persist(); clearVideo(); updateAnnotationControls(); };
  document.addEventListener("pointermove", move); document.addEventListener("pointerup", stop, { once: true });
}

function stepLabel(index) {
  return guide.steps[index].text.trim() || "Silent step";
}

function select(index) {
  stopPreview();
  cancelTools();
  annotationHistory = [];
  annotationFuture = [];
  selectedAnnotation = undefined;
  selected = index;
  const step = guide.steps[index];
  ui.image.src = step.sourceImage || step.image;
  ui.image.alt = `Screenshot of step ${index + 1}`;
  ui.sceneTitle.textContent = `Step ${String(index + 1).padStart(2, "0")}`;
  ui.narration.value = step.text;
  ui.charCount.textContent = `${step.text.length} / 400`;
  ui.moveUp.disabled = index === 0;
  ui.moveDown.disabled = index === guide.steps.length - 1;
  ui.remove.disabled = guide.steps.length === 1;
  ui.saveProject.hidden = false;
  ui.redactionCanvas.width = 0;
  ui.redactionCanvas.height = 0;
  renderAnnotations();
  for (const [position, item] of [...ui.steps.children].entries()) {
    item.firstChild.setAttribute("aria-current", position === index ? "step" : "false");
  }
}

function loadGuide(imported) {
  stopPreview();
  clearVideo();
  if (!globalThis.StepfilmAnnotations) throw new Error("Stepfilm annotation tools failed to load. Reload the editor after restarting Stepfilm.");
  guide = StepfilmAnnotations.normalizeGuide(imported);
  selectedAnnotation = undefined;
  annotationHistory = [];
  annotationFuture = [];
  ui.title.textContent = guide.title;
  ui.instructions.hidden = true;
  const quickStart = document.getElementById("quickStart");
  if (quickStart) quickStart.hidden = true;
  ui.workspace.hidden = false;
  ui.export.hidden = false;
  ui.saveProject.hidden = false;
  ui.count.textContent = guide.steps.length;
  ui.steps.replaceChildren();
  guide.steps.forEach((_, index) => {
    const row = document.createElement("li");
    const button = document.createElement("button");
    const number = document.createElement("span");
    const label = document.createElement("span");
    number.className = "step-number";
    number.textContent = String(index + 1).padStart(2, "0");
    label.className = "step-text";
    label.textContent = stepLabel(index);
    button.append(number, label);
    button.addEventListener("click", () => select(index));
    row.append(button);
    ui.steps.append(row);
  });
  select(0);
  persist();
}

function showEmptyState() {
  stopPreview();
  clearVideo();
  guide = undefined;
  selected = 0;
  ui.title.textContent = "Turn browser steps into a tutorial.";
  ui.instructions.hidden = false;
  const quickStart = document.getElementById("quickStart");
  if (quickStart) quickStart.hidden = false;
  ui.workspace.hidden = true;
  ui.export.hidden = true;
  ui.saveProject.hidden = true;
  ui.steps.replaceChildren();
  ui.count.textContent = "";
}

function persist() {
  if (!guide) return;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(guide)); ui.saveState.textContent = "Saved locally"; }
  catch (_) { ui.saveState.textContent = "Draft loaded"; }
}

async function renderGuidePreview() {
  ui.guidePreview.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = guide.title;
  ui.guidePreview.append(heading);
  const images = await Promise.all(guide.steps.map((step) => flattenStep(step)));
  guide.steps.forEach((step, index) => {
    const item = document.createElement("article");
    const number = document.createElement("span");
    number.className = "preview-number";
    number.textContent = String(index + 1).padStart(2, "0");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = stepLabel(index);
    const image = document.createElement("img");
    image.src = images[index];
    image.alt = `Step ${index + 1}`;
    copy.append(title, image);
    const text = document.createElement("p");
    text.textContent = step.text.trim() || "No written instruction yet — add one in the editor so viewers know what to do.";
    if (!step.text.trim()) text.className = "missing-instruction";
    copy.append(text);
    item.append(number, copy);
    ui.guidePreview.append(item);
  });
}

ui.open.addEventListener("click", () => ui.file.click());
ui.openProject.addEventListener("click", () => ui.projectFile.click());
ui.projectFile.addEventListener("change", async () => {
  const [file] = ui.projectFile.files; ui.projectFile.value = ""; if (!file) return;
  try { loadGuide(JSON.parse(await file.text())); status("Project opened."); }
  catch (_) { status("That project file is not valid Stepfilm JSON.", true); }
});
ui.saveProject.addEventListener("click", () => {
  if (!guide) return;
  const blob = new Blob([JSON.stringify(guide, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
  anchor.href = url; anchor.download = `${guide.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "stepfilm-project"}.stepfilm.json`;
  anchor.click(); URL.revokeObjectURL(url); status("Project saved.");
});
ui.file.addEventListener("change", async () => {
  const [file] = ui.file.files;
  ui.file.value = "";
  if (!file) return;
  ui.open.disabled = true;
  ui.export.disabled = true;
  status("Opening guide…");
  try {
    if (file.size > 50 * 1024 * 1024) throw new Error("Choose an HTML guide under 50 MB.");
    const response = await api("/api/import", file, "text/plain");
    loadGuide(await response.json());
    status("");
  } catch (error) {
    status(error.message, true);
  } finally {
    ui.open.disabled = false;
    ui.export.disabled = false;
  }
});

ui.narration.addEventListener("input", () => {
  stopPreview();
  clearVideo();
  guide.steps[selected].text = ui.narration.value;
  ui.steps.children[selected].querySelector(".step-text").textContent = stepLabel(selected);
  ui.charCount.textContent = `${ui.narration.value.length} / 400`;
  persist();
});

function reorder(nextIndex) {
  if (nextIndex < 0 || nextIndex >= guide.steps.length) return;
  [guide.steps[selected], guide.steps[nextIndex]] = [guide.steps[nextIndex], guide.steps[selected]];
  loadGuide(guide);
  select(nextIndex);
}
ui.moveUp.addEventListener("click", () => reorder(selected - 1));
ui.moveDown.addEventListener("click", () => reorder(selected + 1));
ui.duplicate.addEventListener("click", () => { guide.steps.splice(selected + 1, 0, { ...guide.steps[selected] }); loadGuide(guide); select(selected + 1); });
ui.remove.addEventListener("click", () => { if (guide.steps.length === 1) return; guide.steps.splice(selected, 1); loadGuide(guide); select(Math.min(selected, guide.steps.length - 1)); });

ui.preview.addEventListener("click", async () => {
  if (previewRequest) {
    stopPreview();
    return;
  }
  const text = ui.narration.value.trim();
  if (!text) {
    status("Enter narration to hear a preview.", true);
    return;
  }
  const request = Symbol();
  previewRequest = request;
  ui.preview.textContent = "Stop preview";
  try {
    const response = await api("/api/preview", { text });
    const audio = await response.blob();
    if (previewRequest !== request) return;
    previewUrl = URL.createObjectURL(audio);
    previewAudio = new Audio(previewUrl);
    previewAudio.addEventListener("ended", stopPreview, { once: true });
    await previewAudio.play();
  } catch (error) {
    if (previewRequest !== request) return;
    stopPreview();
    status(error.message, true);
  }
});

function loadImage(source) {
  return new Promise((resolve, reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = reject; image.src = source; });
}
async function flattenStep(step) {
  const source = await loadImage(step.sourceImage || step.image);
  const canvas = document.createElement("canvas"); canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
  const context = canvas.getContext("2d"); context.drawImage(source, 0, 0);
  const scale = Math.max(1, canvas.width / 1280);
  for (const annotation of step.annotations || []) {
    const x = annotation.x * canvas.width, y = annotation.y * canvas.height, width = annotation.width * canvas.width, height = annotation.height * canvas.height;
    if (annotation.type === "blur") {
      const crop = document.createElement("canvas"); crop.width = Math.max(1, Math.round(width)); crop.height = Math.max(1, Math.round(height));
      const cropContext = crop.getContext("2d"); cropContext.filter = `blur(${Math.max(10, 18 * scale)}px)`; cropContext.drawImage(canvas, x, y, width, height, 0, 0, crop.width, crop.height);
      context.drawImage(crop, x, y, width, height); context.strokeStyle = "rgba(255,173,165,.9)"; context.lineWidth = 4 * scale; context.strokeRect(x, y, width, height); continue;
    }
    if (annotation.type === "marker") { context.fillStyle = "#c4f48f"; context.strokeStyle = "#15200e"; context.lineWidth = 5 * scale; context.beginPath(); context.arc(x + width / 2, y + height / 2, Math.max(12, Math.min(width, height) / 2), 0, Math.PI * 2); context.fill(); context.stroke(); context.fillStyle = "#15200e"; context.font = `800 ${28 * scale}px system-ui`; context.textAlign = "center"; context.textBaseline = "middle"; context.fillText((annotation.text || "1").slice(0, 3), x + width / 2, y + height / 2); continue; }
    if (annotation.type === "callout") { context.fillStyle = "rgba(196,244,143,.96)"; context.strokeStyle = "#15200e"; context.lineWidth = 3 * scale; context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height); context.fillStyle = "#15200e"; context.font = `700 ${22 * scale}px system-ui`; context.textAlign = "left"; context.textBaseline = "middle"; context.fillText((annotation.text || "Explain this").slice(0, 72), x + 18 * scale, y + height / 2, Math.max(20, width - 36 * scale)); continue; }
    if (annotation.type === "arrow") { context.strokeStyle = "#c4f48f"; context.fillStyle = "#c4f48f"; context.lineWidth = 7 * scale; context.beginPath(); context.moveTo(x, y); context.lineTo(x + width, y + height); context.stroke(); const angle = Math.atan2(height, width); context.beginPath(); context.moveTo(x + width, y + height); context.lineTo(x + width - 22 * scale * Math.cos(angle - Math.PI / 6), y + height - 22 * scale * Math.sin(angle - Math.PI / 6)); context.lineTo(x + width - 22 * scale * Math.cos(angle + Math.PI / 6), y + height - 22 * scale * Math.sin(angle + Math.PI / 6)); context.closePath(); context.fill(); continue; }
    if (annotation.type === "highlight") { context.fillStyle = "rgba(196,244,143,.22)"; context.strokeStyle = "#c4f48f"; context.lineWidth = 5 * scale; context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height); }
  }
  return canvas.toDataURL("image/png");
}
async function exportSteps() { return Promise.all(guide.steps.map(async (step) => ({ ...step, image: await flattenStep(step), annotations: [] }))); }

ui.export.addEventListener("click", async () => {
  stopPreview();
  clearVideo();
  ui.export.disabled = true;
  ui.open.disabled = true;
  ui.narration.disabled = true;
  status("Rendering video on this computer…");
  try {
    const response = await api("/api/export", { steps: await exportSteps() });
    const videoBytes = await response.arrayBuffer();
    videoUrl = URL.createObjectURL(new Blob([videoBytes], { type: "video/mp4" }));
    const name = guide.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 80);
    ui.download.href = videoUrl;
    ui.download.download = `${name || "tutorial"}.mp4`;
    ui.download.hidden = false;
    ui.download.textContent = "Download MP4 again";
    // Reuse the response blob for playback. The server intentionally streams
    // exports as a one-shot POST response and does not expose a stale
    // `/api/export/latest` file endpoint.
    ui.video.src = videoUrl;
    ui.video.type = "video/mp4";
    ui.result.hidden = false;
    ui.playbackStatus.textContent = "Loading video preview…";
    ui.playbackStatus.classList.remove("error");
    ui.video.onloadedmetadata = () => {
      const duration = Number.isFinite(ui.video.duration) ? `${Math.round(ui.video.duration * 10) / 10}s` : "ready";
      ui.playbackStatus.textContent = `Preview ready · ${duration}`;
      ui.playbackStatus.classList.remove("error");
    };
    ui.video.onerror = () => {
      ui.playbackStatus.textContent = "This browser could not play the preview. The MP4 is still available via Download again; open it in QuickTime or another player.";
      ui.playbackStatus.classList.add("error");
      ui.download.hidden = false;
    };
    ui.video.load();
    await renderGuidePreview();
    ui.download.click();
    status("Your MP4 is ready. The download is available below.");
  } catch (error) {
    status(error.message, true);
  } finally {
    ui.export.disabled = false;
    ui.open.disabled = false;
    ui.narration.disabled = false;
  }
});

ui.copyGuide.addEventListener("click", async () => {
  const text = [guide.title, ...guide.steps.map((step, index) => `${index + 1}. ${step.text.trim() || "Silent step"}`)].join("\n");
  try { await navigator.clipboard.writeText(text); ui.copyGuide.textContent = "Copied"; setTimeout(() => { ui.copyGuide.textContent = "Copy guide text"; }, 1600); }
  catch (_) { status("Clipboard access is unavailable in this browser.", true); }
});

function imagePoint(event) {
  const rect = ui.image.getBoundingClientRect();
  return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
}
function addAnnotation(type, area) {
  recordAnnotationChange();
  const text = type === "marker" ? String(currentAnnotations().length + 1) : type === "callout" ? (guide.steps[selected].text.trim() || "Explain this") : "";
  const annotation = StepfilmAnnotations.create(type, area.x, area.y, area.width, area.height, text);
  guide.steps[selected].annotations.push(annotation); selectedAnnotation = annotation.id;
  persist(); clearVideo(); cancelTools(); renderAnnotations(); status(`${type[0].toUpperCase() + type.slice(1)} added. Drag it to the exact target; use the resize handle to size it.`);
}
function chooseAnnotation(kind) {
  cancelTools();
  annotationMode = kind;
  const button = ({ marker: ui.marker, callout: ui.callout, highlight: ui.highlight, arrow: ui.arrow, blur: ui.blur })[kind];
  button.classList.add("active");
  ui.image.style.cursor = kind === "highlight" || kind === "blur" ? "crosshair" : "copy";
  ui.annotationHint.textContent = kind === "highlight" || kind === "blur" ? "Drag over the target area. Press Esc to cancel." : "Click the target, then drag to move or resize it. Press Esc to cancel.";
}
ui.marker.addEventListener("click", () => chooseAnnotation("marker"));
ui.callout.addEventListener("click", () => chooseAnnotation("callout"));
ui.highlight.addEventListener("click", () => chooseAnnotation("highlight"));
ui.arrow.addEventListener("click", () => chooseAnnotation("arrow"));
ui.blur.addEventListener("click", () => chooseAnnotation("blur"));

ui.image.addEventListener("pointerdown", (event) => {
  if (!annotationMode) return;
  annotationStart = imagePoint(event);
  ui.image.setPointerCapture(event.pointerId);
});
ui.image.addEventListener("pointerup", (event) => {
  if (!annotationMode || !annotationStart) return;
  const end = imagePoint(event);
  const kind = annotationMode;
  const start = annotationStart;
  annotationStart = undefined;
  if (kind === "highlight" || kind === "blur") {
    const area = { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) };
    if (area.width < 0.03 || area.height < 0.03) { status("Drag across a larger target area.", true); return; }
    highlightJustBaked = true;
    setTimeout(() => { highlightJustBaked = false; }, 0);
    addAnnotation(kind, area);
  } else {
    if (kind === "arrow") addAnnotation(kind, { x: Math.max(0, end.x - 0.22), y: end.y, width: 0.22, height: 0.01 });
    else if (kind === "callout") addAnnotation(kind, { x: Math.max(0, Math.min(0.42, end.x - 0.29)), y: Math.max(0, Math.min(0.88, end.y - 0.06)), width: 0.58, height: 0.12 });
    else addAnnotation(kind, { x: end.x - 0.025, y: end.y - 0.025, width: 0.05, height: 0.05 });
  }
});
ui.image.addEventListener("click", () => {
  if (annotationMode !== "highlight" || highlightJustBaked) return;
  addAnnotation("highlight", { x: 0.36, y: 0.34, width: 0.28, height: 0.22 });
});

ui.annotationApply.addEventListener("click", () => {
  const annotation = currentAnnotations().find((item) => item.id === selectedAnnotation); if (!annotation) return;
  recordAnnotationChange(); annotation.text = ui.annotationText.value.slice(0, 140); persist(); renderAnnotations(); clearVideo(); status("Annotation text updated.");
});
ui.annotationDelete.addEventListener("click", () => {
  if (!selectedAnnotation) return;
  recordAnnotationChange(); guide.steps[selected].annotations = currentAnnotations().filter((item) => item.id !== selectedAnnotation); selectedAnnotation = undefined; persist(); renderAnnotations(); clearVideo(); status("Annotation deleted.");
});
ui.annotationUndo.addEventListener("click", () => {
  if (!annotationHistory.length) return;
  annotationFuture.push(snapshotAnnotations()); restoreAnnotations(annotationHistory.pop()); status("Annotation change undone.");
});
ui.annotationRedo.addEventListener("click", () => {
  if (!annotationFuture.length) return;
  annotationHistory.push(snapshotAnnotations()); restoreAnnotations(annotationFuture.pop()); status("Annotation change redone.");
});

ui.redact.addEventListener("click", () => { if (guide) chooseAnnotation("blur"); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") cancelTools(); });
ui.redactionCanvas.addEventListener("pointerdown", (event) => {
  if (!redacting) return;
  const rect = ui.redactionCanvas.getBoundingClientRect();
  redactionStart = [event.clientX - rect.left, event.clientY - rect.top];
  ui.redactionCanvas.setPointerCapture(event.pointerId);
});
ui.redactionCanvas.addEventListener("pointerup", (event) => {
  if (!redacting || !redactionStart) return;
  const rect = ui.redactionCanvas.getBoundingClientRect();
  const end = [event.clientX - rect.left, event.clientY - rect.top];
  const x = Math.max(0, Math.min(redactionStart[0], end[0]));
  const y = Math.max(0, Math.min(redactionStart[1], end[1]));
  const width = Math.min(rect.width - x, Math.abs(end[0] - redactionStart[0]));
  const height = Math.min(rect.height - y, Math.abs(end[1] - redactionStart[1]));
  redactionStart = undefined;
  if (width < 8 || height < 8) return;
  const image = new Image();
  image.onload = () => {
    const canvas = document.createElement("canvas"); canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d"); context.drawImage(image, 0, 0);
    context.fillStyle = "#000";
    context.fillRect(x / rect.width * canvas.width, y / rect.height * canvas.height, width / rect.width * canvas.width, height / rect.height * canvas.height);
    guide.steps[selected].image = canvas.toDataURL("image/png");
    ui.image.src = guide.steps[selected].image; persist(); status("Redaction applied to this scene.");
  };
  image.src = guide.steps[selected].image;
});

api("/api/status").then((response) => response.json()).then(({ voice }) => {
  ui.voice.textContent = `Local voice · ${voice}`;
}).catch((error) => status(error.message, true));

const captureLatest = new URLSearchParams(location.search).get("capture") === "latest";
if (!captureLatest) {
  try { const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)); if (saved?.steps?.length) loadGuide(saved); } catch (_) {}
} else {
  fetch("/api/capture/latest").then((response) => {
    if (!response.ok) throw new Error("No captured workflow is waiting in Stepfilm.");
    return response.json();
  }).then(loadGuide).catch(() => { showEmptyState(); status("No pending capture. Start a new capture from the Chrome extension."); });
}
