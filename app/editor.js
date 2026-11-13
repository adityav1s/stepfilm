const get = (id) => document.getElementById(id);
const ui = Object.fromEntries(
  ["voice", "saveState", "title", "instructions", "open", "openProject", "file", "projectFile", "export", "download", "saveProject", "downloadGuide", "printGuide", "printDisclosure", "downloadMarkdown", "shareGuide", "shareDisclosure", "exportScorm", "scormDisclosure",
   "status", "workspace", "count", "steps", "bulkActions", "bulkCount", "bulkMoveUp", "bulkMoveDown", "bulkDuplicate", "bulkDelete", "image", "clipStepPreview", "sceneTitle", "narration", "stepDescription", "videoAnalysisPane", "videoAnalysis", "videoAnalysisCoverage", "aiAnalyzeVideo", "charCount", "voicePace", "voiceInstructions", "preview", "redact", "marker", "callout", "highlight", "arrow", "blur", "annotationHint", "annotationLayer", "annotationTools", "focusTools", "annotationText", "annotationApply", "annotationDelete", "annotationUndo", "annotationRedo", "redactionHint", "redactionCanvas", "captionPreview", "result", "video", "playbackStatus", "guidePreview", "copyGuide", "moveUp", "moveDown", "duplicate", "remove", "focus", "focusZoom", "focusZoomValue", "focusReset", "focusHint", "clipLibrary", "capturedClipPreview", "insertClip", "mobileVideoFile", "importMobileVideo", "aiSetup", "aiDraft", "transcribeButton", "transcribeCapture", "transcriptFile", "transcript", "transcriptHint", "captionLanguage", "translateCaptions", "downloadVtt", "downloadSrt", "captionEditorPane", "captionEditor", "addCaption", "recordedNarration", "playRecordedStep", "audioSourceControls", "audioSource", "useLocalVoice", "aiVoice", "aiVoiceover", "aiVoiceoverAll", "playAiVoice", "brandTheme", "brandAccent", "brandTitleStyle", "brandLogo", "clearBrandLogo"]
    .map((id) => [id, get(id)]),
);
let guide;
let selected = 0;
let selectedStepIds = new Set();
let videoUrl;
let previewUrl;
let previewAudio;
let previewRequest;
let mp4CaptionButtons = [];
let lastRenderedCaptionLanguage = "original";
const STORAGE_KEY = "stepfilm-draft";
let redacting = false;
let redactionStart;
let annotationMode;
let annotationStart;
let highlightJustBaked = false;
let selectedAnnotation;
let annotationHistory = [];
let annotationFuture = [];
let focusMode = false;

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
  mp4CaptionButtons.forEach((button) => { button.hidden = true; });
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
  focusMode = false;
  redacting = false;
  ui.redact.classList.remove("active");
  ui.redact.textContent = "Redact pixels";
  ui.redactionHint.hidden = true;
  ui.redactionCanvas.style.pointerEvents = "none";
  ui.redactionCanvas.classList.remove("is-active");
  ui.image.style.cursor = "default";
  for (const button of [ui.marker, ui.callout, ui.highlight, ui.arrow, ui.blur]) button.classList.remove("active");
  ui.focus.classList.remove("active");
  ui.annotationHint.textContent = "Choose a tool, then place it on the screenshot.";
}

function currentFocus() { return guide?.steps[selected]?.focus || { x: 0.5, y: 0.5, scale: 1 }; }
function renderFocus() {
  const focus = currentFocus();
  ui.focusZoom.value = String(focus.scale);
  ui.focusZoomValue.textContent = `${Number(focus.scale).toFixed(1)}×`;
  ui.focusHint.textContent = focus.scale > 1 ? `Focused at ${Math.round(focus.x * 100)}%, ${Math.round(focus.y * 100)}% · drag on the image to move.` : "Focus keeps the important area readable in preview and export.";
  ui.focus.classList.toggle("active", focusMode);
}
function updateFocusAt(point) {
  guide.steps[selected].focus = { ...currentFocus(), x: point.x, y: point.y };
  persist(); clearVideo(); renderFocus(); status("Scene focus placed. Adjust zoom or move it to the exact target.");
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
    const displayBox = StepfilmAnnotations.geometry(annotation, Math.max(1, ui.image.naturalWidth), Math.max(1, ui.image.naturalHeight));
    const displayWidth = displayBox.width / Math.max(1, ui.image.naturalWidth), displayHeight = displayBox.height / Math.max(1, ui.image.naturalHeight);
    object.style.left = `${(displayBox.x / Math.max(1, ui.image.naturalWidth)) * 100}%`; object.style.top = `${(displayBox.y / Math.max(1, ui.image.naturalHeight)) * 100}%`;
    object.style.width = `${displayWidth * 100}%`; object.style.height = `${displayHeight * 100}%`;
    if (annotation.type === "arrow") {
      const angle = Math.atan2(annotation.height, annotation.width) * 180 / Math.PI;
      object.style.transform = `rotate(${angle}deg)`;
      object.style.width = `${Math.max(annotation.width, 0.05) * 100}%`;
    } else if (annotation.type === "marker") object.textContent = annotation.text || annotation.label || "1";
    else if (annotation.type === "callout") object.textContent = annotation.text || annotation.label || annotation.content || "Explain this";
    const handle = document.createElement("span"); handle.className = "resize-handle"; handle.title = "Resize annotation"; object.append(handle);
    object.addEventListener("pointerdown", (event) => beginAnnotationDrag(event, annotation.id, event.target === handle));
    ui.annotationLayer.append(object);
  }
  updateAnnotationControls();
}
function syncAnnotationLayer() {
  ui.annotationLayer.style.height = `${ui.image.getBoundingClientRect().height}px`;
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
  const stop = () => { document.removeEventListener("pointermove", move); document.removeEventListener("pointerup", stop); const changed = currentAnnotations().find((item) => item.id === id); if (changed) changed.generated = false; annotationHistory.push(start.annotations); annotationFuture = []; persist(); clearVideo(); updateAnnotationControls(); };
  document.addEventListener("pointermove", move); document.addEventListener("pointerup", stop, { once: true });
}

function stepLabel(index) {
  const step = guide.steps[index];
  return step.text.trim() || (step.kind === "video" ? "Captured video clip" : "Silent step");
}

