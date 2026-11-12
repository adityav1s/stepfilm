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
console.log("annotation model: PASS");
