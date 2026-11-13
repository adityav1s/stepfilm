(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StepfilmAnnotations = api;
})(typeof self === "object" ? self : globalThis, function () {
  const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, Number(value) || 0));
  const normalizeFocus = (focus) => {
    if (!focus || typeof focus !== "object") return { x: 0.5, y: 0.5, scale: 1 };
    return { x: clamp(focus.x, 0, 1), y: clamp(focus.y, 0, 1), scale: clamp(focus.scale, 1, 2.4) };
  };
  const id = () => `annotation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const captions = (items) => Array.isArray(items) ? items.filter((item) => item && Number.isFinite(Number(item.start)) && Number.isFinite(Number(item.end)) && Number(item.end) >= Number(item.start)).map((item) => ({ start: Math.max(0, Number(item.start)), end: Math.max(0, Number(item.end)), text: String(item.text || "").slice(0, 500), translations: item.translations && typeof item.translations === "object" && !Array.isArray(item.translations) ? Object.fromEntries(Object.entries(item.translations).filter(([language, text]) => /^[a-z]{2,3}(?:-[A-Z]{2,4})?$/.test(language) && typeof text === "string").map(([language, text]) => [language, text.slice(0, 1000)])) : {} })) : [];
  const normalizeAudioSource = (step) => {
    if (step.audioSource === "recorded") return "recorded";
    if (step.audioSource === "local") return "local";
    return typeof step.voiceover === "string" && step.voiceover.startsWith("data:audio/mpeg;base64,") ? "synthetic" : "local";
  };
  const create = (type, x, y, width, height, text = "") => ({ id: id(), type, x: clamp(x), y: clamp(y), width: clamp(width, 0.02, 1), height: clamp(height, 0.02, 1), text: String(text).slice(0, 140) });
  function wrapWords(text, capacity) {
    const lines = []; let line = "";
    for (const word of String(text || "Explain this").trim().split(/\s+/)) {
      if (word.length > capacity) {
        if (line) { lines.push(line); line = ""; }
        for (let index = 0; index < word.length; index += capacity) lines.push(word.slice(index, index + capacity));
      } else if (!line) line = word;
      else if ((line.length + 1 + word.length) <= capacity) line += ` ${word}`;
      else { lines.push(line); line = word; }
    }
    if (line) lines.push(line);
    return lines.length ? lines : ["Explain this"];
  }
  function geometry(annotation, imageWidth, imageHeight) {
    if (annotation.type !== "callout") return { x: annotation.x * imageWidth, y: annotation.y * imageHeight, width: annotation.width * imageWidth, height: annotation.height * imageHeight };
    const width = Math.min(imageWidth, Math.max(annotation.width * imageWidth, Math.min(imageWidth, 180)));
    let fontSize = 14; let lineHeight = 16; let lines = wrapWords(annotation.text || annotation.label || annotation.content || "Explain this", Math.max(8, Math.floor((width - 24) / 7)));
    while ((lines.length * lineHeight + 10) > imageHeight && fontSize > 10) { fontSize -= 1; lineHeight = Math.round(fontSize * 1.15); lines = wrapWords(annotation.text || annotation.label || annotation.content || "Explain this", Math.max(8, Math.floor((width - 24) / (fontSize * 0.52)))); }
    const height = Math.min(imageHeight, Math.max(annotation.height * imageHeight, lines.length * lineHeight + 10));
    return { x: annotation.x * imageWidth, y: Math.min(annotation.y * imageHeight, imageHeight - height), width, height, fontSize, lineHeight, lines };
  }
  function normalizeGuide(input) {
    if (!input || !Array.isArray(input.steps) || !input.steps.length) throw new Error("That project does not contain any steps.");
    const narrationAudioId = /^[a-f0-9]{32}$/.test(input.narrationAudioId || "") ? input.narrationAudioId : "";
    const incomingBrand = input.brand && typeof input.brand === "object" ? input.brand : {};
    const brand = {
      theme: ["clean", "editorial", "bold"].includes(incomingBrand.theme) ? incomingBrand.theme : "clean",
      accent: typeof incomingBrand.accent === "string" && /^#[0-9a-f]{6}$/i.test(incomingBrand.accent) ? incomingBrand.accent : "#6d8eff",
      titleStyle: ["standard", "compact", "display"].includes(incomingBrand.titleStyle) ? incomingBrand.titleStyle : "standard",
      logo: typeof incomingBrand.logo === "string" && incomingBrand.logo.length <= 500000 && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(incomingBrand.logo) ? incomingBrand.logo : "",
    };
    const stepIds = new Set();
    const normalizedStepId = (candidate) => {
      let value = typeof candidate === "string" && candidate ? candidate : `step-${Math.random().toString(36).slice(2, 10)}`;
      while (stepIds.has(value)) value = `step-${Math.random().toString(36).slice(2, 10)}`;
      stepIds.add(value);
      return value;
    };
    return { schemaVersion: 2, title: String(input.title || "Untitled guide").slice(0, 150), aiTitle: input.aiTitle === true, titleEdited: input.titleEdited === true, brand, transcript: String(input.transcript || "").slice(0, 100000), transcriptSegments: Array.isArray(input.transcriptSegments) ? input.transcriptSegments.filter((segment) => segment && Number.isFinite(Number(segment.start)) && Number.isFinite(Number(segment.end))).map((segment) => ({ start: Number(segment.start), end: Number(segment.end), text: String(segment.text || "").slice(0, 500), stepIndex: Math.max(0, Number(segment.stepIndex) || 0) })) : [], narrationAudioId, narrationAudioUrl: narrationAudioId ? `/api/narration/${narrationAudioId}` : "", captureDuration: Math.max(0, Number(input.captureDuration) || 0),
      capturedClips: Array.isArray(input.capturedClips) ? input.capturedClips.filter((clip) => clip && /^[a-f0-9]{32}$/.test(clip.id)).map((clip) => ({ id: clip.id, url: `/api/media/${clip.id}`, label: String(clip.label || "Captured tab video").slice(0, 100) })) : [],
      steps: input.steps.map((step) => {
      const stepId = normalizedStepId(step.id);
      if (step.kind === "video") {
        if (!/^[a-f0-9]{32}$/.test(step.videoId || "")) throw new Error("A video step is missing its captured clip.");
        return { id: stepId, kind: "video", videoId: step.videoId, recordedAudioId: /^[a-f0-9]{32}$/.test(step.recordedAudioId || "") ? step.recordedAudioId : "", videoUrl: `/api/media/${step.videoId}`, text: String(step.text || "").slice(0, 400), description: String(step.description || "").slice(0, 400), videoAnalysis: String(step.videoAnalysis || "").slice(0, 1200), captureAt: Math.max(0, Number(step.at ?? step.captureAt) || 0), captureStart: Math.max(0, Number(step.captureStart) || 0), captureEnd: Math.max(0, Number(step.captureEnd) || 0), captions: captions(step.captions), audioSource: normalizeAudioSource(step), aiGenerated: step.aiGenerated === true, voiceover: typeof step.voiceover === "string" ? step.voiceover : "", voiceLabel: String(step.voiceLabel || ""), voicePace: ["slower", "brisk"].includes(step.voicePace) ? step.voicePace : "natural", voiceInstructions: String(step.voiceInstructions || "").slice(0, 300), annotations: [] };
      }
      const sourceImage = step.sourceImage || step.image;
      if (!sourceImage) throw new Error("A step is missing its screenshot.");
      return { id: stepId, image: sourceImage, sourceImage, text: String(step.text || "").slice(0, 400), description: String(step.description || "").slice(0, 400), captureAt: Math.max(0, Number(step.at ?? step.captureAt) || 0), captureStart: Math.max(0, Number(step.captureStart) || 0), captureEnd: Math.max(0, Number(step.captureEnd) || 0), captions: captions(step.captions), audioSource: normalizeAudioSource(step), aiGenerated: step.aiGenerated === true, voiceover: typeof step.voiceover === "string" ? step.voiceover : "", voiceLabel: String(step.voiceLabel || ""), voicePace: ["slower", "brisk"].includes(step.voicePace) ? step.voicePace : "natural", voiceInstructions: String(step.voiceInstructions || "").slice(0, 300), focus: normalizeFocus(step.focus), annotations: Array.isArray(step.annotations) ? step.annotations.map((a) => {
        const type = a.type || "marker";
        const legacySize = type === "callout" && Number(a.width) <= 0.06 && Number(a.height) <= 0.06;
        return { id: a.id || id(), type, x: legacySize ? clamp(Number(a.x) - 0.265, 0, 0.42) : clamp(a.x), y: legacySize ? clamp(Number(a.y) - 0.06, 0, 0.88) : clamp(a.y), width: legacySize ? 0.58 : clamp(a.width, 0.02, 1), height: legacySize ? 0.12 : clamp(a.height, 0.02, 1), text: String(a.text || a.label || a.content || "").slice(0, 140), generated: a.generated === true };
      }) : [] };
    }) };
  }
  return { clamp, create, geometry, normalizeFocus, normalizeGuide, wrapWords };
});
