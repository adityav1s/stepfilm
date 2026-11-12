const state = { active: false, busy: false, lastCapture: 0 };

async function restoreState() {
  const saved = await chrome.storage.local.get(["stepfilmCapturing", "stepfilmCount"]);
  state.active = saved.stepfilmCapturing === true;
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
  if (!state.active || state.busy || Date.now() - state.lastCapture < 700) return;
  state.busy = true;
  try {
    const image = await chrome.tabs.captureVisibleTab(null, { format: "png" });
    const result = await api("/api/capture/step", { image });
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
  if (message.type === "start") {
    state.active = true;
    chrome.storage.local.set({ stepfilmCapturing: true });
    chrome.action.setBadgeText({ text: "REC" });
  }
  if (message.type === "stop") {
    state.active = false;
    chrome.storage.local.set({ stepfilmCapturing: false, stepfilmCount: 0 });
    chrome.action.setBadgeText({ text: "" });
  }
  if ((message.type === "page-ready" || message.type === "page-action") && state.active && sender.tab) capture(sender.tab);
});
