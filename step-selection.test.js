const assert = require("assert");
const { moveSelected, duplicateSelected, deleteSelected } = require("./app/step-selection.js");
const steps = [
  { id: "a", kind: "image", text: "A", annotations: [{ id: "ann-a" }] },
  { id: "b", kind: "video", videoId: "video-b", recordedAudioId: "audio-b", text: "B" },
  { id: "c", kind: "image", text: "C", captions: [{ start: 1, end: 2, text: "C cue" }] },
  { id: "d", kind: "image", text: "D" },
];

assert.deepStrictEqual(moveSelected(steps, ["b", "d"], -1).map((step) => step.id), ["b", "a", "d", "c"]);
assert.deepStrictEqual(moveSelected(steps, ["b", "d"], 1).map((step) => step.id), ["a", "c", "b", "d"]);
const duplicated = duplicateSelected(steps, ["b", "c"], (() => { let id = 0; return () => `copy-${++id}`; })());
assert.deepStrictEqual(duplicated.steps.map((step) => step.id), ["a", "b", "c", "copy-1", "copy-2", "d"]);
assert.strictEqual(duplicated.steps[3].videoId, "video-b");
assert.strictEqual(duplicated.steps[3].recordedAudioId, "audio-b");
assert.notStrictEqual(duplicated.steps[4].captions, steps[2].captions);
assert.deepStrictEqual(deleteSelected(steps, ["b", "d"]).steps.map((step) => step.id), ["a", "c"]);
assert.deepStrictEqual(deleteSelected(steps, ["a", "b", "c", "d"]).steps, steps, "deleting the final step set is rejected");
console.log("step selection operations: PASS");
