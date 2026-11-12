const $ = (id) => document.getElementById(id);
let capturing = false;
const api = async (path, body) => {
  let response;
  try { response = await fetch(`http://127.0.0.1:8765${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }
  catch (_) { throw new Error("Stepfilm is not running. Start it with: cd /Users/aditya/Downloads/stepfilm && python3 server.py"); }
  const raw = await response.text();
  let data;
  try { data = JSON.parse(raw); }
  catch (_) { throw new Error("Port 8765 is serving an old/static server. Stop it and run: cd /Users/aditya/Downloads/stepfilm && python3 server.py"); }
  if (!response.ok) throw new Error(data.error || "Stepfilm is not running.");
  return data;
};
function status(message, error = false) { $("status").textContent = message; $("status").className = `status${error ? " error" : ""}`; }
async function restore() {
  try {
    let response;
    try { response = await fetch("http://127.0.0.1:8765/api/capture/status"); }
    catch (_) { throw new Error("Stepfilm is not running. Start it with: cd /Users/aditya/Downloads/stepfilm && python3 server.py"); }
    const raw = await response.text();
    let state;
    try { state = JSON.parse(raw); } catch (_) { throw new Error("Port 8765 is serving an old/static server. Restart Stepfilm from /Users/aditya/Downloads/stepfilm."); }
    if (state.capturing) { capturing = true; $("start").disabled = true; $("title").disabled = true; $("step").disabled = false; $("stop").disabled = false; $("count").textContent = `${state.count} step${state.count === 1 ? "" : "s"}`; status("Capture in progress."); await chrome.storage.local.set({ stepfilmCapturing: true, stepfilmCount: state.count }); await chrome.runtime.sendMessage({ type: "start" }); await chrome.action.setBadgeText({ text: "REC" }); await chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" }); }
  } catch (error) { status(error.message, true); }
}
$("start").onclick = async () => {
  try { await api("/api/capture/start", { title: $("title").value }); await chrome.runtime.sendMessage({ type: "start" }); capturing = true; $("start").disabled = true; $("title").disabled = true; $("step").disabled = false; $("stop").disabled = false; await chrome.action.setBadgeText({ text: "REC" }); await chrome.action.setBadgeBackgroundColor({ color: "#e56b6f" }); status("Recording clicks. Use Capture current step for any missed action."); }
  catch (error) { status(error.message, true); }
};
$("step").onclick = async () => {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const image = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
    const result = await api("/api/capture/step", { image });
    $("count").textContent = `${result.count} step${result.count === 1 ? "" : "s"}`;
    status("Step captured.");
  } catch (error) { status(error.message, true); }
};
$("stop").onclick = async () => {
  try { await chrome.runtime.sendMessage({ type: "stop" }); await api("/api/capture/stop", {}); await chrome.action.setBadgeText({ text: "" }); await chrome.tabs.create({ url: "http://127.0.0.1:8765/?capture=latest" }); window.close(); }
  catch (error) { status(error.message, true); }
};
restore();
