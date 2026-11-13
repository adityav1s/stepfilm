let recorder;
let stream;
let microphoneStream;
let captureId;
let chunkIndex = 0;
let uploadQueue = Promise.resolve();
let recordingError = "";
let microphoneError = "";
let recordingStartedAt = 0;
const API = "http://127.0.0.1:8765";
function reportFailure(message) {
  recordingError = String(message || "The tab recording stopped unexpectedly.");
  chrome.runtime.sendMessage({ target: "background", type: "recording-failed", error: recordingError }).catch(() => {});
}

function uploadChunk(blob) {
  if (!blob.size) return;
  const index = chunkIndex++;
  uploadQueue = uploadQueue.then(async () => {
    const response = await fetch(`${API}/api/capture/chunk?captureId=${encodeURIComponent(captureId)}&index=${index}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: blob,
    });
    if (!response.ok) throw new Error(`Video upload failed (${response.status}).`);
  }).catch((error) => {
    reportFailure(error.message || error);
    if (recorder?.state === "recording") recorder.stop();
  });
}

async function start(message) {
  if (recorder?.state === "recording") throw new Error("A tab recording is already active.");
  captureId = message.captureId;
  chunkIndex = 0;
  recordingError = "";
  microphoneError = "";
  const source = message.captureMode === "desktop" ? "desktop" : "tab";
  const tabStream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { mandatory: { chromeMediaSource: source, chromeMediaSourceId: message.streamId, maxWidth: 2560, maxHeight: 1440, maxFrameRate: 30 } },
  });
  if (message.microphone) {
    try { microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }, video: false }); }
    catch (error) { tabStream.getTracks().forEach((track) => track.stop()); throw new Error(`Microphone permission failed: ${error.message || error}. Turn narration off to continue without a mic, or allow microphone access and retry.`); }
  }
  stream = new MediaStream([...tabStream.getVideoTracks(), ...(microphoneStream?.getAudioTracks() || [])]);
  tabStream.getAudioTracks().forEach((track) => track.stop());
  const mimeType = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find((type) => MediaRecorder.isTypeSupported(type));
  if (!mimeType) { stream.getTracks().forEach((track) => track.stop()); stream = undefined; throw new Error("This Chrome build cannot record WebM video."); }
  recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 2_500_000 });
  recorder.addEventListener("dataavailable", (event) => uploadChunk(event.data));
  recorder.addEventListener("error", (event) => reportFailure(event.error?.message || "The tab recording stopped unexpectedly."));
  stream.getVideoTracks()[0]?.addEventListener("ended", () => reportFailure("Chrome stopped sharing the active tab."));
  microphoneStream?.getAudioTracks()[0]?.addEventListener("ended", () => {
    microphoneError = "The microphone disconnected; the tab recording is still active, but later narration was not captured.";
    chrome.runtime.sendMessage({ target: "background", type: "microphone-failed", error: microphoneError }).catch(() => {});
  });
  await new Promise((resolve) => { recorder.addEventListener("start", resolve, { once: true }); recorder.start(4000); });
  recordingStartedAt = Date.now();
  const started = await fetch(`${API}/api/capture/recording/started`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ captureId, startedAt: recordingStartedAt }),
  });
  if (!started.ok) {
    recorder.stop();
    stream?.getTracks().forEach((track) => track.stop());
    microphoneStream?.getTracks().forEach((track) => track.stop());
    microphoneStream = undefined;
    stream = undefined;
    recorder = undefined;
    throw new Error("Could not synchronize the microphone recording with screenshot steps.");
  }
}

async function stop() {
  if (recorder?.state === "recording") {
    await new Promise((resolve) => { recorder.addEventListener("stop", resolve, { once: true }); recorder.stop(); });
  }
  stream?.getTracks().forEach((track) => track.stop());
  microphoneStream?.getTracks().forEach((track) => track.stop());
  microphoneStream = undefined;
  stream = undefined;
  await uploadQueue;
  if (recordingError) {
    throw new Error(`${recordingError} Reopen the capture popup to retry Stop. The guide screenshots are preserved.`);
  }
  const response = await fetch(`${API}/api/capture/recording/finish`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ captureId, chunks: chunkIndex, duration: Math.max(0, (Date.now() - recordingStartedAt) / 1000) }),
  });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || "Could not finish the tab recording."); }
  recorder = undefined;
  return response.json();
}

async function captureSnapshot() {
  const track = stream?.getVideoTracks()[0];
  if (!track || track.readyState !== "live") throw new Error("The screen/window stream is not active. Retry Stop if recording stopped.");
  let frame;
  if (typeof ImageCapture === "function") {
    frame = await new ImageCapture(track).grabFrame();
  } else {
    const video = document.createElement("video");
    video.muted = true; video.playsInline = true; video.srcObject = stream;
    await video.play();
    if (video.requestVideoFrameCallback) await new Promise((resolve) => video.requestVideoFrameCallback(resolve));
    else await new Promise((resolve) => setTimeout(resolve, 100));
    frame = video;
    const canvas = document.createElement("canvas"); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
    const context = canvas.getContext("2d"); context.drawImage(frame, 0, 0);
    video.pause(); video.srcObject = null;
    return canvas.toDataURL("image/png");
  }
  const canvas = document.createElement("canvas"); canvas.width = frame.width; canvas.height = frame.height;
  canvas.getContext("2d").drawImage(frame, 0, 0);
  frame.close?.();
  return canvas.toDataURL("image/png");
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.target !== "offscreen") return;
  if (message.type === "start") start(message).then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
  else if (message.type === "stop") stop().then((result) => sendResponse({ ok: true, result })).catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
  else if (message.type === "snapshot") captureSnapshot().then((image) => sendResponse({ ok: true, image })).catch((error) => sendResponse({ ok: false, error: String(error.message || error) }));
  else if (message.type === "status") sendResponse({ recording: recorder?.state === "recording", microphone: microphoneStream?.getAudioTracks().some((track) => track.readyState === "live") || false, captureId, error: recordingError, microphoneError });
  return true;
});