function select(index) {
  stopPreview();
  cancelTools();
  annotationHistory = [];
  annotationFuture = [];
  selectedAnnotation = undefined;
  selected = index;
  const step = guide.steps[index];
  ui.captionPreview.hidden = true;
  ui.captionPreview.textContent = "";
  const isVideo = step.kind === "video";
  ui.aiAnalyzeVideo.hidden = !isVideo;
  ui.videoAnalysisPane.hidden = !isVideo;
  ui.image.hidden = isVideo;
  ui.clipStepPreview.hidden = !isVideo;
  ui.annotationLayer.hidden = isVideo;
  ui.annotationTools.hidden = isVideo;
  ui.focusTools.hidden = isVideo;
  ui.redact.disabled = isVideo;
  if (!isVideo) ui.redact.disabled = false;
  if (isVideo) { ui.clipStepPreview.onerror = () => status("This captured clip is missing from this Stepfilm installation. Reopen the project on the computer where you recorded it or recapture the tab video.", true); ui.clipStepPreview.src = step.videoUrl; }
  else { ui.clipStepPreview.pause(); ui.clipStepPreview.removeAttribute("src"); ui.clipStepPreview.load(); ui.image.src = step.sourceImage || step.image; }
  ui.image.alt = `Screenshot of step ${index + 1}`;
  ui.sceneTitle.textContent = `${isVideo ? "Video" : "Step"} ${String(index + 1).padStart(2, "0")}`;
  ui.narration.value = step.text;
  ui.stepDescription.value = step.description || "";
  ui.videoAnalysis.value = step.videoAnalysis || "";
  ui.voicePace.value = step.voicePace || "natural";
  ui.voiceInstructions.value = step.voiceInstructions || "";
  const stepAudioId = step.recordedAudioId || guide.narrationAudioId;
  const recordedOption = ui.audioSource.querySelector('option[value="recorded"]');
  const aiOption = ui.audioSource.querySelector('option[value="synthetic"]');
  recordedOption.hidden = !stepAudioId;
  recordedOption.disabled = !stepAudioId;
  aiOption.hidden = !step.voiceover;
  aiOption.disabled = !step.voiceover;
  recordedOption.textContent = step.recordedAudioId ? "Original clip audio" : "Recorded microphone";
  ui.audioSource.value = step.audioSource || "synthetic";
  ui.useLocalVoice.hidden = true;
  setRecordedAudioSource(stepAudioId);
  renderStepCaptions();
  ui.playRecordedStep.hidden = !stepAudioId || !(step.captureEnd > step.captureStart);
  ui.transcribeCapture.hidden = !(guide.narrationAudioId || guide.steps.some((item) => item.recordedAudioId));
  ui.transcribeCapture.textContent = step.recordedAudioId ? "Transcribe this clip" : "Transcribe captured narration";
  ui.playAiVoice.hidden = !step.voiceover;
  ui.aiVoiceover.textContent = step.voiceover ? "Regenerate AI voiceover" : "Generate AI voiceover for this step";
  ui.charCount.textContent = `${step.text.length} / 400`;
  ui.moveUp.disabled = index === 0;
  ui.moveDown.disabled = index === guide.steps.length - 1;
  ui.remove.disabled = guide.steps.length === 1;
  ui.saveProject.hidden = false;
  ui.redactionCanvas.width = 0;
  ui.redactionCanvas.height = 0;
  renderAnnotations();
  renderFocus();
  syncAnnotationLayer();
  for (const [position, item] of [...ui.steps.children].entries()) {
    item.querySelector(".step-open").setAttribute("aria-current", position === index ? "step" : "false");
  }
}

function syncBulkActions() {
  const count = selectedStepIds.size;
  ui.bulkActions.hidden = !guide || count === 0;
  ui.bulkCount.textContent = `${count} selected`;
  const indices = guide ? guide.steps.flatMap((step, index) => selectedStepIds.has(step.id) ? [index] : []) : [];
  ui.bulkMoveUp.disabled = !indices.length || indices[0] === 0;
  ui.bulkMoveDown.disabled = !indices.length || indices[indices.length - 1] === guide.steps.length - 1;
  ui.bulkDuplicate.disabled = !indices.length || guide.steps.length + indices.length > 50;
  ui.bulkDelete.disabled = !indices.length || indices.length >= guide.steps.length;
}

function loadGuide(imported) {
  stopPreview();
  clearVideo();
  if (!globalThis.StepfilmAnnotations) throw new Error("Stepfilm annotation tools failed to load. Reload the editor after restarting Stepfilm.");
  guide = StepfilmAnnotations.normalizeGuide(imported);
  ui.brandTheme.value = guide.brand.theme;
  ui.brandAccent.value = guide.brand.accent;
  ui.brandTitleStyle.value = guide.brand.titleStyle;
  ui.brandLogo.value = "";
  ui.transcript.value = guide.transcript;
  const transcriptFollowsCaptions = Boolean(guide.transcriptSegments.length);
  ui.transcript.readOnly = transcriptFollowsCaptions;
  ui.transcriptHint.hidden = !transcriptFollowsCaptions;
  ui.transcript.placeholder = transcriptFollowsCaptions ? "Edit timed captions in each step to update this transcript." : "Transcribe an audio/video narration to create a full transcript…";
  ui.transcribeCapture.hidden = !(guide.narrationAudioId || guide.steps.some((step) => step.recordedAudioId));
  syncCaptionControls();
  if (guide.narrationAudioUrl) { ui.recordedNarration.src = guide.narrationAudioUrl; ui.recordedNarration.hidden = false; }
  else { ui.recordedNarration.pause(); ui.recordedNarration.removeAttribute("src"); ui.recordedNarration.load(); ui.recordedNarration.hidden = true; }
  ui.title.contentEditable = "true";
  ui.title.setAttribute("aria-label", "Editable tutorial title");
  ui.title.oninput = () => { guide.title = ui.title.textContent.trim().slice(0, 150) || "Untitled guide"; guide.aiTitle = false; guide.titleEdited = true; persist(); };
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
  ui.downloadGuide.hidden = false;
  ui.printGuide.hidden = false;
  ui.printDisclosure.hidden = false;
  ui.downloadMarkdown.hidden = false;
  ui.shareGuide.hidden = false;
  ui.shareDisclosure.hidden = false;
  ui.exportScorm.hidden = false;
  ui.scormDisclosure.hidden = false;
  ui.count.textContent = guide.steps.length;
  ui.steps.replaceChildren();
  ui.clipLibrary.hidden = !guide.capturedClips.length;
  if (guide.capturedClips.length) {
    ui.capturedClipPreview.onerror = () => status("The captured tab video is missing from this Stepfilm installation. The screenshot steps are still available.", true);
    ui.capturedClipPreview.src = guide.capturedClips[0].url;
    const alreadyInserted = guide.steps.some((step) => step.kind === "video" && step.videoId === guide.capturedClips[0].id);
    ui.insertClip.disabled = alreadyInserted;
    ui.insertClip.textContent = alreadyInserted ? "Captured clip is already in the guide" : "Insert after selected step";
  }
  else { ui.capturedClipPreview.pause(); ui.capturedClipPreview.removeAttribute("src"); ui.capturedClipPreview.load(); }
  selectedStepIds = new Set([...selectedStepIds].filter((id) => guide.steps.some((step) => step.id === id)));
  guide.steps.forEach((step, index) => {
    const row = document.createElement("li");
    row.className = "step-row";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "step-select";
    check.checked = selectedStepIds.has(step.id);
    check.setAttribute("aria-label", `Select step ${index + 1}`);
    check.addEventListener("change", () => {
      if (check.checked) selectedStepIds.add(step.id); else selectedStepIds.delete(step.id);
      syncBulkActions();
    });
    const button = document.createElement("button");
    button.className = "step-open";
    const number = document.createElement("span");
    const label = document.createElement("span");
    number.className = "step-number";
    number.textContent = guide.steps[index].kind === "video" ? "▶" : String(index + 1).padStart(2, "0");
    label.className = "step-text";
    label.textContent = stepLabel(index);
    button.append(number, label);
    button.addEventListener("click", () => select(index));
    row.append(check, button);
    ui.steps.append(row);
  });
  syncBulkActions();
  select(0);
  persist();
}

function setRecordedAudioSource(audioId) {
  const url = audioId ? `/api/narration/${audioId}` : "";
  if (ui.recordedNarration.getAttribute("src") !== url) {
    ui.recordedNarration.pause();
    if (url) ui.recordedNarration.src = url;
    else ui.recordedNarration.removeAttribute("src");
    ui.recordedNarration.load();
  }
  ui.recordedNarration.hidden = !url;
}

function showEmptyState() {
  stopPreview();
  clearVideo();
  guide = undefined;
  selected = 0;
  selectedStepIds.clear();
  syncBulkActions();
  ui.title.textContent = "Turn browser steps into a tutorial.";
  ui.instructions.hidden = false;
  const quickStart = document.getElementById("quickStart");
  if (quickStart) quickStart.hidden = false;
  ui.workspace.hidden = true;
  ui.export.hidden = true;
  ui.saveProject.hidden = true;
  ui.downloadGuide.hidden = true;
  ui.printGuide.hidden = true;
  ui.printDisclosure.hidden = true;
  ui.downloadMarkdown.hidden = true;
  ui.shareGuide.hidden = true;
  ui.shareDisclosure.hidden = true;
  ui.exportScorm.hidden = true;
  ui.scormDisclosure.hidden = true;
  ui.steps.replaceChildren();
  ui.clipLibrary.hidden = true;
  ui.count.textContent = "";
}

