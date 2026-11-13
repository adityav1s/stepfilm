const $ = (id) => document.getElementById(id);
let capturing = false;
let recordingSince = 0;
let narrationEnabled = false;
let captureSource = "tab";
let timer;
const api = async (path, body) => {
  let response;
  try { response = await fetch(`http://127.0.0.1:8765${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch (_) { throw new Error("Stepfilm is not running. Start it with: python3 server.py"); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Stepfilm is not running.");
  return data;
};
function status(message, error = false) { $("status").textContent = message; $("status").className = `status${error ? " error" : ""}`; }
function paintTimer() {
  if (!recordingSince) { $("recording").textContent = ""; return; }
  const seconds = Math.max(0, Math.floor((Date.now() - recordingSince) / 1000));
  $("recording").textContent = `● ${captureSource === "desktop" ? "Screen/window" : "Tab"}${narrationEnabled ? " + mic" : ""} · ${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
function showCapture(count, title, narration = false, source = "tab") {
  capturing = true;
  $("start").disabled = true;
  $("title").disabled = true;
  $("source").disabled = true;
  $("mic").disabled = true;
  $("mic").checked = narration;
  narrationEnabled = narration;
  captureSource = source === "desktop" ? "desktop" : "tab";
  $("source").value = captureSource;
  $("step").disabled = false;
  $("stop").disabled = false;
  $("title").value = title || $("title").value;
  $("count").textContent = `${count} step${count === 1 ? "" : "s"}`;
}
async function restore() {
  try {
    const response = await fetch("http://127.0.0.1:8765/api/capture/status");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Stepfilm is unavailable.");
    if (!data.capturing) return;
    const saved = await chrome.storage.local.get(["stepfilmCaptureTabId", "stepfilmCaptureTabTitle", "stepfilmCaptureMode"]);
    showCapture(data.count, data.title, data.narration, data.captureMode || saved.stepfilmCaptureMode || "tab");
    const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const onCaptureTab = captureSource === "desktop" || activeTab?.id === saved.stepfilmCaptureTabId;
    const state = await chrome.runtime.sendMessage({ target: "background", type: "recording-status" });
    if (!state?.recording) {
      $("recording").textContent = captureSource === "desktop" ? "Screen/window recording stopped unexpectedly. Keep the screenshot guide or restart capture." : "Tab video stopped unexpectedly. Stop video to keep the screenshot guide, then restart for another recording.";
      $("stop").disabled = true;
      $("step").disabled = true;
      $("keepScreenshots").hidden = false;
      status(state?.error || `The ${captureSource === "desktop" ? "screen/window" : "tab"} video stream is not active. No video is being recorded.`, true);
      return;
    }
    recordingSince = data.recordingStartedAt || Date.now();
    $("keepScreenshots").hidden = true;
    paintTimer();
    clearInterval(timer); timer = setInterval(paintTimer, 1000);
    await chrome.storage.local.set({ stepfilmCapturing: true, stepfilmCount: data.count, stepfilmCaptureId: data.captureId, stepfilmCaptureMode: captureSource });
    await chrome.action.setBadgeText({ text: "REC" });
    await chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" });
    let message = captureSource === "desktop" ? `Screen/window video is recording${data.narration ? " with microphone narration" : ""}. Use Capture current step at each desktop action.` : onCaptureTab ? `Screenshots and tab video are recording${data.narration ? " with microphone narration" : ""}.` : `Video is recording the original tab “${saved.stepfilmCaptureTabTitle || "Browser tab"}”. Return to it to capture more screenshots.`;
    if (state.microphoneError) message += ` ${state.microphoneError}`;
    else if (data.narration && !state.microphone) message += " Microphone is inactive; tab video continues.";
    status(message, Boolean(state.microphoneError || (data.narration && !state.microphone)));
    if (!onCaptureTab) $("step").disabled = true;
  } catch (error) { status(error.message || "Stepfilm is unavailable.", true); }
}

async function startCapture(includeMic = $("mic").checked) {
  $("start").disabled = true;
  $("continueSilent").hidden = true;
  try {
    const selectedSource = $("source").value;
    let streamId = "";
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("Select a browser tab before starting capture.");
    if (includeMic) {
      let check;
      try { check = await navigator.mediaDevices.getUserMedia({ audio: true, video: false }); }
      catch (_) {
        $("start").disabled = false;
        $("continueSilent").hidden = false;
        status("Microphone access was denied. You can allow it in Chrome and retry, or continue this capture without narration.", true);
        return;
      }
      check.getTracks().forEach((track) => track.stop());
    }
    if (selectedSource === "desktop") {
      streamId = await new Promise((resolve) => chrome.desktopCapture.chooseDesktopMedia(["screen", "window"], resolve));
      if (!streamId) throw new Error("Screen/window capture was cancelled. Select a source to retry, or choose Browser tab.");
    }
    if (selectedSource === "tab") streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    const started = await api("/api/capture/start", { title: $("title").value, recording: true, narration: includeMic, captureMode: selectedSource });
    const result = await chrome.runtime.sendMessage({ target: "background", type: "start-recording", captureId: started.captureId, streamId, captureTabId: tab.id, captureTabTitle: tab.title, microphone: includeMic, captureMode: selectedSource });
    if (!result?.ok) {
      await api("/api/capture/cancel", { captureId: started.captureId }).catch(() => {});
      throw new Error(result?.error || "Tab video did not start. No capture was started.");
    }
    await chrome.runtime.sendMessage({ type: "capture-started", captureId: started.captureId, captureTabId: tab.id, captureTabTitle: tab.title, captureMode: selectedSource });
    try { if (selectedSource === "tab") await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] }); }
    catch (_) { status("Video is recording this tab. Chrome blocked automatic click screenshots here; use Capture current step on a supported website.", true); }
    const current = await fetch("http://127.0.0.1:8765/api/capture/status").then((response) => response.json());
    showCapture(current.count || 0, $("title").value, includeMic, selectedSource);
    $("keepScreenshots").hidden = true;
    recordingSince = Date.now(); paintTimer(); timer = setInterval(paintTimer, 1000);
    await chrome.action.setBadgeText({ text: "REC" });
    await chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" });
    status(selectedSource === "desktop" ? (includeMic ? "Screen/window and microphone are recording. Use Capture current step at each action." : "Screen/window video is recording. Use Capture current step at each action.") : (includeMic ? "Tab and microphone are recording. Speak through each action; screenshots mark the transcript steps." : "Screenshots and tab video are recording together."));
  } catch (error) {
    $("start").disabled = false;
    status(`${error.message || "Could not start capture."} Check the selected capture permission and retry.`, true);
  }
}

$("start").onclick = () => startCapture();
$("continueSilent").onclick = () => {
  $("mic").checked = false;
  startCapture(false);
};

$("step").onclick = async () => {
  try {
    if (captureSource === "desktop") {
      const at = Date.now();
      const result = await chrome.runtime.sendMessage({ target: "background", type: "capture-desktop-step", at });
      if (!result?.ok) throw new Error(result?.error || "Could not capture this desktop step.");
      $("count").textContent = `${result.count} step${result.count === 1 ? "" : "s"}`;
      await chrome.storage.local.set({ stepfilmCount: result.count });
      status("Desktop screenshot added. Screen/window video continued recording.");
      return;
    }
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const target = await chrome.storage.local.get(["stepfilmCaptureTabId", "stepfilmCaptureTabTitle"]);
    if (tab?.id !== target.stepfilmCaptureTabId) throw new Error(`Return to “${target.stepfilmCaptureTabTitle || "the original tab"}” to capture a screenshot. Video continues on that original tab.`);
    const at = Date.now();
    const image = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    const result = await api("/api/capture/step", { captureId: (await chrome.storage.local.get("stepfilmCaptureId")).stepfilmCaptureId, image, at });
    $("count").textContent = `${result.count} step${result.count === 1 ? "" : "s"}`;
    await chrome.storage.local.set({ stepfilmCount: result.count });
    status("Screenshot added. Tab video continued recording.");
  } catch (error) { status(error.message || "Screenshot capture failed. Video continues recording.", true); }
};

$("stop").onclick = async () => {
  $("stop").disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ target: "background", type: "stop-recording" });
    if (!result?.ok) throw new Error(result?.error || "Could not finish the tab video.");
    await api("/api/capture/stop", { captureId: (await chrome.storage.local.get("stepfilmCaptureId")).stepfilmCaptureId });
    await chrome.runtime.sendMessage({ type: "stop" });
    await chrome.storage.local.set({ stepfilmCaptureId: "", stepfilmRecording: false });
    clearInterval(timer); await chrome.action.setBadgeText({ text: "" });
    await chrome.tabs.create({ url: "http://127.0.0.1:8765/?capture=latest" }); window.close();
  } catch (error) { $("stop").disabled = false; $("keepScreenshots").hidden = false; status(`${error.message || "Capture could not finish."} Your screenshots are still available; retry Stop or keep them without video.`, true); }
};
$("keepScreenshots").onclick = async () => {
  try {
    const captureId = (await chrome.storage.local.get("stepfilmCaptureId")).stepfilmCaptureId;
    await api("/api/capture/recording/abandon", { captureId });
    await api("/api/capture/stop", { captureId });
    await chrome.runtime.sendMessage({ type: "stop" });
    await chrome.storage.local.set({ stepfilmCaptureId: "", stepfilmRecording: false });
    clearInterval(timer); await chrome.action.setBadgeText({ text: "" });
    await chrome.tabs.create({ url: "http://127.0.0.1:8765/?capture=latest" }); window.close();
  } catch (error) { status(`${error.message || "Could not preserve the screenshot guide."} Screenshots remain in the active capture.`, true); }
};
restore();
