(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.StepfilmGuideExport = api;
})(typeof self === "object" ? self : globalThis, function () {
  const escapeHtml = (value) => String(value || "").replace(/[&<>\"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[char]));
  const safeTitle = (guide) => escapeHtml(guide.title || "Stepfilm guide");
  function safeBrand(guide) {
    const brand = guide.brand && typeof guide.brand === "object" ? guide.brand : {};
    return {
      theme: ["clean", "editorial", "bold"].includes(brand.theme) ? brand.theme : "clean",
      accent: typeof brand.accent === "string" && /^#[0-9a-f]{6}$/i.test(brand.accent) ? brand.accent : "#6d8eff",
      titleStyle: ["standard", "compact", "display"].includes(brand.titleStyle) ? brand.titleStyle : "standard",
      logo: typeof brand.logo === "string" && brand.logo.length <= 500000 && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(brand.logo) ? brand.logo : "",
    };
  }
  function toHtml(guide) {
    const brand = safeBrand(guide);
    const steps = guide.steps.map((step, index) => {
      const tracks = Array.isArray(step.portableCaptionTracks) ? step.portableCaptionTracks.map((track) => `<track kind="captions" src="${escapeHtml(track.src)}" srclang="${escapeHtml(track.language)}" label="${escapeHtml(track.label || track.language)}"${track.default ? " default" : ""}>`).join("") : (step.portableCaptionUrl ? `<track kind="captions" src="${escapeHtml(step.portableCaptionUrl)}" srclang="en" label="English" default>` : "");
      const media = step.kind === "video"
        ? `<video controls playsinline src="${escapeHtml(step.portableVideoUrl || `http://127.0.0.1:8765${step.videoUrl || `/api/media/${step.videoId}`}`)}">${tracks}Captured browser tab clip</video>`
        : `<img src="${escapeHtml(step.image)}" alt="Step ${index + 1}: ${escapeHtml(step.text || "Screenshot")}">`;
      const captionList = Array.isArray(step.captions) && step.captions.length ? `<details class="step-captions"><summary>Captions</summary><ol>${step.captions.map((caption) => `<li>${escapeHtml(caption.text || "")}</li>`).join("")}</ol></details>` : "";
      return `<section data-step="${index + 1}"><h2>Step ${index + 1}</h2>${step.description ? `<p data-description="true">${escapeHtml(step.description)}</p>` : ""}${media}<p>${escapeHtml(step.text || "Silent step")}</p>${captionList}</section>`;
    }).join("\n");
    const transcript = guide.transcript ? `<section><h2>Transcript</h2><p>${escapeHtml(guide.transcript).replace(/\n/g, "<br>")}</p></section>` : "";
    const logo = brand.logo ? `<img class="brand-logo" src="${escapeHtml(brand.logo)}" alt="Guide logo">` : "";
    return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${safeTitle(guide)}</title><style>:root{--brand-accent:${brand.accent}}body{font:16px system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#172033}header{border-bottom:3px solid var(--brand-accent);padding-bottom:16px;margin-bottom:8px}.brand-logo{display:block;width:auto;height:auto;max-width:180px;max-height:64px;object-fit:contain;border:0;margin:0 0 14px}section{border-top:1px solid #d8dee9;padding:24px 0}img,video{display:block;max-width:100%;height:auto;border:1px solid #d8dee9;border-radius:8px}h1{font-size:32px;margin:0;color:#172033}h2{font-size:20px}.step-number{color:var(--brand-accent);font-weight:800}body.theme-editorial{font-family:Georgia,'Times New Roman',serif;background:#f5f1e9;color:#322d27;padding:40px 28px}body.theme-editorial header{border-color:#b47a45}body.theme-bold header{background:#101827;color:#fff;padding:22px;border-radius:12px}body.theme-bold h1{color:#fff}body.title-compact h1{font-size:22px;letter-spacing:.06em;text-transform:uppercase}body.title-display h1{font-size:clamp(38px,7vw,64px);letter-spacing:-.045em}</style></head><body class="theme-${brand.theme} title-${brand.titleStyle}"><header>${logo}<h1>${safeTitle(guide)}</h1></header>${steps}${transcript}</body></html>`;
  }
  function toPrintHtml(guide) {
    const printStyles = "@media print{@page{size:auto;margin:.65in}body{max-width:none;margin:0;padding:0;color:#111;background:#fff!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}header{break-after:avoid}section{break-inside:avoid;page-break-inside:avoid;padding:16px 0}img,video{max-width:100%;max-height:65vh;object-fit:contain;break-inside:avoid;page-break-inside:avoid}details{display:block}details>summary{display:none}details>:not(summary){display:block!important}}";
    return toHtml(guide).replace("</style>", `${printStyles}</style>`);
  }
  function toMarkdown(guide) {
    return [`# ${guide.title || "Stepfilm guide"}`, "", ...guide.steps.flatMap((step, index) => step.kind === "video"
      ? [`## Step ${index + 1}`, "", ...(step.description ? [step.description, ""] : []), `[Play captured clip](http://127.0.0.1:8765${step.videoUrl || `/api/media/${step.videoId}`})`, "", step.text || "Silent step", ""]
      : [`## Step ${index + 1}`, "", ...(step.description ? [step.description, ""] : []), `![Step ${index + 1}](${step.image})`, "", step.text || "Silent step", ""]), ...(guide.transcript ? ["## Transcript", "", guide.transcript, ""] : [])].join("\n");
  }
  return { toHtml, toMarkdown, toPrintHtml };
});