function persist() {
  if (!guide) return;
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(guide)); ui.saveState.textContent = "Saved locally"; }
  catch (_) { ui.saveState.textContent = "Draft loaded"; }
}

async function renderGuidePreview() {
  ui.guidePreview.replaceChildren();
  ui.guidePreview.dataset.theme = guide.brand.theme;
  ui.guidePreview.dataset.titleStyle = guide.brand.titleStyle;
  ui.guidePreview.style.setProperty("--guide-accent", guide.brand.accent);
  const heading = document.createElement("h3");
  heading.className = "guide-preview-title";
  if (guide.brand.logo) {
    const logo = document.createElement("img"); logo.className = "guide-preview-logo"; logo.src = guide.brand.logo; logo.alt = "Guide logo";
    ui.guidePreview.append(logo);
  }
  heading.textContent = guide.title;
  ui.guidePreview.append(heading);
  const images = await Promise.all(guide.steps.map((step) => step.kind === "video" ? null : flattenStep(step)));
  guide.steps.forEach((step, index) => {
    const item = document.createElement("article");
    const number = document.createElement("span");
    number.className = "preview-number";
    number.textContent = String(index + 1).padStart(2, "0");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = stepLabel(index);
    copy.append(title);
    const description = document.createElement("p");
    description.textContent = step.description || "";
    if (step.description) copy.append(description);
    if (step.kind === "video") {
      const clip = document.createElement("video"); clip.src = step.videoUrl; clip.controls = true; clip.playsInline = true; copy.append(clip);
    } else {
      const image = document.createElement("img"); image.src = images[index]; image.alt = `Step ${index + 1}`; copy.append(image);
    }
    const text = document.createElement("p");
    text.textContent = step.text.trim() || "No written instruction yet — add one in the editor so viewers know what to do.";
    if (!step.text.trim()) text.className = "missing-instruction";
    copy.append(text);
    item.append(number, copy);
    ui.guidePreview.append(item);
  });
}

function updateBrandSettings() {
  if (!guide) return;
  guide.brand.theme = ui.brandTheme.value;
  guide.brand.accent = ui.brandAccent.value;
  guide.brand.titleStyle = ui.brandTitleStyle.value;
  persist();
  if (!ui.result.hidden) renderGuidePreview().catch((error) => status(error.message, true));
}
ui.brandTheme.addEventListener("change", updateBrandSettings);
ui.brandAccent.addEventListener("input", updateBrandSettings);
ui.brandTitleStyle.addEventListener("change", updateBrandSettings);
ui.brandLogo.addEventListener("change", async () => {
  const file = ui.brandLogo.files?.[0];
  if (!guide || !file) return;
  if (!/^image\/(png|jpeg|webp)$/.test(file.type) || file.size > 350 * 1024) {
    ui.brandLogo.value = "";
    status("Choose a PNG, JPEG, or WebP logo up to 350 KB.", true);
    return;
  }
  try {
    guide.brand.logo = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = () => reject(new Error("Could not read the logo file.")); reader.readAsDataURL(file); });
    persist(); status("Logo saved in this project.");
    if (!ui.result.hidden) await renderGuidePreview();
  } catch (error) { status(error.message, true); }
});
ui.clearBrandLogo.addEventListener("click", async () => {
  if (!guide) return;
  guide.brand.logo = ""; ui.brandLogo.value = ""; persist();
  if (!ui.result.hidden) await renderGuidePreview();
  status("Logo removed from the guide.");
});

ui.insertClip.addEventListener("click", () => {
  if (!guide || !guide.capturedClips.length) return;
  if (guide.steps.length >= 50) { status("A guide can have up to 50 steps. Remove a step before inserting this clip.", true); return; }
  const clip = guide.capturedClips[0];
  const insertAt = selected + 1;
  guide.steps.splice(insertAt, 0, { id: `clip-${clip.id}`, kind: "video", videoId: clip.id, videoUrl: clip.url, text: "" });
  loadGuide(guide); select(insertAt); status("Continuous tab recording inserted after the selected step.");
});

