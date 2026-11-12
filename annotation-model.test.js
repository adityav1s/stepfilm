const assert = require("assert");
const { create, normalizeGuide } = require("./app/annotation-model.js");

const image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
const guide = normalizeGuide({ title: "Legacy", steps: [{ image, text: "Click target" }] });
assert.equal(guide.schemaVersion, 2);
assert.equal(guide.steps[0].sourceImage, image);
assert.deepEqual(guide.steps[0].annotations, []);
const annotation = create("arrow", 0.2, 0.3, 0.4, 0.01, "Target");
assert.equal(annotation.type, "arrow");
assert.equal(annotation.text, "Target");
assert(annotation.id.startsWith("annotation-"));
assert(annotation.x >= 0 && annotation.x <= 1);
const legacyCallout = normalizeGuide({ title: "Legacy callout", steps: [{ image, text: "", annotations: [{ type: "callout", x: 0.5, y: 0.5, width: 0.05, height: 0.05, label: "Open dashboard" }] }] }).steps[0].annotations[0];
assert.equal(legacyCallout.text, "Open dashboard");
assert.equal(legacyCallout.width, 0.58);
assert.equal(legacyCallout.height, 0.12);
console.log("annotation model: PASS");
