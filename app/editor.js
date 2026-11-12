const get = (id) => document.getElementById(id);
const ui = Object.fromEntries(
  ["voice", "saveState", "title", "instructions", "open", "openProject", "file", "projectFile", "export", "download", "saveProject",
   "status", "workspace", "count", "steps", "image", "sceneTitle", "narration", "charCount", "preview", "redact", "marker", "callout", "highlight", "annotationHint", "redactionHint", "redactionCanvas", "result", "video", "playbackStatus", "guidePreview", "copyGuide", "moveUp", "moveDown", "duplicate", "remove"]
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
  ui.image.style.cursor = "default";
  for (const button of [ui.marker, ui.callout, ui.highlight]) button.classList.remove("active");
  ui.annotationHint.textContent = "Choose a tool, then place it on the screenshot.";
}

function stepLabel(index) {
  return guide.steps[index].text.trim() || "Silent step";
}

function select(index) {
  stopPreview();
  cancelTools();
  selected = index;
  const step = guide.steps[index];
  ui.image.src = step.image;
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
  for (const [position, item] of [...ui.steps.children].entries()) {
    item.firstChild.setAttribute("aria-current", position === index ? "step" : "false");
  }
}

function loadGuide(imported) {
  stopPreview();
  clearVideo();
  guide = imported;
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

function renderGuidePreview() {
  ui.guidePreview.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = guide.title;
  ui.guidePreview.append(heading);
  guide.steps.forEach((step, index) => {
    const item = document.createElement("article");
    const number = document.createElement("span");
    number.className = "preview-number";
    number.textContent = String(index + 1).padStart(2, "0");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = stepLabel(index);
    const image = document.createElement("img");
    image.src = step.image;
    image.alt = `Step ${index + 1}`;
    copy.append(title, image);
    if (step.text.trim()) { const text = document.createElement("p"); text.textContent = step.text; copy.append(text); }
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

ui.export.addEventListener("click", async () => {
  stopPreview();
  clearVideo();
  ui.export.disabled = true;
  ui.open.disabled = true;
  ui.narration.disabled = true;
  status("Rendering video on this computer…");
  try {
    const response = await api("/api/export", { steps: guide.steps });
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
    renderGuidePreview();
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

function bakeAnnotation(kind, area) {
  if (!guide) return;
  const label = kind === "marker" ? String(selected + 1) : (guide.steps[selected].text.trim() || "Do this next");
  const source = new Image();
  source.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = source.naturalWidth; canvas.height = source.naturalHeight;
    const context = canvas.getContext("2d");
    context.drawImage(source, 0, 0);
    const scale = Math.max(1, canvas.width / 1280);
    context.lineJoin = "round";
    if (kind === "marker") {
      const x = canvas.width * area.x, y = canvas.height * area.y, radius = 30 * scale;
      context.fillStyle = "#c4f48f"; context.strokeStyle = "#15200e"; context.lineWidth = 5 * scale;
      context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.fill(); context.stroke();
      context.fillStyle = "#15200e"; context.font = `800 ${28 * scale}px system-ui`; context.textAlign = "center"; context.textBaseline = "middle";
      context.fillText(label.slice(0, 3), x, y);
    } else if (kind === "callout") {
      const width = canvas.width * 0.58, height = 78 * scale;
      const x = Math.max(0, Math.min(canvas.width - width, canvas.width * area.x - width / 2));
      const y = Math.max(0, Math.min(canvas.height - height, canvas.height * area.y - height / 2));
      context.fillStyle = "rgba(196,244,143,.96)"; context.strokeStyle = "#15200e"; context.lineWidth = 3 * scale;
      context.beginPath(); context.roundRect(x, y, width, height, 12 * scale); context.fill(); context.stroke();
      context.fillStyle = "#15200e"; context.font = `700 ${22 * scale}px system-ui`; context.textAlign = "left"; context.textBaseline = "middle";
      context.fillText(label.slice(0, 72), x + 18 * scale, y + height / 2, width - 36 * scale);
    } else {
      const x = canvas.width * area.x, y = canvas.height * area.y, width = canvas.width * area.width, height = canvas.height * area.height;
      context.fillStyle = "rgba(196,244,143,.20)"; context.strokeStyle = "#c4f48f"; context.lineWidth = 5 * scale;
      context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height);
    }
    guide.steps[selected].image = canvas.toDataURL("image/png");
    ui.image.src = guide.steps[selected].image;
    persist();
    clearVideo();
    cancelTools();
    status(`${kind === "highlight" ? "Highlight" : kind === "marker" ? "Marker" : "Callout"} added to this scene.`);
  };
  source.src = guide.steps[selected].image;
}
function chooseAnnotation(kind) {
  cancelTools();
  annotationMode = kind;
  const button = kind === "marker" ? ui.marker : kind === "callout" ? ui.callout : ui.highlight;
  button.classList.add("active");
  ui.image.style.cursor = kind === "highlight" ? "crosshair" : "copy";
  ui.annotationHint.textContent = kind === "highlight" ? "Drag over the area to highlight. Press Esc to cancel." : "Click the part of the screenshot this annotation should explain. Press Esc to cancel.";
}
ui.marker.addEventListener("click", () => chooseAnnotation("marker"));
ui.callout.addEventListener("click", () => chooseAnnotation("callout"));
ui.highlight.addEventListener("click", () => chooseAnnotation("highlight"));

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
  if (kind === "highlight") {
    const area = { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) };
    if (area.width < 0.03 || area.height < 0.03) { status("Drag across a larger area to create a highlight.", true); return; }
    bakeAnnotation(kind, area);
  } else {
    bakeAnnotation(kind, end);
  }
});

ui.redact.addEventListener("click", () => {
  if (!guide) return;
  cancelTools();
  redacting = !redacting;
  ui.redact.classList.toggle("active", redacting);
  ui.redactionHint.hidden = !redacting;
  ui.redactionCanvas.style.pointerEvents = redacting ? "auto" : "none";
  ui.redact.textContent = redacting ? "Done redacting" : "Redact pixels";
});
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