ui.importMobileVideo.addEventListener("click", () => ui.mobileVideoFile.click());
ui.mobileVideoFile.addEventListener("change", async () => {
  const file = ui.mobileVideoFile.files?.[0];
  ui.mobileVideoFile.value = "";
  if (!file) return;
  if (!file.type.startsWith("video/")) { status("Choose a mobile video file to import.", true); return; }
  if (file.size > 250 * 1024 * 1024) { status("Mobile video imports are limited to 250 MB.", true); return; }
  if (guide && guide.steps.length >= 50) { status("A guide can have up to 50 steps. Remove a step before importing this clip.", true); return; }
  ui.importMobileVideo.disabled = true;
  ui.importMobileVideo.textContent = "Preparing video…";
  status(`Uploading and preparing ${file.name}…`);
  try {
    const response = await fetch(`/api/media/import?filename=${encodeURIComponent(file.name)}`, {
      method: "POST", headers: { "Content-Type": file.type || "video/mp4" }, body: file,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || "Could not prepare this video file.");
    const imported = { id: `mobile-${result.id}`, kind: "video", videoId: result.id, text: "", description: result.label || "Mobile recording", recordedAudioId: result.audioId || "", audioSource: result.hasAudio ? "recorded" : "synthetic", captureStart: 0, captureEnd: Number(result.duration) || 0 };
    if (guide) {
      const insertAt = selected + 1;
      guide.steps.splice(insertAt, 0, imported);
      loadGuide(guide); select(insertAt);
    } else {
      const title = String(result.label || "Mobile recording").replace(/\.[^.]+$/, "") || "Mobile recording";
      loadGuide({ title, steps: [imported] }); select(0);
    }
    status(result.hasAudio ? "Mobile recording imported with its original audio. Preview it, edit the narration, then export." : "Mobile recording imported. This clip has no audio track.");
  } catch (error) { status(error.message || "Mobile video import failed.", true); }
  finally { ui.importMobileVideo.disabled = false; ui.importMobileVideo.textContent = "Choose video file"; }
});

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
function downloadText(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
async function exportDocument(format) {
  if (!guide) return;
  const name = guide.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 80) || "stepfilm-guide";
  const steps = await exportSteps();
  const documentGuide = { ...guide, steps };
  if (format === "html") {
    downloadText(`${name}.html`, StepfilmGuideExport.toHtml(documentGuide), "text/html");
    status("HTML guide exported.");
  } else {
    downloadText(`${name}.md`, StepfilmGuideExport.toMarkdown(documentGuide), "text/markdown");
    status("Markdown guide exported.");
  }
}
ui.downloadGuide.addEventListener("click", () => exportDocument("html").catch((error) => status(error.message, true)));
ui.downloadMarkdown.addEventListener("click", () => exportDocument("markdown").catch((error) => status(error.message, true)));
ui.printGuide.addEventListener("click", async () => {
  if (!guide) return;
  const printWindow = window.open("about:blank", "_blank");
  if (!printWindow) { status("Allow pop-ups from Stepfilm to open the print-ready guide.", true); return; }
  ui.printGuide.disabled = true;
  status("Preparing print-ready guide…");
  try {
    const steps = await exportSteps();
    const html = StepfilmGuideExport.toPrintHtml({ ...guide, steps });
    printWindow.document.open();
    printWindow.document.write(html);
    printWindow.document.close();
    await Promise.all(Array.from(printWindow.document.images, (image) => image.decode().catch(() => undefined)));
    printWindow.focus();
    printWindow.print();
    status("Print-ready guide opened. Choose Save as PDF in the browser print dialog.");
  } catch (error) {
    printWindow.close();
    status(`Could not prepare the print view: ${error.message}`, true);
  } finally { ui.printGuide.disabled = false; }
});
async function buildPortableGuideRequest() {
  const steps = await exportSteps();
  const mediaSteps = [];
  steps.forEach((step, index) => {
    if (step.kind !== "video") return;
    step.portableVideoUrl = `media/${step.videoId}.webm`;
    const captions = Array.isArray(step.captions) ? step.captions : [];
    const languages = [...new Set(captions.flatMap((caption) => Object.keys(caption.translations || {})))];
    const tracks = [];
    const originalCues = captions.filter((caption) => caption.text?.trim()).map(({ start, end, text }) => ({ start, end, text }));
    if (originalCues.length) tracks.push({ language: "en", label: "English", default: true, cues: originalCues });
    for (const language of languages) {
      const cues = captions.map((caption) => ({ start: caption.start, end: caption.end, text: caption.translations?.[language] || "" }));
      if (cues.length && cues.every((cue) => cue.text.trim())) tracks.push({ language, label: language, default: false, cues });
    }
    step.portableCaptionTracks = tracks.map((track) => ({ language: track.language, label: track.label, default: track.default, src: `captions/step-${index + 1}-${track.language}.vtt` }));
    mediaSteps.push({ videoId: step.videoId, stepNumber: index + 1, captionTracks: tracks });
  });
  return { title: guide.title, html: StepfilmGuideExport.toHtml({ ...guide, steps }), mediaSteps };
}

ui.shareGuide.addEventListener("click", async () => {
  if (!guide) return;
  ui.shareGuide.disabled = true;
  status("Bundling a portable guide and captured media…");
  try {
    const response = await api("/api/export/share", await buildPortableGuideRequest());
    const blob = await response.blob();
    const name = guide.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 80) || "stepfilm-guide";
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${name}-portable.zip`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
    status("Portable ZIP downloaded. Unzip it and open index.html to view the guide offline.");
  } catch (error) { status(error.message || "Could not bundle the portable guide.", true); }
  finally { ui.shareGuide.disabled = false; }
});
ui.exportScorm.addEventListener("click", async () => {
  if (!guide) return;
  ui.exportScorm.disabled = true;
  status("Packaging the guide for a SCORM 1.2 LMS…");
  try {
    const response = await api("/api/export/scorm", await buildPortableGuideRequest());
    const blob = await response.blob();
    const name = guide.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "").slice(0, 80) || "stepfilm-course";
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
    anchor.href = url; anchor.download = `${name}-scorm12.zip`; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
    status("SCORM 1.2 package downloaded. Upload the ZIP to an LMS that accepts SCORM 1.2 courses.");
  } catch (error) { status(error.message || "Could not create the SCORM package.", true); }
  finally { ui.exportScorm.disabled = false; }
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
  guide.steps[selected].aiGenerated = false;
  const currentStep = guide.steps[selected];
  if (currentStep.captions.length) {
    currentStep.captions = [{ start: currentStep.captureStart, end: currentStep.captureEnd, text: ui.narration.value }];
    syncTranscriptFromCaptions(); renderStepCaptions();
  }
  const hadVoiceover = Boolean(guide.steps[selected].voiceover);
  guide.steps[selected].voiceover = "";
  if (hadVoiceover) status("Narration changed. Generate a fresh AI voiceover before exporting.");
  ui.playAiVoice.hidden = true;
  ui.steps.children[selected].querySelector(".step-text").textContent = stepLabel(selected);
  ui.charCount.textContent = `${ui.narration.value.length} / 400`;
  persist();
});
ui.stepDescription.addEventListener("input", () => { if (!guide) return; guide.steps[selected].description = ui.stepDescription.value; guide.steps[selected].aiGenerated = false; persist(); });
ui.videoAnalysis.addEventListener("input", () => { if (!guide || guide.steps[selected]?.kind !== "video") return; guide.steps[selected].videoAnalysis = ui.videoAnalysis.value.slice(0, 1200); guide.steps[selected].aiGenerated = false; persist(); });
ui.audioSource.addEventListener("change", () => {
  if (!guide) return;
  const step = guide.steps[selected];
  if (ui.audioSource.value === "recorded" && !(step.recordedAudioId || guide.narrationAudioId)) { ui.audioSource.value = step.audioSource; return; }
  if (ui.audioSource.value === "synthetic" && !step.voiceover) { ui.audioSource.value = step.audioSource; return; }
  step.audioSource = ui.audioSource.value; persist();
  status(ui.audioSource.value === "recorded" ? "Recorded audio selected for this step." : ui.audioSource.value === "local" ? "Local speech selected. MP4 export will generate it on this computer." : "AI voiceover selected for this step.");
});
ui.useLocalVoice.addEventListener("click", () => {
  const step = guide?.steps?.[selected]; if (!step) return;
  step.audioSource = "local"; ui.audioSource.value = "local"; ui.useLocalVoice.hidden = true; persist();
  status("Local speech selected for MP4 export. Your narration text and voice direction are saved.");
});
ui.voicePace.addEventListener("change", () => { if (guide) { guide.steps[selected].voicePace = ui.voicePace.value; persist(); } });
ui.voiceInstructions.addEventListener("input", () => { if (guide) { guide.steps[selected].voiceInstructions = ui.voiceInstructions.value.slice(0, 300); persist(); } });

function hasCaptions(project) { return project?.steps?.some((step) => step.captions?.length); }
function allCaptions() { return (guide?.steps || []).flatMap((step) => step.captions || []); }
function selectedCaptionLanguage() { return ui.captionLanguage?.value || ""; }
function syncCaptionControls() {
  const available = hasCaptions(guide);
  ui.captionLanguage.hidden = !available;
  ui.translateCaptions.hidden = !available;
  const language = selectedCaptionLanguage();
  const source = allCaptions().filter((caption) => caption.text.trim());
  const complete = !language || (source.length > 0 && source.every((caption) => caption.translations?.[language]?.trim()));
  ui.downloadVtt.hidden = !available;
  ui.downloadSrt.hidden = !available;
  ui.downloadVtt.disabled = !complete;
  ui.downloadSrt.disabled = !complete;
  ui.translateCaptions.disabled = !language || !source.length;
  ui.translateCaptions.textContent = language && complete ? "Retranslate captions" : "Translate captions";
  const label = language ? `${language.toUpperCase()} ` : "";
  ui.downloadVtt.textContent = `Download ${label}VTT captions`;
  ui.downloadSrt.textContent = `Download ${label}SRT captions`;
}
function syncTranscriptFromCaptions() {
  guide.transcript = guide.steps.flatMap((step) => step.captions || []).sort((a, b) => a.start - b.start).map((caption) => caption.text.trim()).filter(Boolean).join(" ");
  ui.transcript.value = guide.transcript; persist();
  syncCaptionControls();
}
function renderStepCaptions() {
  const step = guide?.steps?.[selected];
  ui.captionEditor.replaceChildren();
  ui.captionEditorPane.hidden = !step?.captions?.length;
  if (!step?.captions?.length) return;
  step.captions.forEach((caption, index) => {
    const row = document.createElement("div"); row.className = "caption-row";
    const text = document.createElement("textarea"); text.rows = 2; text.maxLength = 500; text.value = caption.text; text.setAttribute("aria-label", `Caption ${index + 1} text`);
    text.addEventListener("input", () => { caption.text = text.value; step.text = step.captions.map((item) => item.text.trim()).filter(Boolean).join(" ").slice(0, 400); ui.narration.value = step.text; ui.charCount.textContent = `${step.text.length} / 400`; syncTranscriptFromCaptions(); });
    const language = selectedCaptionLanguage();
    if (language) {
      if (!caption.translations) caption.translations = {};
      const translationLabel = document.createElement("label"); translationLabel.textContent = `${ui.captionLanguage.selectedOptions[0]?.textContent || language} translation`;
      const translated = document.createElement("textarea"); translated.rows = 2; translated.maxLength = 1000; translated.value = caption.translations[language] || ""; translated.setAttribute("aria-label", `Caption ${index + 1} translated text`);
      translated.addEventListener("input", () => { caption.translations[language] = translated.value; persist(); syncCaptionControls(); });
      const translation = document.createElement("div"); translation.className = "caption-translation"; translation.append(translationLabel, translated); row.append(translation);
    }
    const start = document.createElement("input"); start.type = "number"; start.min = "0"; start.step = "0.1"; start.value = Number(caption.start).toFixed(1); start.setAttribute("aria-label", `Caption ${index + 1} start time in seconds`);
    const end = document.createElement("input"); end.type = "number"; end.min = "0"; end.step = "0.1"; end.value = Number(caption.end).toFixed(1); end.setAttribute("aria-label", `Caption ${index + 1} end time in seconds`);
    const updateTimes = () => { caption.start = Math.max(0, Number(start.value) || 0); caption.end = Math.max(caption.start, Number(end.value) || caption.start); end.value = caption.end.toFixed(1); persist(); };
    start.addEventListener("change", updateTimes); end.addEventListener("change", updateTimes);
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "button danger"; remove.textContent = "Remove"; remove.addEventListener("click", () => { step.captions.splice(index, 1); step.text = step.captions.map((item) => item.text.trim()).filter(Boolean).join(" ").slice(0, 400); ui.narration.value = step.text; ui.charCount.textContent = `${step.text.length} / 400`; renderStepCaptions(); syncTranscriptFromCaptions(); });
    const times = document.createElement("div"); times.className = "caption-times"; times.append("Start", start, "End", end, remove); row.append(text, times); ui.captionEditor.append(row);
  });
}
ui.addCaption.addEventListener("click", () => {
  const step = guide?.steps?.[selected]; if (!step) return;
  if (!step.captions) step.captions = [];
  step.captions.push({ start: step.captureStart || 0, end: step.captureEnd || guide.captureDuration || 1, text: "" });
  renderStepCaptions(); persist();
});
function captionTimestamp(seconds, separator) {
  const value = Math.max(0, Number(seconds) || 0); const hours = Math.floor(value / 3600); const minutes = Math.floor((value % 3600) / 60); const whole = Math.floor(value % 60); const millis = Math.floor((value - Math.floor(value)) * 1000);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(whole).padStart(2, "0")}${separator}${String(millis).padStart(3, "0")}`;
}
function downloadCaptions(format) {
  const language = selectedCaptionLanguage();
  const captions = allCaptions().filter((item) => item.text.trim()).map((item) => ({ ...item, exportText: language ? item.translations?.[language] || "" : item.text })).filter((item) => item.exportText.trim()).sort((a, b) => a.start - b.start);
  const text = format === "vtt" ? `WEBVTT\n\n${captions.map((item, index) => `${index + 1}\n${captionTimestamp(item.start, ".")} --> ${captionTimestamp(item.end, ".")}\n${item.exportText.trim()}`).join("\n\n")}\n` : `${captions.map((item, index) => `${index + 1}\n${captionTimestamp(item.start, ",")} --> ${captionTimestamp(item.end, ",")}\n${item.exportText.trim()}`).join("\n\n")}\n`;
  const suffix = language ? `-${language}` : "";
  downloadText(`${guide.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "stepfilm-guide"}${suffix}.${format}`, text, format === "vtt" ? "text/vtt" : "application/x-subrip");
}
ui.downloadVtt.addEventListener("click", () => downloadCaptions("vtt"));
ui.downloadSrt.addEventListener("click", () => downloadCaptions("srt"));
function downloadRenderedCaptions(format) {
  const language = lastRenderedCaptionLanguage === "original" ? "original" : lastRenderedCaptionLanguage;
  api(`/api/export/captions?format=${encodeURIComponent(format)}&language=${encodeURIComponent(language)}`).then(async (response) => {
    const text = await response.text();
    const suffix = language === "original" ? "original" : language;
    const title = guide.title.replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "") || "stepfilm-guide";
    downloadText(`${title}-mp4-timed-${suffix}.${format}`, text, format === "vtt" ? "text/vtt" : "application/x-subrip");
  }).catch((error) => status(error.message || "Could not download the MP4-aligned captions.", true));
}
mp4CaptionButtons = ["vtt", "srt"].map((format) => {
  const button = document.createElement("button"); button.type = "button"; button.className = "button"; button.hidden = true;
  button.dataset.format = format;
  button.textContent = `Download MP4-timed ${format.toUpperCase()}`;
  button.addEventListener("click", () => downloadRenderedCaptions(format));
  ui.downloadSrt.insertAdjacentElement("afterend", button);
  return button;
});
ui.captionLanguage.addEventListener("change", () => { renderStepCaptions(); syncCaptionControls(); updateCaptionPlaybackPreview(); });
ui.translateCaptions.addEventListener("click", async () => {
  const language = selectedCaptionLanguage();
  const entries = guide.steps.flatMap((step) => (step.captions || []).filter((caption) => caption.text.trim()).map((caption) => ({ caption, text: caption.text.trim() })));
  if (!language || !entries.length) { status("Choose a target language and add captions first.", true); return; }
  if (entries.some(({ caption }) => caption.translations?.[language]?.trim()) && !confirm("Replace the existing translations for this language?")) return;
  ui.translateCaptions.disabled = true; ui.translateCaptions.textContent = "Translating…";
  try {
    const response = await api("/api/ai/translate-captions", { language, captions: entries.map((entry) => entry.text) });
    const result = await response.json();
    if (!Array.isArray(result.translations) || result.translations.length !== entries.length) throw new Error("The translation result was incomplete. Original captions are unchanged.");
    entries.forEach(({ caption }, index) => { if (!caption.translations) caption.translations = {}; caption.translations[language] = String(result.translations[index] || "").slice(0, 1000); });
    persist(); renderStepCaptions(); syncCaptionControls(); status(`Translated ${entries.length} captions into ${ui.captionLanguage.selectedOptions[0]?.textContent || language}. Review each translation before exporting.`);
  } catch (error) { status(error.message, true); }
  finally { syncCaptionControls(); }
});

ui.aiSetup.addEventListener("click", async () => {
  const key = prompt("OpenAI API key (kept in Stepfilm memory until the server stops):", "");
  if (key === null) return;
  try { await api("/api/ai/configure", { apiKey: key }); status(key ? "AI connected for this Stepfilm session." : "AI disconnected."); }
  catch (error) { status(error.message, true); }
});
ui.aiAnalyzeVideo.addEventListener("click", async () => {
  const step = guide?.steps?.[selected];
  if (step?.kind !== "video") { status("Select a video step to analyze.", true); return; }
  const hasAuthored = !step.aiGenerated && (step.text.trim() || step.description.trim() || step.videoAnalysis.trim());
  if (hasAuthored && !confirm("Analyzing this clip will replace its current instruction, description, and action breakdown. Continue?")) return;
  ui.aiAnalyzeVideo.disabled = true; ui.aiAnalyzeVideo.textContent = "Sampling video…";
  try {
    const response = await api("/api/ai/analyze-video", { videoId: step.videoId });
    const result = await response.json();
    step.description = String(result.description || "").slice(0, 400);
    step.text = String(result.instruction || "").slice(0, 400);
    step.videoAnalysis = (Array.isArray(result.events) ? result.events : []).map((event) => {
      const time = Math.max(0, Number(event.time) || 0);
      return `${Math.floor(time / 60)}:${String(Math.floor(time % 60)).padStart(2, "0")} — ${String(event.action || "").trim()}`;
    }).filter((line) => !line.endsWith("— ")).join("\n").slice(0, 1200);
    step.aiGenerated = true;
    ui.stepDescription.value = step.description; ui.narration.value = step.text; ui.videoAnalysis.value = step.videoAnalysis;
    ui.charCount.textContent = `${step.text.length} / 400`;
    ui.steps.children[selected].querySelector(".step-text").textContent = stepLabel(selected);
    persist();
    const coverage = (result.sampledAt || []).map((time) => `${Number(time).toFixed(1)}s`).join(", ");
    ui.videoAnalysisCoverage.textContent = `Analyzed ${result.sampledFrames || 0} frames selected across ${Number(result.duration || 0).toFixed(1)}s, favoring visual changes. Sampled at ${coverage || "timestamps unavailable"}. Brief actions may be missed; review the sequence.`;
    status(`Video analysis drafted an instruction and ${result.events?.length || 0} timestamped observations from ${result.sampledFrames || 0} visually selected frames. Brief actions may be missing.`);
  } catch (error) { status(error.message, true); }
  finally { ui.aiAnalyzeVideo.disabled = false; ui.aiAnalyzeVideo.textContent = "Analyze this video step"; }
});
ui.aiDraft.addEventListener("click", async () => {
  if (!guide) return;
  const hasAuthored = guide.steps.some((step) => (!step.aiGenerated && (step.text.trim() || (step.description || "").trim())) || step.voiceover) || guide.titleEdited || (guide.title && !guide.aiTitle && !["Browser workflow", "Captured workflow", "Untitled guide"].includes(guide.title));
  if (hasAuthored && !confirm("AI drafting will replace existing title, descriptions, narration, and voiceovers. Continue?")) return;
  ui.aiDraft.disabled = true; ui.aiDraft.textContent = "Drafting…";
  try {
    const response = await api("/api/ai/draft", { title: guide.title, steps: guide.steps.map((step) => ({ ...step, image: step.sourceImage || step.image })) });
    const result = await response.json();
    guide.title = String(result.title || guide.title).slice(0, 150); guide.aiTitle = true; guide.titleEdited = false; ui.title.textContent = guide.title;
    let skipped = 0;
    result.steps.forEach((draft, index) => {
      const step = guide.steps[index];
      if (step.kind === "video" || draft.skipped) { skipped++; return; }
      step.text = String(draft.instruction || "").slice(0, 400);
      step.description = String(draft.description || "").slice(0, 400);
      step.aiGenerated = true;
      step.annotations = (step.annotations || []).filter((a) => a.generated !== true);
      const callout = String(draft.callout || "").slice(0, 140);
      if (callout) { const [tx, ty] = Array.isArray(draft.target) ? draft.target : [0.5, 0.5]; step.annotations.push(StepfilmAnnotations.create("callout", Math.max(0, Math.min(.7, Number(tx) - .15)), Math.max(0, Math.min(.84, Number(ty) + .06)), .3, .1, callout)); step.annotations.at(-1).generated = true; }
      step.voiceover = "";
    });
    loadGuide(guide); status(`AI drafted title, descriptions, instructions and callouts. Review every suggestion.${skipped ? ` ${skipped} video step${skipped === 1 ? " was" : "s were"} skipped because video analysis is not available.` : ""}`);
  } catch (error) { status(error.message, true); }
  finally { ui.aiDraft.disabled = false; ui.aiDraft.textContent = "Draft tutorial"; }
});
ui.transcribeButton.addEventListener("click", () => ui.transcriptFile.click());
ui.transcribeCapture.addEventListener("click", async () => {
  const selectedStep = guide?.steps?.[selected];
  const clipAudioId = selectedStep?.recordedAudioId || "";
  const captureId = clipAudioId || guide?.narrationAudioId;
  if (!captureId) { status("This project has no captured microphone narration.", true); return; }
  const hasEdits = guide.transcript || guide.steps.some((step) => step.text.trim() || step.captions?.length);
  if (hasEdits && !confirm("Transcribing again will replace existing step narration, transcript, and caption timing. Continue?")) return;
  ui.transcribeCapture.disabled = true; ui.transcribeCapture.textContent = "Transcribing and aligning…";
  try {
    const clipDuration = Number(selectedStep?.captureEnd) || 0;
    const transcriptionSteps = clipAudioId ? [{ kind: "video", at: 0 }] : guide.steps.map((step) => ({ kind: step.kind, at: step.captureAt }));
    const response = await api("/api/ai/transcribe-capture", { captureId, duration: clipAudioId ? clipDuration : guide.captureDuration, steps: transcriptionSteps });
    const result = await response.json();
    guide.transcript = String(result.text || "").slice(0, 100000);
    guide.transcriptSegments = (result.segments || []).map((segment) => ({ ...segment, stepIndex: clipAudioId ? selected : segment.stepIndex }));
    result.steps.forEach((draft, index) => {
      const step = guide.steps[clipAudioId ? selected : index]; if (!step) return;
      step.text = String(draft.text || "").slice(0, 400); step.captions = draft.captions || [];
      step.captureStart = Number(draft.captureStart ?? 0); step.captureEnd = Number(draft.captureEnd ?? guide.captureDuration);
      step.audioSource = draft.audioSource || "recorded";
      step.aiGenerated = false;
    });
    if (clipAudioId) guide.transcript = guide.steps.flatMap((step) => step.captions || []).map((caption) => caption.text.trim()).filter(Boolean).join(" ");
    loadGuide(guide); status(`Transcribed narration into ${result.segments?.length || 0} timed caption segments across ${result.steps.length} steps. Edit the text or timing before exporting.`);
  } catch (error) { status(error.message, true); }
  finally { ui.transcribeCapture.disabled = false; ui.transcribeCapture.textContent = "Transcribe captured narration"; }
});
ui.transcriptFile.addEventListener("change", async () => {
  const file = ui.transcriptFile.files?.[0]; if (!file) return;
  if (guide?.transcript && !confirm("Replace the existing guide transcript with a new transcription?")) { ui.transcriptFile.value = ""; return; }
  if (file.size > 25 * 1024 * 1024) { status("Choose an audio/video file 25 MB or smaller.", true); return; }
  try {
    const audio = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file); });
    status(`Transcribing ${file.name}…`);
    const result = await (await api("/api/ai/transcribe", { filename: file.name, audio })).json();
    ui.transcript.value = String(result.text || "").slice(0, 100000); guide.transcript = ui.transcript.value; persist(); status(`Transcript generated from ${file.name}. Review and edit the full transcript below.`);
  } catch (error) { status(error.message, true); }
  finally { ui.transcriptFile.value = ""; }
});
ui.transcript.addEventListener("input", () => { if (guide && !ui.transcript.readOnly) { guide.transcript = ui.transcript.value.slice(0, 100000); persist(); } });
ui.playRecordedStep.addEventListener("click", async () => {
  const step = guide?.steps?.[selected]; if (!step || !(step.recordedAudioId || guide.narrationAudioId)) return;
  const audio = ui.recordedNarration;
  try { audio.pause(); audio.currentTime = Math.max(0, step.captureStart || 0); await audio.play(); }
  catch (error) { status(`Could not play this recorded segment: ${error.message}`, true); return; }
});
function updateCaptionPlaybackPreview() {
  const step = guide?.steps?.[selected]; if (!step) return;
  const now = ui.recordedNarration.currentTime;
  const caption = step.captions?.find((item) => now >= item.start && now <= item.end && item.text.trim());
  const language = selectedCaptionLanguage();
  ui.captionPreview.textContent = caption ? (language ? caption.translations?.[language] || caption.text : caption.text) : "";
  ui.captionPreview.hidden = !caption || ui.recordedNarration.paused;
}
ui.recordedNarration.ontimeupdate = () => {
  const step = guide?.steps?.[selected]; if (!step) return;
  const now = ui.recordedNarration.currentTime;
  updateCaptionPlaybackPreview();
  const end = step.captureEnd || 0;
  if (!ui.recordedNarration.paused && end > 0 && now >= end) { ui.recordedNarration.pause(); ui.captionPreview.hidden = true; }
};
ui.aiVoiceover.addEventListener("click", async () => {
  const text = ui.narration.value.trim(); if (!text) { status("Add narration text before generating a voiceover.", true); return; }
  if (guide.steps[selected].voiceover && !confirm("Replace this step’s existing AI voiceover?")) return;
  ui.aiVoiceover.disabled = true; ui.aiVoiceover.textContent = "Generating…";
  try {
    const response = await api("/api/ai/voice", { text, voice: ui.aiVoice.value, pace: ui.voicePace.value, instructions: ui.voiceInstructions.value });
    const blob = await response.blob(); const data = await new Promise((resolve) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsDataURL(blob); });
    guide.steps[selected].voiceover = data; guide.steps[selected].voiceLabel = ui.aiVoice.value; guide.steps[selected].audioSource = "synthetic"; ui.audioSource.value = "synthetic"; persist(); select(selected); status("AI voiceover generated. It will be used in MP4 export.");
  } catch (error) { ui.useLocalVoice.hidden = false; status(`AI voiceover failed. Your narration text and voice direction are saved. Use Local speech for export, or retry AI voiceover. ${error.message}`, true); }
  finally { ui.aiVoiceover.disabled = false; ui.aiVoiceover.textContent = "Generate AI voiceover"; }
});
ui.aiVoiceoverAll.addEventListener("click", async () => {
  if (!guide) return;
  const targets = guide.steps.map((step, index) => ({ step, index })).filter(({ step }) => step.text.trim());
  if (!targets.length) { status("Add narration to at least one step first.", true); return; }
  if (targets.some(({ step }) => step.voiceover) && !confirm("Replace existing AI voiceovers for narrated steps?")) return;
  ui.aiVoiceoverAll.disabled = true;
  try {
    for (const [position, { step }] of targets.entries()) {
      ui.aiVoiceoverAll.textContent = `Generating ${position + 1} of ${targets.length}…`;
      const response = await api("/api/ai/voice", { text: step.text.trim(), voice: ui.aiVoice.value, pace: step.voicePace || "natural", instructions: step.voiceInstructions || "" });
      const blob = await response.blob();
      step.voiceover = await new Promise((resolve) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.readAsDataURL(blob); });
      step.voiceLabel = ui.aiVoice.value;
      step.audioSource = "synthetic"; persist();
    }
    select(selected); status(`Generated AI voiceover for ${targets.length} narrated steps. Play each one or export the guide.`);
  } catch (error) { status(`${error.message} Completed voiceovers remain saved. Retry to finish the rest.`, true); }
  finally { ui.aiVoiceoverAll.disabled = false; ui.aiVoiceoverAll.textContent = "Generate voiceover for all steps"; }
});
ui.playAiVoice.addEventListener("click", () => { const src = guide?.steps[selected]?.voiceover; if (src) new Audio(src).play().catch((error) => status(error.message, true)); });

