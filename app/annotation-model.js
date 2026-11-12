(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StepfilmAnnotations = api;
})(typeof self === "object" ? self : globalThis, function () {
  const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, Number(value) || 0));
  const id = () => `annotation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
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
    return { schemaVersion: 2, title: String(input.title || "Untitled guide").slice(0, 150), steps: input.steps.map((step) => {
      const sourceImage = step.sourceImage || step.image;
      if (!sourceImage) throw new Error("A step is missing its screenshot.");
      return { id: step.id || `step-${Math.random().toString(36).slice(2, 10)}`, image: sourceImage, sourceImage, text: String(step.text || "").slice(0, 400), annotations: Array.isArray(step.annotations) ? step.annotations.map((a) => {
        const type = a.type || "marker";
        const legacySize = type === "callout" && Number(a.width) <= 0.06 && Number(a.height) <= 0.06;
        return { id: a.id || id(), type, x: legacySize ? clamp(Number(a.x) - 0.265, 0, 0.42) : clamp(a.x), y: legacySize ? clamp(Number(a.y) - 0.06, 0, 0.88) : clamp(a.y), width: legacySize ? 0.58 : clamp(a.width, 0.02, 1), height: legacySize ? 0.12 : clamp(a.height, 0.02, 1), text: String(a.text || a.label || a.content || "").slice(0, 140) };
      }) : [] };
    }) };
  }
  return { clamp, create, geometry, normalizeGuide, wrapWords };
});
