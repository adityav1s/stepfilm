const state = { active: false, busy: false, lastCapture: 0, captureId: "", captureTabId: undefined, captureMode: "tab" };

async function restoreState() {
  const saved = await chrome.storage.local.get(["stepfilmCapturing", "stepfilmCount", "stepfilmCaptureId", "stepfilmCaptureTabId", "stepfilmCaptureMode"]);
  state.active = saved.stepfilmCapturing === true;
  state.captureId = saved.stepfilmCaptureId || "";
  state.captureTabId = Number.isInteger(saved.stepfilmCaptureTabId) ? saved.stepfilmCaptureTabId : undefined;
  state.captureMode = saved.stepfilmCaptureMode === "desktop" ? "desktop" : "tab";
  if (state.active) {
    await chrome.action.setBadgeText({ text: "REC" });
    await chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" });
  } else if (saved.stepfilmCount === undefined) {
    await chrome.action.setBadgeText({ text: "" });
  }
}

restoreState().catch(() => {});
async function api(path, body) {
  const response = await fetch(`http://127.0.0.1:8765${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (!response.ok) throw new Error("Stepfilm server rejected the capture.");
  return response.json();
}
async function capture(tab) {
  if (!state.active || state.captureMode !== "tab" || tab.id !== state.captureTabId || state.busy || Date.now() - state.lastCapture < 700) return;
  const capturedAt = Date.now();
  state.busy = true;
  try {
    const [visible] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
    if (visible?.id !== state.captureTabId) {
      state.busy = false;
      return;
    }
    const image = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    const result = await api("/api/capture/step", { image, captureId: state.captureId, at: capturedAt });
    state.lastCapture = Date.now();
    await chrome.storage.local.set({ stepfilmCount: result.count });
    await chrome.action.setBadgeText({ text: String(result.count) });
    await chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" });
  } catch (error) {
    await chrome.storage.local.set({ stepfilmError: String(error) });
    await chrome.action.setBadgeText({ text: "!" });
    await chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" });
  }
  state.busy = false;
}
chrome.runtime.onMessage.addListener((message, sender) => {
  if (message.target && message.target !== "background") return;
  if (message.type === "start") {
    state.active = true;
    chrome.storage.local.set({ stepfilmCapturing: true });
    chrome.action.setBadgeText({ text: "REC" });
  }
  if (message.type === "stop") {
    state.active = false;
    state.captureTabId = undefined;
    state.captureId = "";
    state.captureMode = "tab";
    chrome.storage.local.set({ stepfilmCapturing: false, stepfilmCount: 0, stepfilmCaptureId: "", stepfilmCaptureTabId: null, stepfilmCaptureTabTitle: "", stepfilmCaptureMode: "tab" });
    chrome.action.setBadgeText({ text: "" });
  }
  if (message.type === "capture-started") {
    state.captureId = message.captureId;
    state.captureTabId = message.captureTabId;
    state.captureMode = message.captureMode === "desktop" ? "desktop" : "tab";
    state.active = true;
    chrome.storage.local.set({ stepfilmCaptureId: state.captureId, stepfilmCaptureTabId: state.captureTabId, stepfilmCaptureTabTitle: message.captureTabTitle, stepfilmCaptureMode: state.captureMode, stepfilmCapturing: true });
  }
  if (message.type === "recording-failed") {
    state.active = false;
    chrome.storage.local.set({ stepfilmRecording: false, stepfilmRecordingError: message.error || "Tab recording stopped unexpectedly." });
    chrome.action.setBadgeText({ text: "!" });
    chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" });
  }
  if (message.type === "microphone-failed") {
    chrome.storage.local.set({ stepfilmMicrophoneError: message.error || "Microphone recording stopped unexpectedly." });
  }
  if ((message.type === "page-ready" || message.type === "page-action") && state.active && sender.tab) capture(sender.tab);
});

async function ensureOffscreen() {
  const url = chrome.runtime.getURL("offscreen.html");
  const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"], documentUrls: [url] });
  if (!contexts.length) await chrome.offscreen.createDocument({ url: "offscreen.html", reasons: ["USER_MEDIA"], justification: "Record the active browser tab while capturing individual guide screenshots." });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.target !== "background" || !["start-recording", "stop-recording", "recording-status", "capture-desktop-step"].includes(message.type)) return;
  (async () => {
    try {
      if (message.type === "start-recording") {
        await ensureOffscreen();
        state.captureId = message.captureId;
        state.captureTabId = message.captureTabId;
        state.captureMode = message.captureMode === "desktop" ? "desktop" : "tab";
        state.active = true;
        await chrome.storage.local.set({ stepfilmCaptureId: state.captureId, stepfilmCaptureTabId: state.captureTabId, stepfilmCaptureTabTitle: message.captureTabTitle, stepfilmCaptureMode: state.captureMode, stepfilmCapturing: true });
        const result = await chrome.runtime.sendMessage({ target: "offscreen", type: "start", streamId: message.streamId, captureId: state.captureId, microphone: message.microphone === true, captureMode: state.captureMode });
        if (!result?.ok) throw new Error(result?.error || "Could not start tab recording.");
        await chrome.storage.local.set({ stepfilmRecording: true, stepfilmRecordingError: "", stepfilmMicrophoneError: "", stepfilmNarration: message.microphone === true, stepfilmRecordingStartedAt: Date.now() });
        sendResponse({ ok: true });
      } else if (message.type === "capture-desktop-step") {
        if (!state.active || state.captureMode !== "desktop") throw new Error("There is no active screen/window capture.");
        const snapshot = await chrome.runtime.sendMessage({ target: "offscreen", type: "snapshot" });
        if (!snapshot?.ok) throw new Error(snapshot?.error || "Could not read a frame from the screen capture.");
        const result = await api("/api/capture/step", { captureId: state.captureId, image: snapshot.image, at: Number(message.at) || Date.now() });
        sendResponse({ ok: true, count: result.count });
      } else if (message.type === "stop-recording") {
        state.active = false;
        const result = await chrome.runtime.sendMessage({ target: "offscreen", type: "stop", captureId: state.captureId });
        await chrome.storage.local.set({ stepfilmRecording: false, stepfilmRecordingError: result?.error || "" });
        if (!result?.ok) throw new Error(result?.error || "Could not stop tab recording.");
        sendResponse({ ok: true });
      } else if (message.type === "recording-status") {
        const result = await chrome.runtime.sendMessage({ target: "offscreen", type: "status" }).catch(() => ({ recording: false }));
        const saved = await chrome.storage.local.get(["stepfilmRecordingStartedAt", "stepfilmRecordingError", "stepfilmMicrophoneError"]);
        sendResponse({ ...result, recordingStartedAt: saved.stepfilmRecordingStartedAt, error: result.error || saved.stepfilmRecordingError, microphoneError: saved.stepfilmMicrophoneError });
      }
    } catch (error) {
      if (message.type === "start-recording") {
        state.active = false;
        await chrome.storage.local.set({ stepfilmCapturing: false, stepfilmRecording: false, stepfilmRecordingError: String(error?.message || error) });
      }
      sendResponse({ ok: false, error: String(error?.message || error) });
    }
  })();
  return true;
});