function applyBulkSteps(nextSteps, nextSelectedIds, preferredStepId, message) {
  guide.steps = nextSteps;
  selectedStepIds = new Set(nextSelectedIds);
  loadGuide(guide);
  const preferredIndex = preferredStepId ? guide.steps.findIndex((step) => step.id === preferredStepId) : -1;
  select(preferredIndex >= 0 ? preferredIndex : Math.min(selected, guide.steps.length - 1));
  status(message);
}

function moveSelectedSteps(direction) {
  if (!guide || !selectedStepIds.size) return;
  const preferredId = guide.steps[selected]?.id;
  const steps = StepfilmStepSelection.moveSelected(guide.steps, [...selectedStepIds], direction);
  applyBulkSteps(steps, [...selectedStepIds], preferredId, `Moved ${selectedStepIds.size} selected step${selectedStepIds.size === 1 ? "" : "s"}.`);
}

ui.bulkMoveUp.addEventListener("click", () => moveSelectedSteps(-1));
ui.bulkMoveDown.addEventListener("click", () => moveSelectedSteps(1));
ui.bulkDuplicate.addEventListener("click", () => {
  if (!guide || !selectedStepIds.size) return;
  const result = StepfilmStepSelection.duplicateSelected(guide.steps, [...selectedStepIds]);
  if (result.steps.length > 50) { status("A guide can have up to 50 steps. Remove steps before duplicating this selection.", true); return; }
  applyBulkSteps(result.steps, result.selectedIds, result.selectedIds[0], `Duplicated ${result.selectedIds.length} selected step${result.selectedIds.length === 1 ? "" : "s"}.`);
});
ui.bulkDelete.addEventListener("click", () => {
  if (!guide || !selectedStepIds.size) return;
  if (selectedStepIds.size >= guide.steps.length) { status("Keep at least one step in the guide.", true); return; }
  const oldSelected = selected;
  const selectedCurrentId = guide.steps[selected]?.id;
  const result = StepfilmStepSelection.deleteSelected(guide.steps, [...selectedStepIds]);
  const targetId = selectedStepIds.has(selectedCurrentId) ? result.steps[Math.min(oldSelected, result.steps.length - 1)]?.id : selectedCurrentId;
  applyBulkSteps(result.steps, [], targetId, `Deleted ${result.deletedCount} selected step${result.deletedCount === 1 ? "" : "s"}.`);
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
  const base = document.createElement("canvas"); base.width = source.naturalWidth; base.height = source.naturalHeight;
  const baseContext = base.getContext("2d"); baseContext.drawImage(source, 0, 0);
  const scale = Math.max(1, 1280 / source.naturalWidth, 720 / source.naturalHeight);
  const canvas = document.createElement("canvas"); canvas.width = Math.round(source.naturalWidth * scale); canvas.height = Math.round(source.naturalHeight * scale);
  const context = canvas.getContext("2d"); context.scale(scale, scale);
  const focus = StepfilmAnnotations.normalizeFocus(step.focus);
  context.save();
  context.translate((canvas.width / scale) / 2 - focus.x * base.width * focus.scale, (canvas.height / scale) / 2 - focus.y * base.height * focus.scale);
  context.scale(focus.scale, focus.scale);
  context.drawImage(base, 0, 0);
  for (const annotation of step.annotations || []) {
    const box = StepfilmAnnotations.geometry(annotation, source.naturalWidth, source.naturalHeight);
    const { x, y, width, height } = box;
    if (annotation.type === "blur") {
      const crop = document.createElement("canvas"); crop.width = Math.max(1, Math.round(width)); crop.height = Math.max(1, Math.round(height));
      const cropContext = crop.getContext("2d"); cropContext.filter = "blur(18px)"; cropContext.drawImage(base, x, y, width, height, 0, 0, crop.width, crop.height);
      context.drawImage(crop, x, y, width, height); context.fillStyle = "rgba(16,24,39,.72)"; context.fillRect(x, y, width, height); context.strokeStyle = "rgba(255,173,165,.9)"; context.lineWidth = 4; context.strokeRect(x, y, width, height); continue;
    }
    if (annotation.type === "marker") { context.fillStyle = "#c4f48f"; context.strokeStyle = "#15200e"; context.lineWidth = 5; context.beginPath(); context.arc(x + width / 2, y + height / 2, Math.max(12, Math.min(width, height) / 2), 0, Math.PI * 2); context.fill(); context.stroke(); context.fillStyle = "#15200e"; context.font = "800 28px system-ui"; context.textAlign = "center"; context.textBaseline = "middle"; context.fillText((annotation.text || "1").slice(0, 3), x + width / 2, y + height / 2); continue; }
    if (annotation.type === "callout") {
      context.fillStyle = "rgba(196,244,143,.96)"; context.strokeStyle = "#15200e"; context.lineWidth = 3; context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height);
      const layout = StepfilmAnnotations.geometry(annotation, source.naturalWidth, source.naturalHeight);
      context.fillStyle = "#15200e"; context.font = `700 ${layout.fontSize}px system-ui`; context.textAlign = "left"; context.textBaseline = "top";
      layout.lines.forEach((value, index) => context.fillText(value, x + 12, y + 5 + index * layout.lineHeight)); continue;
    }
    if (annotation.type === "arrow") { context.strokeStyle = "#c4f48f"; context.fillStyle = "#c4f48f"; context.lineWidth = 7; context.beginPath(); context.moveTo(x, y); context.lineTo(x + width, y + height); context.stroke(); const angle = Math.atan2(height, width); context.beginPath(); context.moveTo(x + width, y + height); context.lineTo(x + width - 22 * Math.cos(angle - Math.PI / 6), y + height - 22 * Math.sin(angle - Math.PI / 6)); context.lineTo(x + width - 22 * Math.cos(angle + Math.PI / 6), y + height - 22 * Math.sin(angle + Math.PI / 6)); context.closePath(); context.fill(); continue; }
    if (annotation.type === "highlight") { context.fillStyle = "rgba(196,244,143,.22)"; context.strokeStyle = "#c4f48f"; context.lineWidth = 5; context.fillRect(x, y, width, height); context.strokeRect(x, y, width, height); }
  }
  context.restore();
  return canvas.toDataURL("image/png");
}
async function exportSteps() { const language = selectedCaptionLanguage(); return Promise.all(guide.steps.map(async (step) => step.kind === "video" ? ({ ...step, captionLanguage: language, annotations: [] }) : ({ ...step, captionLanguage: language, image: await flattenStep(step), annotations: [] }))); }

