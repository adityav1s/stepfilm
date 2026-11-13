const assert = require("assert");
const { toHtml, toMarkdown, toPrintHtml } = require("./app/guide-export.js");
const guide = { title: "Billing workflow", steps: [
  { image: "data:image/png;base64,AAA", description: "Open the Billing page", text: "Choose Billing from the navigation." },
  { image: "data:image/png;base64,BBB", description: "Select the invoice list", text: "Select Invoices" }
], transcript: "First, choose Billing. Then open invoices." };
const html = toHtml(guide);
const printHtml = toPrintHtml(guide);
assert(printHtml.includes("@media print") && printHtml.includes("page-break-inside:avoid") && printHtml.includes("Choose Billing from the navigation."));
assert(html.includes("<section data-step=\"1\">") && html.includes("<section data-step=\"2\">") );
assert(html.includes("data:image/png;base64,AAA") && html.includes("Select Invoices") && html.includes("<h2>Transcript</h2>"));
const brandedHtml = toHtml({ title: "Branded", brand: { theme: "bold", accent: "#ff3366", titleStyle: "display", logo: "data:image/png;base64,AAA" }, steps: [{ image: "data:image/png;base64,AAA", text: "Continue" }] });
assert(brandedHtml.includes("theme-bold") && brandedHtml.includes("#ff3366") && brandedHtml.includes("title-display") && brandedHtml.includes("class=\"brand-logo\""));
const markdown = toMarkdown(guide);
assert(markdown.includes("## Step 1") && markdown.includes("Open the Billing page") && markdown.includes("Choose Billing from the navigation."));
assert(markdown.includes("data:image/png;base64,BBB") && markdown.includes("## Transcript"));
const clipHtml = toHtml({ title: "Mixed", steps: [{ kind: "video", videoId: "0123456789abcdef0123456789abcdef", videoUrl: "/api/media/0123456789abcdef0123456789abcdef", text: "Review the transition" }] });
assert(clipHtml.includes("<video controls") && clipHtml.includes("Review the transition"));
const portableHtml = toHtml({ title: "Offline tutorial", steps: [{ kind: "video", videoId: "0123456789abcdef0123456789abcdef", portableVideoUrl: "media/0123456789abcdef0123456789abcdef.webm", portableCaptionTracks: [{ src: "captions/step-1-en.vtt", language: "en", label: "English", default: true }], captions: [{ start: 0, end: 1, text: "Open the menu" }], text: "Open the menu" }] });
assert(portableHtml.includes('src="media/0123456789abcdef0123456789abcdef.webm"') && portableHtml.includes('src="captions/step-1-en.vtt"') && portableHtml.includes("<summary>Captions</summary>"));
assert(toMarkdown({ title: "Mixed", steps: [{ kind: "video", videoId: "0123456789abcdef0123456789abcdef", text: "Review the transition" }] }).includes("[Play captured clip]"));
console.log("guide export: PASS");
