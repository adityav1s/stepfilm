(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StepfilmAnnotations = api;
})(typeof self === "object" ? self : globalThis, function () {
  const clamp = (value, min = 0, max = 1) => Math.max(min, Math.min(max, Number(value) || 0));
  const id = () => `annotation-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  const create = (type, x, y, width, height, text = "") => ({ id: id(), type, x: clamp(x), y: clamp(y), width: clamp(width, 0.02, 1), height: clamp(height, 0.02, 1), text: String(text).slice(0, 140) });
  function normalizeGuide(input) {
    if (!input || !Array.isArray(input.steps) || !input.steps.length) throw new Error("That project does not contain any steps.");
    return { schemaVersion: 2, title: String(input.title || "Untitled guide").slice(0, 150), steps: input.steps.map((step) => {
      const sourceImage = step.sourceImage || step.image;
      if (!sourceImage) throw new Error("A step is missing its screenshot.");
      return { id: step.id || `step-${Math.random().toString(36).slice(2, 10)}`, image: sourceImage, sourceImage, text: String(step.text || "").slice(0, 400), annotations: Array.isArray(step.annotations) ? step.annotations.map((a) => ({ id: a.id || id(), type: a.type || "marker", x: clamp(a.x), y: clamp(a.y), width: clamp(a.width, 0.02, 1), height: clamp(a.height, 0.02, 1), text: String(a.text || "").slice(0, 140) })) : [] };
    }) };
  }
  return { clamp, create, normalizeGuide };
});