ui.export.addEventListener("click", async () => {
  const renderLanguage = selectedCaptionLanguage();
  if (renderLanguage && allCaptions().some((caption) => caption.text.trim() && !caption.translations?.[renderLanguage]?.trim())) {
    status("Finish translating every caption or choose Original captions before exporting.", true);
    return;
  }
  stopPreview();
  clearVideo();
  ui.export.disabled = true;
  ui.open.disabled = true;
  ui.narration.disabled = true;
  status("Rendering video on this computer…");
  try {
    const response = await api("/api/export", { steps: await exportSteps(), narrationAudioId: guide.narrationAudioId, brand: guide.brand, title: guide.title });
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
    lastRenderedCaptionLanguage = renderLanguage || "original";
    const trackLabel = renderLanguage ? (ui.captionLanguage.selectedOptions[0]?.textContent || renderLanguage) : "original";
    mp4CaptionButtons.forEach((button) => { button.hidden = !hasCaptions(guide); button.textContent = `Download MP4-timed ${trackLabel} ${button.dataset.format.toUpperCase()}`; });
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
  const text = [guide.title, ...guide.steps.flatMap((step, index) => [`${index + 1}. ${step.description ? `${step.description} — ` : ""}${step.text.trim() || (step.kind === "video" ? "Captured video clip" : "Silent step")}`]), ...(guide.transcript ? ["", "Transcript", guide.transcript] : [])].join("\n");
  try { await navigator.clipboard.writeText(text); ui.copyGuide.textContent = "Copied"; setTimeout(() => { ui.copyGuide.textContent = "Copy guide text"; }, 1600); }
  catch (_) { status("Clipboard access is unavailable in this browser.", true); }
});

function imagePoint(event) {
  const rect = ui.image.getBoundingClientRect();
  return { x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) };
}
ui.image.addEventListener("load", () => { syncAnnotationLayer(); renderAnnotations(); });
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
ui.focus.addEventListener("click", () => { cancelTools(); focusMode = true; ui.focus.classList.add("active"); ui.focusHint.textContent = "Click the important area in the screenshot. Press Esc to cancel."; });
ui.focusReset.addEventListener("click", () => { guide.steps[selected].focus = { x: 0.5, y: 0.5, scale: 1 }; persist(); clearVideo(); renderFocus(); status("Scene focus reset."); });
ui.focusZoom.addEventListener("input", () => { guide.steps[selected].focus = { ...currentFocus(), scale: Number(ui.focusZoom.value) }; persist(); clearVideo(); renderFocus(); });

