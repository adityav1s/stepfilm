// Stepfilm guide renderer + in-editor player.
// drawStep() paints one annotated step (spotlight, click ring, callout with a
// pointer, step number, redactions) onto a canvas. It is shared by the step
// thumbnails, flattened export images and the Guidde-style Play preview.
(function (root) {
  "use strict";
  // Brand theme: mirrors guide_frames.py Theme so the in-editor player, the
  // thumbnails and the rendered MP4 share colours.
  const DEFAULT_ACCENT = "#6d8eff";
  const hexRgb = (value) => { if (!/^#[0-9a-f]{6}$/i.test(value || "")) value = DEFAULT_ACCENT; return [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16)); };
  const mixRgb = (a, b, t) => a.map((v, i) => Math.round(v + (b[i] - v) * t));
  const css = (rgb, alpha = 1) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${alpha})`;
  function themeFor(brand) {
    brand = brand && typeof brand === "object" ? brand : {};
    const accent = hexRgb(brand.accent);
    const name = ["clean", "editorial", "bold"].includes(brand.theme) ? brand.theme : "clean";
    const lum = (0.2126 * accent[0] + 0.7152 * accent[1] + 0.0722 * accent[2]) / 255;
    const onAccent = lum > 0.62 ? [20, 24, 34] : [255, 255, 255];
    let bgTop, bgBottom, ink, sub;
    if (name === "bold") { bgTop = mixRgb(accent, [10, 12, 20], 0.25); bgBottom = mixRgb(accent, [10, 12, 20], 0.6); ink = [255, 255, 255]; sub = [230, 234, 245]; }
    else if (name === "editorial") { bgTop = [246, 241, 231]; bgBottom = mixRgb([246, 241, 231], accent, 0.18); ink = [28, 26, 22]; sub = [82, 76, 66]; }
    else { bgTop = mixRgb([244, 246, 251], accent, 0.08); bgBottom = mixRgb([228, 233, 245], accent, 0.28); ink = [16, 22, 38]; sub = [70, 80, 102]; }
    return { name, accentRgb: accent, accent: css(accent), accentA: (alpha) => css(accent, alpha), onAccent: css(onAccent), bgTop: css(bgTop), bgBottom: css(bgBottom), ink: css(ink), sub: css(sub), bar: { clean: 5, editorial: 7, bold: 10 }[name], titleStyle: ["standard", "compact", "display"].includes(brand.titleStyle) ? brand.titleStyle : "standard" };
  }
  const DEFAULT_THEME = themeFor({});
  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const ease = (t) => { t = clamp(t, 0, 1); return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; };
  const lerp = (a, b, t) => a + (b - a) * t;
  const FULL_VIEW = { x: 0.5, y: 0.5, scale: 1 };

  function loadImage(source) {
    return new Promise((resolve, reject) => { const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error("A step screenshot could not be loaded.")); image.src = source; });
  }

  // The element the step is about: the highlight box (or a small box around
  // the click point), plus the point the cursor should click.
  function targetOf(step) {
    const annotations = step?.annotations || [];
    const highlight = annotations.find((a) => a.type === "highlight");
    const click = annotations.find((a) => a.type === "click");
    let point;
    if (click) point = { x: click.x + click.width / 2, y: click.y + click.height / 2 };
    else if (highlight) point = { x: highlight.x + highlight.width / 2, y: highlight.y + highlight.height / 2 };
    else if (step?.action?.point && step.action.type !== "scene") point = { ...step.action.point };
    else {
      const marker = annotations.find((a) => a.type === "marker" || a.type === "arrow");
      if (marker) point = marker.type === "arrow" ? { x: marker.x + marker.width, y: marker.y + marker.height } : { x: marker.x + marker.width / 2, y: marker.y + marker.height / 2 };
    }
    if (!point) return null;
    const box = highlight ? { x: highlight.x, y: highlight.y, width: highlight.width, height: highlight.height } : { x: point.x - 0.02, y: point.y - 0.02, width: 0.04, height: 0.04 };
    return { point, box, clicks: Boolean(click) };
  }

  // Where an auto callout should sit (image fractions). Small targets such as
  // checkboxes/radios/icons sit beside their label, so the guard covers the
  // whole row to the right; then pick the side with room, in the export's
  // order (below, right, above, left), never overlapping the guard.
  function placeCallout(step, text, aspect = 16 / 10) {
    const target = targetOf(step);
    if (!target) return null;
    let g = { ...target.box };
    if (g.width < 0.05) { const left = Math.max(0.005, g.x - 0.01); g = { x: left, y: g.y, width: Math.min(0.99, g.x + g.width + 0.3) - left, height: g.height }; }
    const gx = 0.012, gy = 0.012 * aspect;
    const guard = { x0: g.x - gx, y0: g.y - gy, x1: g.x + g.width + gx, y1: g.y + g.height + gy };
    const chars = String(text || "").trim().length || 10;
    const textPx = chars * 8.4, maxPx = 330, innerMax = maxPx - 64;
    const lines = Math.max(1, Math.ceil(textPx / innerMax));
    const width = Math.max(0.14, Math.min(maxPx, textPx + 64) / 1280), height = Math.min(0.4, ((lines * 19 + 22) / 1280) * aspect);
    const cx = target.point.x, cy = g.y + g.height / 2, gap = 0.018;
    const fit = (x, y) => ({ x: clamp(x, 0.01, 0.99 - width), y: clamp(y, 0.01, 0.99 - height) });
    const candidates = [
      ["below", fit(cx - width * 0.25, guard.y1 + gap * aspect * 0.4), 0.99 - (guard.y1 + gap * aspect * 0.4 + height)],
      ["right", fit(guard.x1 + gap, cy - height / 2), 0.99 - (guard.x1 + gap + width)],
      ["above", fit(cx - width * 0.25, guard.y0 - gap * aspect * 0.4 - height), guard.y0 - gap * aspect * 0.4 - height - 0.01],
      ["left", fit(guard.x0 - gap - width, cy - height / 2), guard.x0 - gap - width - 0.01],
    ];
    const overlaps = (p) => !(p.x + width <= guard.x0 || p.x >= guard.x1 || p.y + height <= guard.y0 || p.y >= guard.y1);
    let best = null;
    candidates.forEach(([side, pos, room], order) => {
      if (room < 0 || overlaps(pos)) return;
      const score = Math.min(room, 0.09) * 100 - order * 0.01;
      if (!best || score > best.score) best = { score, side, ...pos };
    });
    if (!best) {
      // Nothing fits cleanly: take the side with the most room, clamped.
      const [side, pos] = candidates.reduce((a, b) => (b[2] > a[2] ? b : a));
      best = { side, ...pos };
    }
    return { x: best.x, y: best.y, width, height, side: best.side };
  }

  // A camera view that zooms toward the target while keeping the callout in frame.
  function targetView(step, imageWidth, imageHeight, frameWidth, frameHeight, wanted = 1.45) {
    const target = targetOf(step);
    const focus = step?.focus && Number(step.focus.scale) > 1 ? step.focus : null;
    if (!target) return focus ? { x: focus.x, y: focus.y, scale: Number(focus.scale) } : FULL_VIEW;
    let { x: left, y: top } = target.box; let right = target.box.x + target.box.width; let bottom = target.box.y + target.box.height;
    for (const a of step.annotations || []) {
      if (a.type !== "callout" && a.type !== "click") continue;
      left = Math.min(left, a.x); top = Math.min(top, a.y); right = Math.max(right, a.x + a.width); bottom = Math.max(bottom, a.y + a.height + (a.type === "callout" ? 0.05 : 0));
    }
    const base = Math.min(frameWidth / imageWidth, frameHeight / imageHeight);
    const fitWidth = frameWidth / (imageWidth * base), fitHeight = frameHeight / (imageHeight * base);
    const limit = Math.min(fitWidth / Math.max(0.05, right - left), fitHeight / Math.max(0.05, bottom - top)) * 0.86;
    const scale = clamp(Math.min(Math.max(focus ? Number(focus.scale) : 0, wanted), limit), 1, 2.4);
    return { x: (left + right) / 2, y: (top + bottom) / 2, scale };
  }

  function viewTransform(imageWidth, imageHeight, width, height, view = FULL_VIEW) {
    const base = Math.min(width / imageWidth, height / imageHeight);
    const s = base * (view.scale || 1);
    const sw = imageWidth * s, sh = imageHeight * s;
    let tx = width / 2 - view.x * sw, ty = height / 2 - view.y * sh;
    tx = sw <= width ? (width - sw) / 2 : clamp(tx, width - sw, 0);
    ty = sh <= height ? (height - sh) / 2 : clamp(ty, height - sh, 0);
    return { tx, ty, sw, sh, x: (fx) => tx + fx * sw, y: (fy) => ty + fy * sh };
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function wrapText(ctx, text, maxWidth) {
    const lines = []; let line = "";
    for (const word of String(text || "").trim().split(/\s+/).filter(Boolean)) {
      const next = line ? `${line} ${word}` : word;
      if (ctx.measureText(next).width <= maxWidth || !line) line = next;
      else { lines.push(line); line = word; }
    }
    if (line) lines.push(line);
    return lines.length ? lines : [""];
  }

  function drawCursor(ctx, x, y, size, pressed) {
    ctx.save(); ctx.translate(x, y); const k = size / 24 * (pressed ? 0.88 : 1); ctx.scale(k, k);
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, 19); ctx.lineTo(4.6, 14.6); ctx.lineTo(8, 22); ctx.lineTo(11.2, 20.6); ctx.lineTo(7.8, 13.4); ctx.lineTo(14, 13.4); ctx.closePath();
    ctx.shadowColor = "rgba(0,0,0,.45)"; ctx.shadowBlur = 6; ctx.shadowOffsetY = 2;
    ctx.fillStyle = "#fff"; ctx.fill(); ctx.shadowColor = "transparent"; ctx.lineWidth = 1.6; ctx.strokeStyle = "#0b0f18"; ctx.stroke();
    ctx.restore();
  }

  function pointerSide(bubble, target) {
    const cx = target.x + target.width / 2, cy = target.y + target.height / 2;
    if (target.x + target.width <= bubble.x) return "left";
    if (target.x >= bubble.x + bubble.width) return "right";
    if (cy < bubble.y) return "top";
    if (cy > bubble.y + bubble.height) return "bottom";
    return cx < bubble.x + bubble.width / 2 ? "left" : "right";
  }

  function drawCallout(ctx, annotation, map, targetBox, number, unit, alpha, theme) {
    const fontSize = Math.max(7, (number ? 15 : 22) * unit), padX = (number ? 13 : 20) * unit, padY = (number ? 10 : 14) * unit, badge = number ? 22 * unit : 0;
    let y = map.y(annotation.y);
    let width = number ? Math.max(130 * unit, annotation.width * map.sw) : 400 * unit;
    ctx.save();
    ctx.font = `600 ${fontSize}px 'Avenir Next', Avenir, Inter, system-ui, sans-serif`;
    const extra = padX * 2 + (badge ? badge + 8 * unit : 0);
    const lines = wrapText(ctx, annotation.text || "Click here", width - extra);
    // Shrink the bubble to its text, keeping the side nearest the target fixed.
    const fitted = Math.max(...lines.map((line) => ctx.measureText(line).width)) + extra + 2 * unit;
    let x = map.x(annotation.x);
    if (fitted < width) { if (targetBox && targetBox.x > x + width / 2) x += width - fitted; width = fitted; }
    const lineHeight = fontSize * 1.28;
    const height = Math.max(badge + padY * 2, lines.length * lineHeight + padY * 2);
    // Keep the bubble inside the visible screenshot.
    const minX = Math.max(0, map.tx) + 4 * unit, maxX = Math.min(ctx.canvas.width, map.tx + map.sw) - 4 * unit;
    const minY = Math.max(0, map.ty) + 4 * unit, maxY = Math.min(ctx.canvas.height, map.ty + map.sh) - 4 * unit;
    x = clamp(x, minX, Math.max(minX, maxX - width)); y = clamp(y, minY, Math.max(minY, maxY - height));
    const bubble = { x, y, width, height };
    const pop = 0.86 + 0.14 * ease(alpha);
    ctx.globalAlpha = clamp(alpha, 0, 1);
    ctx.translate(x + width / 2, y + height / 2); ctx.scale(pop, pop); ctx.translate(-(x + width / 2), -(y + height / 2));
    ctx.beginPath(); roundRect(ctx, x, y, width, height, 14 * unit);
    if (targetBox) {
      const side = pointerSide(bubble, targetBox);
      const tcx = targetBox.x + targetBox.width / 2, tcy = targetBox.y + targetBox.height / 2;
      const size = 12 * unit;
      if (side === "left" || side === "right") {
        const py = clamp(tcy, y + 12 * unit + size, y + height - 12 * unit - size); const ex = side === "left" ? x : x + width; const dir = side === "left" ? -1 : 1;
        ctx.moveTo(ex, py - size); ctx.lineTo(ex + dir * size * 1.25, py); ctx.lineTo(ex, py + size);
      } else {
        const px = clamp(tcx, x + 12 * unit + size, x + width - 12 * unit - size); const ey = side === "top" ? y : y + height; const dir = side === "top" ? -1 : 1;
        ctx.moveTo(px - size, ey); ctx.lineTo(px, ey + dir * size * 1.25); ctx.lineTo(px + size, ey);
      }
    }
    ctx.shadowColor = "rgba(0,0,0,.35)"; ctx.shadowBlur = 14 * unit; ctx.shadowOffsetY = 4 * unit;
    ctx.fillStyle = theme.accent; ctx.fill("nonzero");
    ctx.shadowColor = "transparent";
    let textX = x + padX;
    if (badge) {
      const bx = x + padX + badge / 2, by = y + padY + badge / 2;
      ctx.beginPath(); ctx.arc(bx, by, badge / 2, 0, Math.PI * 2); ctx.fillStyle = theme.onAccent; ctx.fill();
      ctx.fillStyle = theme.accent; ctx.font = `800 ${Math.max(6, 12 * unit)}px 'Avenir Next', Avenir, Inter, system-ui, sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(String(number), bx, by + 0.5 * unit);
      textX += badge + 8 * unit;
    }
    ctx.font = `600 ${fontSize}px 'Avenir Next', Avenir, Inter, system-ui, sans-serif`; ctx.fillStyle = theme.onAccent; ctx.textAlign = "left"; ctx.textBaseline = "top";
    const textTop = y + (height - lines.length * lineHeight) / 2 + fontSize * 0.12;
    lines.forEach((line, index) => ctx.fillText(line, textX, textTop + index * lineHeight));
    ctx.restore();
  }

  // Paint one step. o: { width, height, view, spotlight, callout, click, ripple, cursor, number, unit }
  function drawStep(ctx, image, step, o = {}) {
    const width = o.width || ctx.canvas.width, height = o.height || ctx.canvas.height;
    const iw = image.naturalWidth || image.width, ih = image.naturalHeight || image.height;
    const inset = clamp(o.inset || 0, 0, 0.2);
    const frameW = width * (1 - inset * 2), frameH = height * (1 - inset * 2), offX = width * inset, offY = height * inset;
    const inner = viewTransform(iw, ih, frameW, frameH, o.view || FULL_VIEW);
    const map = { tx: inner.tx + offX, ty: inner.ty + offY, sw: inner.sw, sh: inner.sh, x: (fx) => inner.tx + offX + fx * inner.sw, y: (fy) => inner.ty + offY + fy * inner.sh };
    const unit = o.unit || Math.max(width, height * 16 / 9) / 1280;
    const a = (key) => (o[key] === undefined ? 1 : clamp(o[key], 0, 1));
    const theme = o.theme || DEFAULT_THEME;
    ctx.save();
    if (o.background) ctx.fillStyle = o.background;
    else { const g = ctx.createLinearGradient(0, 0, 0, height); g.addColorStop(0, theme.bgTop); g.addColorStop(1, theme.bgBottom); ctx.fillStyle = g; }
    ctx.fillRect(0, 0, width, height);
    if (inset > 0) {
      const cx = Math.max(offX, map.tx), cy = Math.max(offY, map.ty), cw = Math.min(offX + frameW, map.tx + map.sw) - cx, ch = Math.min(offY + frameH, map.ty + map.sh) - cy;
      ctx.save(); ctx.beginPath(); roundRect(ctx, cx, cy, cw, ch, 14 * unit);
      ctx.shadowColor = "rgba(16,22,38,.28)"; ctx.shadowBlur = 30 * unit; ctx.shadowOffsetY = 10 * unit; ctx.fillStyle = "#fff"; ctx.fill(); ctx.shadowColor = "transparent";
      ctx.clip(); ctx.drawImage(image, map.tx, map.ty, map.sw, map.sh); ctx.restore();
    } else ctx.drawImage(image, map.tx, map.ty, map.sw, map.sh);
    const annotations = step.annotations || [];
    const box = (an) => ({ x: map.x(an.x), y: map.y(an.y), width: an.width * map.sw, height: an.height * map.sh });
    // Redactions are always baked in, whatever the animation state.
    for (const an of annotations.filter((item) => item.type === "blur")) {
      const b = box(an); const sx = an.x * iw, sy = an.y * ih, sw = Math.max(1, an.width * iw), sh = Math.max(1, an.height * ih);
      const small = document.createElement("canvas"); small.width = Math.max(1, Math.round(sw / 14)); small.height = Math.max(1, Math.round(sh / 14));
      small.getContext("2d").drawImage(image, sx, sy, sw, sh, 0, 0, small.width, small.height);
      ctx.imageSmoothingEnabled = false; ctx.drawImage(small, b.x, b.y, b.width, b.height); ctx.imageSmoothingEnabled = true;
      ctx.fillStyle = "rgba(16,24,39,.6)"; ctx.fillRect(b.x, b.y, b.width, b.height);
    }
    const highlights = annotations.filter((item) => item.type === "highlight").map(box);
    const pad = 4 * unit;
    if (highlights.length && a("spotlight") > 0) {
      ctx.beginPath(); ctx.rect(0, 0, width, height);
      for (const h of highlights) roundRect(ctx, h.x - pad, h.y - pad, h.width + pad * 2, h.height + pad * 2, 8 * unit);
      ctx.fillStyle = `rgba(8,11,20,${0.41 * a("spotlight")})`; ctx.fill("evenodd");
      ctx.globalAlpha = a("spotlight");
      for (const h of highlights) {
        ctx.beginPath(); roundRect(ctx, h.x - pad - 3 * unit, h.y - pad - 3 * unit, h.width + (pad + 3 * unit) * 2, h.height + (pad + 3 * unit) * 2, 11 * unit);
        ctx.lineWidth = Math.max(1, 2 * unit); ctx.strokeStyle = "rgba(255,255,255,.9)"; ctx.stroke();
        ctx.beginPath(); roundRect(ctx, h.x - pad, h.y - pad, h.width + pad * 2, h.height + pad * 2, 8 * unit);
        ctx.lineWidth = Math.max(1.5, 4 * unit); ctx.strokeStyle = theme.accent; ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    for (const an of annotations) {
      const b = box(an);
      if (an.type === "arrow" && a("callout") > 0) {
        ctx.save(); ctx.globalAlpha = a("callout"); ctx.strokeStyle = theme.accent; ctx.fillStyle = theme.accent; ctx.lineWidth = Math.max(2, 6 * unit); ctx.lineCap = "round";
        const ex = b.x + b.width, ey = b.y + b.height; ctx.beginPath(); ctx.moveTo(b.x, b.y); ctx.lineTo(ex, ey); ctx.stroke();
        const angle = Math.atan2(b.height, b.width), head = 20 * unit; ctx.beginPath(); ctx.moveTo(ex, ey); ctx.lineTo(ex - head * Math.cos(angle - Math.PI / 6), ey - head * Math.sin(angle - Math.PI / 6)); ctx.lineTo(ex - head * Math.cos(angle + Math.PI / 6), ey - head * Math.sin(angle + Math.PI / 6)); ctx.closePath(); ctx.fill(); ctx.restore();
      } else if (an.type === "marker" && a("callout") > 0) {
        ctx.save(); ctx.globalAlpha = a("callout"); const r = Math.max(9 * unit, Math.min(b.width, b.height) / 2); const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
        ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fillStyle = theme.accent; ctx.fill(); ctx.lineWidth = Math.max(1.5, 3 * unit); ctx.strokeStyle = "#fff"; ctx.stroke();
        ctx.fillStyle = theme.onAccent; ctx.font = `800 ${r}px 'Avenir Next', Avenir, Inter, system-ui, sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText((an.text || "1").slice(0, 3), cx, cy); ctx.restore();
      } else if (an.type === "click" && a("click") > 0) {
        const cx = b.x + b.width / 2, cy = b.y + b.height / 2, r = Math.max(7 * unit, 0.018 * map.sw);
        ctx.save(); ctx.globalAlpha = a("click");
        ctx.beginPath(); ctx.arc(cx, cy, r * 1.55, 0, Math.PI * 2); ctx.fillStyle = theme.accentA(0.27); ctx.fill();
        ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.lineWidth = Math.max(2, 5 * unit); ctx.strokeStyle = "#fff"; ctx.stroke(); ctx.lineWidth = Math.max(1.2, 3 * unit); ctx.strokeStyle = theme.accent; ctx.stroke();
        ctx.beginPath(); ctx.arc(cx, cy, r * 0.3, 0, Math.PI * 2); ctx.fillStyle = theme.accent; ctx.fill(); ctx.restore();
      }
    }
    const target = targetOf(step);
    if (o.ripple !== undefined && o.ripple !== null && target && o.ripple > 0 && o.ripple < 1) {
      const cx = map.x(target.point.x), cy = map.y(target.point.y), r = 0.018 * map.sw;
      for (const offset of [0, 0.25]) {
        const t = clamp((o.ripple - offset) / (1 - offset), 0, 1); if (t <= 0 || t >= 1) continue;
        ctx.beginPath(); ctx.arc(cx, cy, (10 + 46 * t) * unit, 0, Math.PI * 2); ctx.lineWidth = Math.max(2, 4 * unit * (1 - t)); ctx.strokeStyle = theme.accentA(1 - t); ctx.stroke();
      }
    }
    if (a("callout") > 0) {
      const targetBox = target ? box(target.box) : null;
      for (const an of annotations.filter((item) => item.type === "callout")) drawCallout(ctx, an, map, targetBox, o.number, unit, a("callout"), theme);
    }
    if (o.cursor) drawCursor(ctx, map.x(o.cursor.x), map.y(o.cursor.y), 26 * unit, o.cursor.pressed);
    ctx.restore();
    return map;
  }

  async function renderStepCanvas(step, { width, height, view, number, unit, theme, zoomToTarget = false, maxScale = 1.6 } = {}) {
    const image = await loadImage(step.sourceImage || step.image);
    const iw = image.naturalWidth, ih = image.naturalHeight;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(width || iw); canvas.height = Math.round(height || (canvas.width * ih / iw));
    let chosen = view || FULL_VIEW;
    if (!view && zoomToTarget) { const wanted = targetView(step, iw, ih, canvas.width, canvas.height, maxScale); chosen = { ...wanted, scale: Math.min(wanted.scale, maxScale) }; }
    drawStep(canvas.getContext("2d"), image, step, { width: canvas.width, height: canvas.height, view: chosen, number, unit, theme });
    return canvas;
  }

  // ---------------------------------------------------------------- player
  const estimateSeconds = (text) => Math.max(1.6, String(text || "").trim().split(/\s+/).filter(Boolean).length / 2.5 + 0.4);

  function makeVoice(text, fetchAudio, options = {}) {
    // Returns a controller; .ready resolves once audio can start.
    const voice = { duration: estimateSeconds(text), ended: false, started: false, kind: "none" };
    let audio; let utterance; let resolveEnd; let stopped = false;
    voice.done = new Promise((resolve) => { resolveEnd = () => { voice.ended = true; resolve(); }; });
    if (!String(text || "").trim()) { voice.ready = Promise.resolve(); voice.isReady = true; voice.started = true; resolveEnd(); voice.play = voice.pause = voice.resume = voice.stop = () => {}; return voice; }
    const useSpeech = () => {
      voice.kind = "speech";
      if (!("speechSynthesis" in root)) { voice.kind = "silent"; return; }
      utterance = new SpeechSynthesisUtterance(text); utterance.rate = 1.02;
      utterance.onend = () => resolveEnd(); utterance.onerror = () => resolveEnd();
    };
    voice.ready = (async () => {
      try {
        if (options.src) { audio = new Audio(options.src); }
        else {
          // A slow local voice should not stall the guide: fall back after 6s.
          const url = await Promise.race([fetchAudio(text), new Promise((_, reject) => setTimeout(() => reject(new Error("Narration audio timed out")), 6000))]);
          if (stopped) return; audio = new Audio(url);
        }
        await new Promise((resolve, reject) => { audio.onloadedmetadata = resolve; audio.onerror = reject; audio.load(); });
        if (Number.isFinite(audio.duration)) voice.duration = audio.duration;
        if (options.start) audio.currentTime = options.start;
        if (options.end > options.start) voice.duration = options.end - options.start;
        audio.onended = () => resolveEnd();
        voice.element = audio;
        voice.kind = "audio";
      } catch (_) { audio = undefined; useSpeech(); }
      voice.isReady = true;
    })();
    voice.play = () => {
      if (stopped || voice.ended) return;
      voice.started = true;
      if (audio) {
        audio.play().catch(() => { audio = undefined; useSpeech(); voice.play(); });
        if (options.end > options.start) { const stopAt = options.end; audio.ontimeupdate = () => { if (audio && audio.currentTime >= stopAt) { audio.pause(); resolveEnd(); } }; }
      } else if (utterance) {
        speechSynthesis.cancel(); speechSynthesis.speak(utterance);
        // Some browsers never fire onend (or have no voices); don't stall the guide.
        voice.safety = setTimeout(resolveEnd, (voice.duration * 1.6 + 2) * 1000);
      }
      else setTimeout(resolveEnd, voice.duration * 1000);
    };
    voice.pause = () => { if (audio) audio.pause(); else if (utterance && voice.started) speechSynthesis.pause(); };
    voice.resume = () => { if (!voice.started || voice.ended) return; if (audio) audio.play().catch(() => {}); else if (utterance) speechSynthesis.resume(); };
    voice.forceEnd = () => { if (audio) audio.pause(); if (utterance) speechSynthesis.cancel(); resolveEnd(); };
    voice.stop = () => { stopped = true; clearTimeout(voice.safety); if (audio) { audio.pause(); audio.src = ""; } if (utterance) speechSynthesis.cancel(); };
    return voice;
  }

  function el(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }

  class GuidePlayer {
    constructor(options) {
      this.options = options;
      this.audioCache = new Map();
      this.build();
    }

    build() {
      const overlay = el("div", "player-overlay"); overlay.id = "guidePlayer"; overlay.hidden = true;
      overlay.setAttribute("role", "dialog"); overlay.setAttribute("aria-modal", "true"); overlay.setAttribute("aria-label", "Guide player");
      const shell = el("div", "player-shell");
      const head = el("div", "player-head");
      this.titleNode = el("strong", "player-title", "Guide");
      this.counter = el("span", "player-counter", "");
      const close = el("button", "player-close", "✕ Close preview"); close.type = "button"; close.id = "playerClose"; close.setAttribute("aria-label", "Close preview");
      close.addEventListener("click", () => this.close());
      const headText = el("div"); headText.append(this.titleNode, this.counter);
      head.append(headText, close);
      const stage = el("div", "player-stage");
      this.canvas = el("canvas", "player-canvas"); this.canvas.width = 1280; this.canvas.height = 720;
      this.canvas.addEventListener("click", () => this.toggle());
      this.video = el("video", "player-video"); this.video.playsInline = true; this.video.hidden = true;
      this.caption = el("div", "player-caption"); this.caption.id = "playerCaption"; this.caption.setAttribute("aria-live", "polite");
      this.bigPlay = el("button", "player-bigplay", "▶"); this.bigPlay.type = "button"; this.bigPlay.setAttribute("aria-label", "Play"); this.bigPlay.addEventListener("click", () => this.play());
      this.voiceBadge = el("span", "player-voice-badge"); this.voiceBadge.id = "playerSpeaking"; this.voiceBadge.hidden = true; this.voiceBadge.setAttribute("aria-live", "polite");
      const bars = el("span", "speaking-bars"); bars.setAttribute("aria-hidden", "true"); bars.append(el("i"), el("i"), el("i"), el("i"));
      this.badgeTextNode = el("span", "speaking-text"); this.voiceBadge.append(bars, this.badgeTextNode);
      stage.append(this.canvas, this.video, this.caption, this.bigPlay, this.voiceBadge);
      this.progress = el("div", "player-progress"); this.progress.id = "playerProgress"; this.progress.setAttribute("aria-label", "Guide progress");
      const controls = el("div", "player-controls");
      const button = (id, label, text, handler) => { const b = el("button", "player-button", text); b.type = "button"; b.id = id; b.setAttribute("aria-label", label); b.title = label; b.addEventListener("click", handler); return b; };
      this.prevButton = button("playerPrev", "Previous step", "⏮", () => this.jump(this.index - 1));
      this.playButton = button("playerToggle", "Play", "▶", () => this.toggle());
      this.nextButton = button("playerNext", "Next step", "⏭", () => this.jump(this.index + 1));
      this.ccButton = button("playerCaptions", "Toggle captions", "CC", () => { this.captionsOn = !this.captionsOn; this.ccButton.classList.toggle("active", this.captionsOn); this.updateCaption(); });
      this.ccButton.classList.add("active"); this.captionsOn = true;
      this.timeNode = el("span", "player-time", "");
      const spacer = el("span", "player-spacer");
      controls.append(this.prevButton, this.playButton, this.nextButton, this.timeNode, spacer, this.ccButton);
      this.strip = el("ol", "player-strip"); this.strip.id = "playerSteps"; this.strip.setAttribute("aria-label", "Jump to a step");
      shell.append(head, stage, this.progress, controls, this.strip);
      overlay.append(shell);
      overlay.addEventListener("click", (event) => { if (event.target === overlay) this.close(); });
      document.body.append(overlay);
      this.overlay = overlay; this.ctx = this.canvas.getContext("2d");
      document.addEventListener("keydown", (event) => {
        if (this.overlay.hidden) return;
        if (event.key === "Escape") { event.stopImmediatePropagation(); this.close(); }
        else if (event.key === " " || event.key === "k") { event.preventDefault(); this.toggle(); }
        else if (event.key === "ArrowRight") this.jump(this.index + 1);
        else if (event.key === "ArrowLeft") this.jump(this.index - 1);
      }, true);
      this.video.addEventListener("ended", () => { this.videoEnded = true; });
    }

    fetchAudio(text) {
      const voice = this.options.voice?.() || "";
      const key = `${voice}\n${text}`;
      if (!this.audioCache.has(key)) {
        const request = this.options.speak(text, voice).then((blob) => URL.createObjectURL(blob));
        request.catch(() => this.audioCache.delete(key));
        this.audioCache.set(key, request);
      }
      return this.audioCache.get(key);
    }

    buildScenes(guide) {
      const scenes = [{ kind: "intro", text: (guide.intro || "").trim() || guide.title }];
      guide.steps.forEach((step, index) => scenes.push({ kind: step.kind === "video" ? "video" : "step", step, number: index + 1, text: (step.text || "").trim() }));
      scenes.push({ kind: "outro", text: (guide.outro || "").trim() || "That's it — you've completed every step." });
      return scenes;
    }

    async loadMedia(guide) {
      this.images = this.images || new Map();
      await Promise.all(guide.steps.map(async (step) => {
        if (step.kind === "video") return;
        const source = step.sourceImage || step.image; const cached = this.images.get(step.id);
        if (cached && cached.src === source) return;
        try { this.images.set(step.id, await loadImage(source)); } catch (_) {}
      }));
      if (guide.brand?.logo) this.logo = await loadImage(guide.brand.logo).catch(() => undefined); else this.logo = undefined;
    }

    imageFor(step) { return step ? this.images.get(step.id) : undefined; }

    async open(guide, startAt = 0) {
      this.guide = guide; this.theme = themeFor(guide.brand);
      this.scenes = this.buildScenes(guide);
      this.titleNode.textContent = guide.title;
      this.overlay.hidden = false; document.body.classList.add("player-open");
      this.overlay.style.setProperty("--player-accent", this.theme.accent); this.overlay.style.setProperty("--player-on-accent", this.theme.onAccent);
      this.images = new Map();
      await this.loadMedia(guide);
      this.renderProgress(); this.renderStrip();
      this.cursor = { x: 0.62, y: 0.86 };
      this.playing = false;
      // startAt is a scene index: 0 = intro, n = step n.
      this.enter(Math.max(0, Math.min(this.scenes.length - 1, Number(startAt) || 0)), false);
      this.lastFrame = performance.now();
      cancelAnimationFrame(this.raf); this.raf = requestAnimationFrame((now) => this.tick(now));
      this.play();
    }

    // The editor changed the guide (e.g. AI polished the wording) while the
    // player is open: pick up new text for this and upcoming scenes.
    async refresh(guide) {
      if (!this.scenes || this.overlay.hidden) return;
      await this.loadMedia(guide);
      const scenes = this.buildScenes(guide);
      this.guide = guide; this.theme = themeFor(guide.brand); this.titleNode.textContent = guide.title;
      const current = this.scene;
      if (scenes.length === this.scenes.length) {
        scenes.forEach((scene, index) => { if (index !== this.index) this.scenes[index] = scene; });
        // Keep the playing scene's timing; swap in the new wording if its voice has not started yet.
        const fresh = scenes[this.index];
        if (current && fresh.kind === current.kind) {
          current.step = fresh.step;
          if (!current.voice?.started && fresh.text !== current.text) { current.text = fresh.text; this.enter(this.index, this.playing); }
        }
      } else {
        this.scenes = scenes; this.index = Math.min(this.index, scenes.length - 1);
        this.renderProgress();
        this.enter(this.index, this.playing);
      }
      this.renderStrip();
      for (const next of this.scenes.slice(this.index + 1, this.index + 3)) if (next.text) this.fetchAudio(next.text).catch(() => {});
    }

    close() {
      this.pause(); this.voice?.stop(); this.voice = undefined; this.video.pause(); this.video.removeAttribute("src"); this.video.load();
      cancelAnimationFrame(this.raf); this.overlay.hidden = true; document.body.classList.remove("player-open");
      if ("speechSynthesis" in root) speechSynthesis.cancel();
      this.options.onClose?.(this.scene?.number ? this.scene.number - 1 : undefined);
    }

    renderProgress() {
      this.progress.replaceChildren();
      this.segments = this.scenes.map((scene, index) => {
        const segment = el("button", "player-segment"); segment.type = "button";
        segment.setAttribute("aria-label", scene.kind === "intro" ? "Intro" : scene.kind === "outro" ? "Outro" : `Step ${scene.number}`);
        segment.style.flexGrow = scene.kind === "intro" || scene.kind === "outro" ? "0.6" : "1";
        const fill = el("span", "player-fill"); segment.append(fill);
        segment.addEventListener("click", () => this.jump(index));
        this.progress.append(segment); return { segment, fill };
      });
    }

    async renderStrip() {
      this.strip.replaceChildren();
      this.stripItems = this.scenes.map((scene, index) => {
        const item = el("li"); const b = el("button", "player-chip"); b.type = "button";
        const label = scene.kind === "intro" ? "Intro" : scene.kind === "outro" ? "Outro" : `${scene.number}`;
        const thumb = el("span", "player-chip-thumb");
        if (scene.kind === "intro" || scene.kind === "outro") thumb.textContent = scene.kind === "intro" ? "▶" : "✓";
        b.append(thumb, el("span", "player-chip-label", label));
        b.setAttribute("aria-label", scene.kind === "step" || scene.kind === "video" ? `Jump to step ${scene.number}: ${scene.text}` : `Jump to ${label}`);
        b.addEventListener("click", () => this.jump(index));
        item.append(b); this.strip.append(item); return { item: b, thumb };
      });
      for (const [index, scene] of this.scenes.entries()) {
        if (scene.kind === "step" && this.imageFor(scene.step)) {
          const canvas = document.createElement("canvas"); canvas.width = 160; canvas.height = 90;
          const image = this.imageFor(scene.step);
          drawStep(canvas.getContext("2d"), image, scene.step, { width: 160, height: 90, unit: 0.3, number: scene.number, theme: this.theme, view: targetView(scene.step, image.naturalWidth, image.naturalHeight, 160, 90, 2.4) });
          this.stripItems[index].thumb.style.backgroundImage = `url(${canvas.toDataURL("image/jpeg", 0.7)})`;
        } else if (scene.kind === "video") this.stripItems[index].thumb.textContent = "🎬";
      }
    }

    enter(index, autoplay = true) {
      this.voice?.stop();
      this.video.pause();
      this.index = index; this.scene = this.scenes[index]; this.t = 0; this.voiceStartAt = undefined; this.voiceEndT = undefined; this.videoEnded = false;
      const scene = this.scene;
      const delay = scene.kind === "step" ? (targetOf(scene.step) ? 0.9 : 0.4) : 0.55;
      scene.voiceAt = delay;
      let voiceOptions = {};
      if (scene.kind === "step" || scene.kind === "video") {
        const step = scene.step;
        if (step.audioSource === "synthetic" && step.voiceover) voiceOptions = { src: step.voiceover };
        else if (step.audioSource === "recorded" && scene.kind === "step" && (step.recordedAudioId || this.guide.narrationAudioId) && step.captureEnd > step.captureStart) voiceOptions = { src: `/api/narration/${step.recordedAudioId || this.guide.narrationAudioId}`, start: step.captureStart, end: step.captureEnd };
      }
      const speakText = scene.kind === "video" && scene.step.recordedAudioId ? "" : scene.text;
      this.voice = makeVoice(speakText, (text) => this.fetchAudio(text), voiceOptions);
      scene.voice = this.voice;
      this.voiceLabel = "";
      this.voice.ready.then(() => { if (this.scene === scene) this.voiceLabel = this.voice.kind === "speech" ? "browser voice" : this.voice.kind === "audio" ? (voiceOptions.src ? "recorded voice" : (this.options.voice?.() || "local voice")) : ""; });
      // Warm up the next two scenes so narration starts without a gap.
      for (const next of this.scenes.slice(index + 1, index + 3)) if (next.text && !(next.kind === "video" && next.step.recordedAudioId)) this.fetchAudio(next.text).catch(() => {});
      if (scene.kind === "step") {
        const image = this.imageFor(scene.step);
        scene.view = image ? targetView(scene.step, image.naturalWidth, image.naturalHeight, 1280, 720) : FULL_VIEW;
        scene.from = { ...this.cursor };
      }
      this.video.hidden = scene.kind !== "video"; this.canvas.classList.toggle("is-hidden", scene.kind === "video");
      if (scene.kind === "video") {
        this.video.src = scene.step.videoUrl; this.video.currentTime = 0; this.video.muted = false;
        this.video.onerror = () => { this.videoEnded = true; };
        if (this.playing || autoplay) this.video.play().catch(() => {});
      } else { this.video.removeAttribute("src"); }
      this.segments.forEach(({ segment, fill }, position) => { segment.classList.toggle("current", position === index); fill.style.width = position < index ? "100%" : "0%"; });
      this.stripItems?.forEach(({ item }, position) => { item.setAttribute("aria-current", position === index ? "step" : "false"); if (position === index) item.scrollIntoView({ block: "nearest", inline: "center" }); });
      this.counter.textContent = scene.kind === "intro" ? "Intro" : scene.kind === "outro" ? "Outro" : `Step ${scene.number} of ${this.guide.steps.length}`;
      this.prevButton.disabled = index === 0; this.nextButton.disabled = index === this.scenes.length - 1;
      this.updateCaption();
      this.options.onScene?.(scene);
    }

    jump(index) {
      if (!this.scenes || index < 0 || index >= this.scenes.length) return;
      if (this.scene?.kind === "step") this.cursor = targetOf(this.scene.step)?.point || this.cursor;
      this.finished = false;
      this.enter(index);
      if (!this.playing) this.play();
    }

    play() {
      if (this.index >= this.scenes.length - 1 && this.finished) { this.finished = false; this.enter(0); }
      this.playing = true; this.playButton.textContent = "❚❚"; this.playButton.setAttribute("aria-label", "Pause"); this.bigPlay.hidden = true;
      if (this.voice?.started) this.voice.resume();
      if (this.scene?.kind === "video") this.video.play().catch(() => {});
      this.lastFrame = performance.now();
    }

    pause() {
      this.playing = false; this.playButton.textContent = "▶"; this.playButton.setAttribute("aria-label", "Play"); this.bigPlay.hidden = false;
      this.voice?.pause(); this.video.pause();
    }

    toggle() { if (this.playing) this.pause(); else this.play(); }

    updateCaption() {
      let text = this.scene?.kind === "intro" || this.scene?.kind === "outro" ? "" : this.scene?.text || "";
      // Don't show the same words twice: skip the caption when the step's
      // callout already shows the narration.
      const same = (a, b) => String(a || "").replace(/[\s.!?…]+$/g, "").replace(/\s+/g, " ").trim().toLowerCase() === String(b || "").replace(/[\s.!?…]+$/g, "").replace(/\s+/g, " ").trim().toLowerCase();
      if (text && this.scene?.kind === "step" && (this.scene.step.annotations || []).some((a) => a.type === "callout" && same(a.text, text))) text = "";
      this.caption.textContent = text;
      this.caption.hidden = !this.captionsOn || !text || (this.scene?.kind === "step" && this.t < this.scene.voiceAt - 0.2);
    }

    sceneLength() {
      const scene = this.scene; const voice = this.voice;
      const base = scene.kind === "intro" ? 3.4 : scene.kind === "outro" ? 3.6 : 3.2;
      const voiceEnd = this.voiceEndT !== undefined ? this.voiceEndT : (scene.voiceAt + (voice?.duration || 0));
      return Math.max(base, voiceEnd + 0.7);
    }

    tick(now) {
      this.raf = requestAnimationFrame((next) => this.tick(next));
      const dt = Math.min(0.1, (now - this.lastFrame) / 1000); this.lastFrame = now;
      const scene = this.scene; if (!scene) return;
      if (this.playing) {
        this.t += dt;
        if (this.voice && !this.voice.started && this.t >= scene.voiceAt) {
          // Hold the clock until the narration audio is ready, then start it.
          if (this.voice.isReady) this.voice.play();
          else this.t = scene.voiceAt;
        }
        // Watchdog: if the audio stalls, don't hold the guide hostage.
        if (this.voice?.started && !this.voice.ended && this.t > scene.voiceAt + this.voice.duration + 2.5) this.voice.forceEnd?.();
        if (this.voice?.ended && this.voiceEndT === undefined) this.voiceEndT = Math.max(this.t, scene.voiceAt);
        let length = this.sceneLength();
        const voiceDone = !this.voice || this.voice.ended;
        const videoDone = scene.kind !== "video" || this.videoEnded;
        if (scene.kind === "video" && !this.videoEnded && Number.isFinite(this.video.duration)) length = Math.max(length, this.video.duration);
        if (this.t >= length && voiceDone && videoDone) {
          if (this.index < this.scenes.length - 1) { this.jump(this.index + 1); return; }
          this.finished = true; this.pause(); this.t = length;
        }
      }
      this.draw();
      const speaking = Boolean(this.playing && this.voice?.started && !this.voice.ended && this.voice.kind !== "none");
      if (this.overlay.dataset.speaking !== String(speaking)) this.overlay.dataset.speaking = String(speaking);
      this.overlay.dataset.scene = String(this.index); this.overlay.dataset.voice = this.voice?.kind || "";
      this.voiceBadge.classList.toggle("is-speaking", speaking);
      const badgeText = speaking ? `Speaking${this.voiceLabel ? ` · ${this.voiceLabel}` : ""}` : this.voice && this.voice.started && !this.voice.ended && !this.playing ? "Narration paused" : this.voice && !this.voice.isReady && this.playing ? "Preparing narration…" : "";
      if (this.badgeTextNode.textContent !== badgeText) { this.badgeTextNode.textContent = badgeText; this.voiceBadge.hidden = !badgeText; }
      const length = this.sceneLength();
      const fraction = this.scene.kind === "video" && Number.isFinite(this.video.duration) && this.video.duration > 0 ? this.video.currentTime / this.video.duration : clamp(this.t / length, 0, 1);
      if (this.segments[this.index]) this.segments[this.index].fill.style.width = `${fraction * 100}%`;
      const total = this.scenes.length;
      this.timeNode.textContent = `${this.index + 1} / ${total}`;
      this.updateCaption();
    }

    draw() {
      const ctx = this.ctx, W = 1280, H = 720, scene = this.scene, t = this.t;
      if (scene.kind === "intro" || scene.kind === "outro") return this.drawCard(scene, t);
      if (scene.kind === "video") return;
      const image = this.imageFor(scene.step);
      if (!image) { ctx.fillStyle = this.theme.bgBottom; ctx.fillRect(0, 0, W, H); return; }
      const target = targetOf(scene.step);
      if (!target) {
        const k = ease(t / 6);
        drawStep(ctx, image, scene.step, { theme: this.theme, width: W, height: H, inset: 0.05, view: { x: 0.5, y: 0.5, scale: lerp(1, Math.max(1.04, scene.view.scale), k) }, spotlight: ease((t - 0.3) / 0.5), callout: ease((t - 0.5) / 0.4), click: ease((t - 0.5) / 0.4) });
        this.drawChrome(scene, t);
        return;
      }
      // 0–0.35s establishing shot, 0.35–1.25s zoom + cursor glide, click at 1.3s,
      // then spotlight and callout fade in while narration plays.
      const zoom = ease((t - 0.25) / 1.0);
      const view = { x: lerp(0.5, scene.view.x, zoom), y: lerp(0.5, scene.view.y, zoom), scale: lerp(1, scene.view.scale, zoom) };
      const glide = ease((t - 0.35) / 0.95);
      const cursor = { x: lerp(scene.from.x, target.point.x, glide), y: lerp(scene.from.y, target.point.y, glide), pressed: t > 1.3 && t < 1.48 };
      drawStep(ctx, image, scene.step, {
        width: W, height: H, view, inset: 0.05 * (1 - zoom),
        spotlight: ease((t - 1.3) / 0.45), click: ease((t - 1.35) / 0.3), callout: ease((t - 1.5) / 0.4),
        ripple: t > 1.3 ? (t - 1.3) / 0.65 : undefined, cursor, theme: this.theme,
      });
      this.drawChrome(scene, t);
    }

    // Accent bar, logo and the "Step N of M" pill, as in the exported MP4.
    drawChrome(scene, t) {
      const ctx = this.ctx, W = 1280, theme = this.theme;
      ctx.save();
      ctx.fillStyle = theme.accent; ctx.fillRect(0, 0, W, theme.bar);
      const label = `Step ${scene.number} of ${this.guide.steps.length}`;
      ctx.font = "600 22px 'Avenir Next', Avenir, Inter, system-ui, sans-serif";
      const pillW = 56 + ctx.measureText(label).width + 22, pillH = 52;
      const px = 28, py = 26;
      ctx.globalAlpha = ease(t / 0.4);
      ctx.beginPath(); roundRect(ctx, px, py, pillW, pillH, 26); ctx.fillStyle = "rgba(12,16,26,.8)"; ctx.fill();
      ctx.beginPath(); ctx.arc(px + 26, py + 26, 20, 0, Math.PI * 2); ctx.fillStyle = theme.accent; ctx.fill();
      ctx.fillStyle = theme.onAccent; ctx.font = "800 22px 'Avenir Next', Avenir, Inter, system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(String(scene.number), px + 26, py + 27);
      ctx.fillStyle = "#fff"; ctx.font = "600 22px 'Avenir Next', Avenir, Inter, system-ui, sans-serif"; ctx.textAlign = "left"; ctx.fillText(label, px + 56, py + 27);
      ctx.globalAlpha = 1;
      if (this.logo) this.drawLogo();
      ctx.restore();
    }

    drawLogo() {
      const ctx = this.ctx, h = 48, w = Math.min(220, this.logo.naturalWidth * h / this.logo.naturalHeight), x = 1280 - w - 28, y = 22;
      ctx.beginPath(); roundRect(ctx, x - 10, y - 8, w + 20, h + 16, 12); ctx.fillStyle = "rgba(255,255,255,.88)"; ctx.fill();
      ctx.drawImage(this.logo, x, y, w, h);
    }

    // Intro/outro cards laid out like guide_frames.py CardScene: centred
    // icon, accent label pill, headline, body on the brand gradient.
    drawCard(scene, t) {
      const ctx = this.ctx, W = 1280, H = 720, theme = this.theme;
      const gradient = ctx.createLinearGradient(0, 0, 0, H); gradient.addColorStop(0, theme.bgTop); gradient.addColorStop(1, theme.bgBottom);
      ctx.fillStyle = gradient; ctx.fillRect(0, 0, W, H);
      ctx.fillStyle = theme.accent; ctx.fillRect(0, 0, W, theme.bar);
      if (this.logo) this.drawLogo();
      const intro = scene.kind === "intro";
      const size = intro ? { standard: 60, compact: 46, display: 72 }[theme.titleStyle] : 52;
      const headText = intro ? (theme.titleStyle === "compact" ? this.guide.title.toUpperCase() : this.guide.title) : (scene.text || "That's it!");
      const bodyText = intro ? (this.guide.intro || "") : this.guide.title;
      ctx.font = `800 ${size}px 'Avenir Next', Avenir, Inter, system-ui, sans-serif`;
      const head = wrapText(ctx, headText, 1040).slice(0, 3); const headLine = intro ? Math.round(size * 1.18) : 64;
      ctx.font = "500 30px 'Avenir Next', Avenir, Inter, system-ui, sans-serif";
      const body = bodyText ? wrapText(ctx, bodyText, 940).slice(0, intro ? 3 : 2) : [];
      const label = intro ? `VIDEO GUIDE  ·  ${this.guide.steps.length} STEPS` : "ALL DONE";
      const blocks = [];
      if (!intro) blocks.push({ h: 96, draw: (y) => { ctx.beginPath(); ctx.arc(W / 2, y + 48, 48, 0, Math.PI * 2); ctx.fillStyle = theme.accent; ctx.fill(); ctx.strokeStyle = theme.onAccent; ctx.lineWidth = 9; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.beginPath(); ctx.moveTo(W / 2 - 20, y + 50); ctx.lineTo(W / 2 - 5, y + 64); ctx.lineTo(W / 2 + 22, y + 34); ctx.stroke(); } });
      blocks.push({ h: 40, draw: (y) => { ctx.font = "600 20px 'Avenir Next', Avenir, Inter, system-ui, sans-serif"; const w = ctx.measureText(label).width + 36; ctx.beginPath(); roundRect(ctx, W / 2 - w / 2, y, w, 40, 20); ctx.fillStyle = theme.accent; ctx.fill(); ctx.fillStyle = theme.onAccent; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(label, W / 2, y + 21); } });
      blocks.push({ h: head.length * headLine + 12, draw: (y) => { ctx.font = `800 ${size}px 'Avenir Next', Avenir, Inter, system-ui, sans-serif`; ctx.fillStyle = theme.ink; ctx.textAlign = "center"; ctx.textBaseline = "middle"; head.forEach((line, i) => ctx.fillText(line, W / 2, y + i * headLine + headLine / 2 + 4)); } });
      if (body.length) blocks.push({ h: body.length * 42 + 12, draw: (y) => { ctx.font = "500 30px 'Avenir Next', Avenir, Inter, system-ui, sans-serif"; ctx.fillStyle = theme.sub; ctx.textAlign = "center"; ctx.textBaseline = "middle"; body.forEach((line, i) => ctx.fillText(line, W / 2, y + i * 42 + 25)); } });
      const gap = 22; let y = (H - (blocks.reduce((sum, b) => sum + b.h, 0) + gap * (blocks.length - 1))) / 2;
      blocks.forEach((block, index) => {
        const start = 0.1 + 0.18 * index; const p = ease((t - start) / 0.6);
        if (p > 0) { ctx.save(); ctx.globalAlpha = p; block.draw(y + (1 - p) * 26); ctx.restore(); }
        y += block.h + gap;
      });
    }
  }

  let player;
  const api = {
    drawStep, themeFor, placeCallout, targetOf, targetView, viewTransform, loadImage, renderStepCanvas, makeVoice,
    open(guide, startAt, options) { if (!player) player = new GuidePlayer(options); else player.options = options; return player.open(guide, startAt); },
    close() { player?.close(); },
    refresh(guide) { return player?.refresh(guide); },
    get instance() { return player; },
  };
  root.StepfilmPlayer = api;
})(typeof self === "object" ? self : globalThis);