ui.image.addEventListener("pointerdown", (event) => {
  if (!annotationMode) return;
  annotationStart = imagePoint(event);
  ui.image.setPointerCapture(event.pointerId);
});
function finishAnnotationGesture(event) {
  if (!annotationMode || !annotationStart) return;
  const end = imagePoint(event);
  const kind = annotationMode;
  const start = annotationStart;
  annotationStart = undefined;
  if (kind === "highlight" || kind === "blur") {
    const area = { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) };
    if (area.width < 0.03 || area.height < 0.03) {
      if (kind === "blur") {
        highlightJustBaked = true;
        setTimeout(() => { highlightJustBaked = false; }, 0);
        addAnnotation("blur", { x: Math.max(0, Math.min(0.8, end.x - 0.1)), y: Math.max(0, Math.min(0.88, end.y - 0.06)), width: 0.2, height: 0.12 });
      } else status("Drag across a larger target area.", true);
      return;
    }
    highlightJustBaked = true;
    setTimeout(() => { highlightJustBaked = false; }, 0);
    addAnnotation(kind, area);
  } else {
    if (kind === "arrow") addAnnotation(kind, { x: Math.max(0, end.x - 0.22), y: end.y, width: 0.22, height: 0.01 });
    else if (kind === "callout") addAnnotation(kind, { x: Math.max(0, Math.min(0.42, end.x - 0.29)), y: Math.max(0, Math.min(0.88, end.y - 0.06)), width: 0.58, height: 0.12 });
    else addAnnotation(kind, { x: end.x - 0.025, y: end.y - 0.025, width: 0.05, height: 0.05 });
  }
}
document.addEventListener("pointerup", finishAnnotationGesture);
document.addEventListener("pointercancel", () => { annotationStart = undefined; });
ui.image.addEventListener("click", (event) => {
  if (focusMode) { updateFocusAt(imagePoint(event)); cancelTools(); return; }
  if (highlightJustBaked) return;
  if (annotationMode === "highlight") addAnnotation("highlight", { x: 0.36, y: 0.34, width: 0.28, height: 0.22 });
  if (annotationMode === "blur") { const point = imagePoint(event); addAnnotation("blur", { x: Math.max(0, Math.min(0.8, point.x - 0.1)), y: Math.max(0, Math.min(0.88, point.y - 0.06)), width: 0.2, height: 0.12 }); }
});

ui.annotationApply.addEventListener("click", () => {
  const annotation = currentAnnotations().find((item) => item.id === selectedAnnotation); if (!annotation) return;
  recordAnnotationChange(); annotation.text = ui.annotationText.value.slice(0, 140); annotation.generated = false; persist(); renderAnnotations(); clearVideo(); status("Annotation text updated.");
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

api("/api/status").then((response) => response.json()).then(({ voice, aiConfigured }) => {
  ui.voice.textContent = `Local voice · ${voice}`;
  ui.aiSetup.textContent = aiConfigured ? "AI connected" : "Connect AI";
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
