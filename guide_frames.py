"""Pillow frame drawing for Stepfilm's Guidde-style video.

A screenshot scene starts on the whole screenshot (framed as a card on a
branded background), eases the camera toward the target, glides a cursor to
the click point, plays a click ripple, then fades in the spotlight, click
ring and callout. A step badge and burned-in captions sit on top. Redaction
(blur annotations) is baked into the screenshot pixels before anything else.
"""

import base64
import re
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import guide_motion as M

W, H = M.WIDTH, M.HEIGHT
DEFAULT_ACCENT = "#6d8eff"

_FONT_CACHE = {}
_FONT_FILES = {
    "bold": [("/System/Library/Fonts/Avenir Next.ttc", 0), ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0), ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0)],
    "demi": [("/System/Library/Fonts/Avenir Next.ttc", 2), ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 0), ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0)],
    "medium": [("/System/Library/Fonts/Avenir Next.ttc", 5), ("/System/Library/Fonts/Supplemental/Arial.ttf", 0), ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0)],
}


def font(weight, size):
    key = (weight, size)
    if key not in _FONT_CACHE:
        loaded = None
        for path, index in _FONT_FILES[weight]:
            try:
                loaded = ImageFont.truetype(path, size, index=index)
                break
            except OSError:
                continue
        if loaded is None:
            try:
                loaded = ImageFont.load_default(size=size)
            except TypeError:
                loaded = ImageFont.load_default()
        _FONT_CACHE[key] = loaded
    return _FONT_CACHE[key]


def hex_rgb(value, fallback=DEFAULT_ACCENT):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        value = fallback
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def luminance(rgb):
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


class Theme:
    def __init__(self, brand=None):
        brand = brand if isinstance(brand, dict) else {}
        self.accent = hex_rgb(brand.get("accent"))
        self.name = brand.get("theme") if brand.get("theme") in {"clean", "editorial", "bold"} else "clean"
        self.title_style = brand.get("titleStyle") if brand.get("titleStyle") in {"standard", "compact", "display"} else "standard"
        self.on_accent = (20, 24, 34) if luminance(self.accent) > 0.62 else (255, 255, 255)
        if self.name == "bold":
            self.bg_top, self.bg_bottom = mix(self.accent, (10, 12, 20), 0.25), mix(self.accent, (10, 12, 20), 0.6)
            self.card_ink, self.card_sub = (255, 255, 255), (230, 234, 245)
        elif self.name == "editorial":
            self.bg_top, self.bg_bottom = (246, 241, 231), mix((246, 241, 231), self.accent, 0.18)
            self.card_ink, self.card_sub = (28, 26, 22), (82, 76, 66)
        else:
            self.bg_top, self.bg_bottom = mix((244, 246, 251), self.accent, 0.08), mix((228, 233, 245), self.accent, 0.28)
            self.card_ink, self.card_sub = (16, 22, 38), (70, 80, 102)
        self.logo = decode_logo(brand.get("logo", ""))
        self._background = None

    def background(self):
        if self._background is None:
            top, bottom = self.bg_top, self.bg_bottom
            column = Image.new("RGB", (1, H))
            for y in range(H):
                column.putpixel((0, y), mix(top, bottom, y / (H - 1)))
            self._background = column.resize((W, H))
        return self._background


def decode_logo(data):
    if not isinstance(data, str) or not data:
        return None
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", data)
    if not match or len(data) > 500000:
        raise ValueError("The saved guide logo is invalid or too large. Remove it or upload a smaller PNG, JPEG, or WebP logo.")
    try:
        with Image.open(BytesIO(base64.b64decode(match.group(2), validate=True))) as source:
            logo = source.convert("RGBA")
            logo.thumbnail((180, 56), Image.Resampling.LANCZOS)
            return logo
    except (ValueError, OSError) as error:
        raise ValueError("The saved guide logo cannot be decoded. Remove it or upload a valid image.") from error


# --------------------------------------------------------------------------
# Redaction


def bake_redactions(image, annotations):
    """Blur + darken every blur annotation directly into the screenshot pixels.

    Runs before any camera move or overlay so redacted content can never
    appear in a frame, even zoomed in.
    """
    image = image.convert("RGB")
    width, height = image.size
    for annotation in annotations or []:
        if not isinstance(annotation, dict) or annotation.get("type") != "blur":
            continue
        x0 = int(M.clamp(annotation.get("x")) * width)
        y0 = int(M.clamp(annotation.get("y")) * height)
        x1 = int(round(M.clamp(M.clamp(annotation.get("x")) + M.clamp(annotation.get("width"))) * width))
        y1 = int(round(M.clamp(M.clamp(annotation.get("y")) + M.clamp(annotation.get("height"))) * height))
        if x1 - x0 < 1 or y1 - y0 < 1:
            continue
        region = image.crop((x0, y0, x1, y1))
        # Pixelate first (destroys detail irreversibly), then blur and darken.
        small = region.resize((max(1, (x1 - x0) // 16), max(1, (y1 - y0) // 16)), Image.Resampling.BILINEAR)
        region = small.resize(region.size, Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(12))
        region = Image.blend(region, Image.new("RGB", region.size, (16, 24, 39)), 0.55)
        image.paste(region, (x0, y0))
    return image


# --------------------------------------------------------------------------
# Small helpers


def wrap(text, face, max_width, max_lines):
    words = str(text or "").split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and face.getlength(candidate) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and face.getlength(last + "…") > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip(" .,") + "…"
    return lines


def with_alpha(layer, alpha):
    """Return the RGBA layer with its alpha multiplied by `alpha` (0..1)."""
    if alpha >= 0.999:
        return layer
    if alpha <= 0.001:
        return None
    faded = layer.copy()
    faded.putalpha(layer.getchannel("A").point(lambda value: int(value * alpha)))
    return faded


def paste(frame, layer, position=(0, 0), alpha=1.0):
    layer = with_alpha(layer, alpha)
    if layer is not None:
        frame.paste(layer, position, layer)


_CURSOR = {}


def cursor_sprite(scale=1.0):
    key = round(scale, 2)
    if key not in _CURSOR:
        ss = 4
        points = [(0, 0), (0, 23), (6, 17.5), (10.2, 27), (14, 25.4), (9.9, 16.2), (17.2, 16.2)]
        size = int(40 * ss)
        sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(sprite)
        k = 1.25 * scale * ss
        offset = 4 * ss
        shadow = [(offset + 2 * ss + x * k, offset + 3 * ss + y * k) for x, y in points]
        draw.polygon(shadow, fill=(0, 0, 0, 90))
        sprite = sprite.filter(ImageFilter.GaussianBlur(2 * ss))
        draw = ImageDraw.Draw(sprite)
        body = [(offset + x * k, offset + y * k) for x, y in points]
        draw.polygon(body, fill=(255, 255, 255, 255), outline=(12, 14, 20, 255), width=int(1.8 * ss))
        _CURSOR[key] = (sprite.resize((size // ss, size // ss), Image.Resampling.LANCZOS), 4)
    return _CURSOR[key]


def ring_sprite(radius, width, color, alpha, fill=None):
    ss = 3
    r = int(radius * ss)
    pad = int(width * ss) + 2
    size = 2 * (r + pad)
    sprite = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(sprite)
    box = (pad, pad, pad + 2 * r, pad + 2 * r)
    if fill:
        draw.ellipse(box, fill=fill)
    draw.ellipse(box, outline=(*color, int(255 * alpha)), width=max(1, int(width * ss)))
    sprite = sprite.resize((size // ss, size // ss), Image.Resampling.LANCZOS)
    return sprite, (r + pad) // ss


# --------------------------------------------------------------------------
# Shared chrome: badge, caption, brand bar


def badge_layer(theme, number, total):
    layer = Image.new("RGBA", (360, 80), (0, 0, 0, 0))
    ss = 2
    big = Image.new("RGBA", (360 * ss, 80 * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    label = f"Step {number} of {total}" if total else f"Step {number}"
    label_font = font("demi", 22 * ss)
    text_w = label_font.getlength(label)
    pill_w = int(56 * ss + text_w + 22 * ss)
    draw.rounded_rectangle((0, 0, pill_w, 52 * ss), radius=26 * ss, fill=(12, 16, 26, 205))
    draw.ellipse((6 * ss, 6 * ss, 46 * ss, 46 * ss), fill=(*theme.accent, 255))
    num_font = font("bold", 22 * ss)
    num = str(number)
    draw.text((26 * ss, 26 * ss), num, font=num_font, fill=(*theme.on_accent, 255), anchor="mm")
    draw.text((56 * ss, 26 * ss), label, font=label_font, fill=(255, 255, 255, 255), anchor="lm")
    layer = big.resize((360, 80), Image.Resampling.LANCZOS)
    return layer


def caption_layer(text):
    face = font("demi", 30)
    lines = wrap(text, face, 1040, 2)
    if not lines:
        return None, 0
    line_h, pad_x, pad_y = 42, 30, 16
    width = int(max(face.getlength(line) for line in lines)) + pad_x * 2
    height = len(lines) * line_h + pad_y * 2
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=16, fill=(10, 13, 22, 215))
    for index, line in enumerate(lines):
        draw.text((width / 2, pad_y + index * line_h + line_h / 2), line, font=face, fill=(255, 255, 255, 255), anchor="mm")
    return layer, height


def chrome_layer(theme):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    bar = {"clean": 5, "editorial": 7, "bold": 10}[theme.name]
    draw.rectangle((0, 0, W, bar - 1), fill=(*theme.accent, 255))
    if theme.logo is not None:
        logo = theme.logo
        x, y = W - logo.width - 28, 22
        draw.rounded_rectangle((x - 10, y - 8, x + logo.width + 10, y + logo.height + 8), radius=12, fill=(255, 255, 255, 225))
        layer.alpha_composite(logo, (x, y))
    return layer


# --------------------------------------------------------------------------
# Screenshot scene


CALLOUT_SAFE = (28, 92, W - 28, H - 150)   # clear of the step badge (top) and captions (bottom)
CALLOUT_FONT, CALLOUT_LINE, CALLOUT_PAD_X, CALLOUT_PAD_Y = 24, 32, 20, 14
CALLOUT_GAP = 24


def measure_callout(text, max_width):
    face = font("demi", CALLOUT_FONT)
    lines = wrap(text, face, max_width, 4)
    bw = int(max(face.getlength(line) for line in lines)) + CALLOUT_PAD_X * 2
    bh = len(lines) * CALLOUT_LINE + CALLOUT_PAD_Y * 2
    return lines, bw, bh


def place_callout(target, text, safe=CALLOUT_SAFE, force=False):
    """Place a callout bubble beside a target box (frame pixels).

    Tries right/left/below/above at a few wrap widths and returns the
    placement with the most spare room that stays inside `safe` and does not
    overlap the (padded) target: (side, x, y, lines, width, height). With
    force=True it always returns the least-bad option, clamped inside safe.
    """
    tx0, ty0, tx1, ty1 = target
    tcx, tcy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    guard = (tx0 - 12, ty0 - 12, tx1 + 12, ty1 + 12)
    best, least_bad = None, None
    order = {"below": 0, "right": 1, "above": 2, "left": 3}
    for max_width in (400, 320, 250):
        lines, bw, bh = measure_callout(text, max_width)
        for side in order:
            if side == "right":
                x, y, room = tx1 + CALLOUT_GAP, tcy - bh / 2, safe[2] - tx1 - CALLOUT_GAP - bw
            elif side == "left":
                x, y, room = tx0 - CALLOUT_GAP - bw, tcy - bh / 2, tx0 - CALLOUT_GAP - bw - safe[0]
            elif side == "below":
                x, y, room = tcx - bw / 2, ty1 + CALLOUT_GAP, safe[3] - ty1 - CALLOUT_GAP - bh
            else:
                x, y, room = tcx - bw / 2, ty0 - CALLOUT_GAP - bh, ty0 - CALLOUT_GAP - bh - safe[1]
            x = min(max(x, safe[0]), safe[2] - bw)
            y = min(max(y, safe[1]), safe[3] - bh)
            overlaps = not (x + bw <= guard[0] or x >= guard[2] or y + bh <= guard[1] or y >= guard[3])
            candidate = (side, int(x), int(y), lines, bw, bh)
            if room >= 0 and not overlaps:
                score = (min(room, 120), -order[side])
                if best is None or score > best[0]:
                    best = (score, candidate)
            elif force:
                badness = (overlaps, -room)
                if least_bad is None or badness < least_bad[0]:
                    least_bad = (badness, candidate)
        if best is not None:
            return best[1]
    return least_bad[1] if force and least_bad else None


class ScreenshotScene:
    def __init__(self, image, step, number, total, theme, duration, cues, prev_point=None, ken_burns=None):
        self.step = step
        self.theme = theme
        self.duration = float(duration)
        annotations = [a for a in (step.get("annotations") or []) if isinstance(a, dict)]
        # 1. Redaction first, on the full-resolution screenshot.
        source = bake_redactions(image, annotations)
        self.annotations = [a for a in annotations if a.get("type") != "blur"]
        self.focus = M.final_focus(step)
        self.target = M.target_box(step)
        self.point = M.click_point(step)
        has_overlays = bool(self.annotations)
        self.ken_burns = (not self.target and not has_overlays) if ken_burns is None else ken_burns
        if self.ken_burns and not (step.get("focus") or {}).get("scale", 1) > 1:
            self.focus = {"x": 0.5, "y": 0.42, "scale": 1}
        # 2. Pre-scale so the max zoom is ~1:1 with output pixels.
        fit = M.fit_scale(*source.size)
        max_scale = fit * max(1.0, self.focus["scale"])
        if max_scale < 1:
            target = (max(1, round(source.width * max_scale)), max(1, round(source.height * max_scale)))
            source = source.resize(target, Image.Resampling.LANCZOS)
        self.source = source
        self.iw, self.ih = source.size
        self.prev_point = prev_point or M.DEFAULT_CURSOR_START
        self.action_callout = next((a for a in self.annotations if a.get("type") == "callout" and a.get("origin") == "action"), None)
        self.callout_place = None
        self._layout()
        self.chrome = chrome_layer(theme)
        self.badge = badge_layer(theme, number, total)
        self.cues = cues or []
        self._captions = {}
        self.prev_frame = None
        self._build_overlays()
        self.badge_pos = (28, 26)
        if self.target and not self.ken_burns:
            x0, y0, x1, y1 = M.box_to_frame(self.target, self.final_camera, self.iw, self.ih)
            if x0 < 28 + self.badge.width and y0 < 26 + 60:
                self.badge_pos = (W - 28 - 230, 26) if theme.logo is None else (28, H - 26 - 60)
        self.static_from = max(M.CALLOUT_FADE[1], M.CLICK_AT + M.RIPPLE_LENGTH, M.CURSOR_END) + 0.01
        self._static_cache = {}

    # -- layout -----------------------------------------------------------
    def _layout(self):
        """Pick the deepest zoom that keeps the whole target AND its callout inside the
        frame (clear of badge and caption bands), with the callout beside, never over, the target."""
        if self.ken_burns or not self.target:
            self.final_camera = M.camera_at(self.duration, self.iw, self.ih, self.focus, ken_burns=self.ken_burns, duration=self.duration)
            return
        base = dict(self.focus)
        scales, scale = [], base["scale"]
        while scale > M.INSET + 0.02:
            scales.append(scale)
            scale *= 0.86
        scales.append(M.INSET)
        fallback = None
        for scale in scales:
            focus = {**base, "scale": scale}
            focus.pop("nudge", None)
            camera = M.camera_at(self.duration, self.iw, self.ih, focus)
            nudge = M.safe_nudge(M.box_to_frame(self.target, camera, self.iw, self.ih))
            if nudge != (0.0, 0.0):
                focus["nudge"] = nudge
                camera = M.camera_at(self.duration, self.iw, self.ih, focus)
            frame_box = M.box_to_frame(self.target, camera, self.iw, self.ih)
            inside = (frame_box[0] >= CALLOUT_SAFE[0] - 2 and frame_box[1] >= CALLOUT_SAFE[1] - 2 and
                      frame_box[2] <= CALLOUT_SAFE[2] + 2 and frame_box[3] <= CALLOUT_SAFE[3] + 2)
            place = place_callout(frame_box, self._callout_text()) if self.action_callout else None
            if fallback is None:
                fallback = (focus, camera, place)
            if inside and (place is not None or not self.action_callout):
                self.focus, self.final_camera, self.callout_place = focus, camera, place
                return
        self.focus, self.final_camera, self.callout_place = fallback
        if self.action_callout and self.callout_place is None:
            frame_box = M.box_to_frame(self.target, self.final_camera, self.iw, self.ih)
            self.callout_place = place_callout(frame_box, self._callout_text(), force=True)

    def _callout_text(self):
        annotation = self.action_callout or {}
        return (annotation.get("text") or self.step.get("text") or "").strip() or "Click here"

    # -- camera -----------------------------------------------------------
    def camera(self, t):
        return M.camera_at(t, self.iw, self.ih, self.focus, ken_burns=self.ken_burns, duration=self.duration)

    def base(self, camera):
        cx, cy, s = camera
        left, top = cx - W / 2 / s, cy - H / 2 / s
        right, bottom = cx + W / 2 / s, cy + H / 2 / s
        if left >= -0.01 and top >= -0.01 and right <= self.iw + 0.01 and bottom <= self.ih + 0.01:
            return self.source.resize((W, H), Image.Resampling.BILINEAR,
                                      box=(max(0, left), max(0, top), min(self.iw, right), min(self.ih, bottom)))
        frame = self.theme.background().copy()
        ix0, iy0, ix1, iy1 = max(0, left), max(0, top), min(self.iw, right), min(self.ih, bottom)
        dx0, dy0 = (ix0 - left) * s, (iy0 - top) * s
        dw, dh = max(1, round((ix1 - ix0) * s)), max(1, round((iy1 - iy0) * s))
        region = self.source.resize((dw, dh), Image.Resampling.BILINEAR, box=(ix0, iy0, ix1, iy1))
        x, y = round(dx0), round(dy0)
        # Soft card shadow and rounded corners while the screenshot is inset.
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((x - 2, y + 4, x + dw + 2, y + dh + 10), radius=18, fill=(10, 14, 24, 60))
        frame.paste(shadow, (0, 0), shadow)
        mask = Image.new("L", (dw, dh), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, dw - 1, dh - 1), radius=14, fill=255)
        frame.paste(region, (x, y), mask)
        return frame

    # -- overlays at the final camera -------------------------------------
    def _build_overlays(self):
        camera = self.final_camera
        accent = self.theme.accent
        to_frame = lambda box: M.box_to_frame(box, camera, self.iw, self.ih)
        highlights = [M._rect(a) for a in self.annotations if a.get("type") == "highlight"]
        highlights = [box for box in highlights if box]
        self.spot = None
        if highlights:
            ss = 2
            dim = Image.new("RGBA", (W * ss, H * ss), (8, 11, 20, 105))
            draw = ImageDraw.Draw(dim)
            strokes = []
            for box in highlights:
                x0, y0, x1, y1 = to_frame(box)
                pad = 8
                rect = (int((x0 - pad) * ss), int((y0 - pad) * ss), int((x1 + pad) * ss), int((y1 + pad) * ss))
                draw.rounded_rectangle(rect, radius=10 * ss, fill=(0, 0, 0, 0))
                strokes.append(rect)
            for rect in strokes:
                draw.rounded_rectangle((rect[0] - 3 * ss, rect[1] - 3 * ss, rect[2] + 3 * ss, rect[3] + 3 * ss), radius=13 * ss, outline=(255, 255, 255, 230), width=2 * ss)
                draw.rounded_rectangle(rect, radius=10 * ss, outline=(*accent, 255), width=4 * ss)
            self.spot = dim.resize((W, H), Image.Resampling.LANCZOS)
        rings = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        has_ring = False
        for annotation in self.annotations:
            if annotation.get("type") != "click":
                continue
            box = M._rect(annotation)
            if not box:
                continue
            cx, cy = M.to_frame((box["x"] + box["width"] / 2, box["y"] + box["height"] / 2), camera, self.iw, self.ih)
            radius = max(14, 0.018 * self.iw * camera[2])
            halo, hr = ring_sprite(radius * 1.55, 0, accent, 0, fill=(*accent, 70))
            rings.paste(halo, (int(cx - hr), int(cy - hr)), halo)
            outer, orr = ring_sprite(radius, 5, (255, 255, 255), 1)
            rings.alpha_composite(outer, (int(cx - orr), int(cy - orr)))
            inner, ir = ring_sprite(radius, 3, accent, 1)
            rings.alpha_composite(inner, (int(cx - ir), int(cy - ir)))
            dot, dr = ring_sprite(radius * 0.3, 0, accent, 0, fill=(*accent, 255))
            rings.alpha_composite(dot, (int(cx - dr), int(cy - dr)))
            has_ring = True
        self.rings = rings if has_ring else None
        callouts = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        has_callout = False
        target_frame = to_frame(self.target) if self.target else None
        occupied = []
        for annotation in self.annotations:
            kind = annotation.get("type")
            if kind == "callout":
                self._draw_callout(callouts, annotation, camera, target_frame, occupied)
                has_callout = True
            elif kind == "marker":
                box = M._rect(annotation)
                if box:
                    cx, cy = M.to_frame((box["x"] + box["width"] / 2, box["y"] + box["height"] / 2), camera, self.iw, self.ih)
                    dot, r = ring_sprite(22, 3, (255, 255, 255), 1, fill=(*accent, 255))
                    callouts.alpha_composite(dot, (int(cx - r), int(cy - r)))
                    ImageDraw.Draw(callouts).text((cx, cy), (annotation.get("text") or "1")[:3], font=font("bold", 22), fill=(*self.theme.on_accent, 255), anchor="mm")
                    has_callout = True
            elif kind == "arrow":
                box = M._rect(annotation) or {"x": M.clamp(annotation.get("x")), "y": M.clamp(annotation.get("y")), "width": 0, "height": 0}
                x0, y0 = M.to_frame((box["x"], box["y"]), camera, self.iw, self.ih)
                x1, y1 = M.to_frame((box["x"] + box["width"], box["y"] + box["height"]), camera, self.iw, self.ih)
                self._draw_arrow(callouts, (x0, y0), (x1, y1))
                has_callout = True
        self.callouts = callouts if has_callout else None

    def _draw_arrow(self, layer, start, end):
        import math
        ss = 2
        big = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        (x0, y0), (x1, y1) = start, end
        angle = math.atan2(y1 - y0, x1 - x0)
        head = 26
        bx, by = x1 - head * 0.8 * math.cos(angle), y1 - head * 0.8 * math.sin(angle)
        for color, width in (((255, 255, 255, 255), 11), ((*self.theme.accent, 255), 7)):
            draw.line([(x0 * ss, y0 * ss), (bx * ss, by * ss)], fill=color, width=width * ss)
        tri = [(x1, y1), (x1 - head * math.cos(angle - 0.45), y1 - head * math.sin(angle - 0.45)), (x1 - head * math.cos(angle + 0.45), y1 - head * math.sin(angle + 0.45))]
        draw.polygon([(x * ss, y * ss) for x, y in tri], fill=(*self.theme.accent, 255), outline=(255, 255, 255, 255), width=2 * ss)
        layer.alpha_composite(big.resize((W, H), Image.Resampling.LANCZOS))

    def _draw_callout(self, layer, annotation, camera, target_frame, occupied):
        text = (annotation.get("text") or self.step.get("text") or "").strip() or "Click here"
        pointer = None
        place = self.callout_place if annotation is self.action_callout else None
        if place is None and annotation.get("origin") == "action" and target_frame:
            place = place_callout(target_frame, text, force=True)
        if place is not None:
            side, x, y, lines, bw, bh = place
            tx0, ty0, tx1, ty1 = target_frame
            pointer = (side, (tx0 + tx1) / 2, (ty0 + ty1) / 2)
        else:
            lines, bw, bh = measure_callout(text, 400)
            safe = CALLOUT_SAFE
            x0, y0, x1, _ = M.box_to_frame(M._rect(annotation) or {"x": 0.05, "y": 0.1, "width": 0.3, "height": 0.1}, camera, self.iw, self.ih)
            x, y = min(max(x0, safe[0]), safe[2] - bw), min(max(y0, safe[1]), safe[3] - bh)
        line_h, pad_x, pad_y = CALLOUT_LINE, CALLOUT_PAD_X, CALLOUT_PAD_Y
        x, y = int(x), int(y)
        ss = 2
        pad = 24
        big = Image.new("RGBA", ((bw + pad * 2) * ss, (bh + pad * 2) * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        draw.rounded_rectangle(((pad + 2) * ss, (pad + 5) * ss, (pad + bw + 2) * ss, (pad + bh + 5) * ss), radius=14 * ss, fill=(0, 0, 0, 70))
        big = big.filter(ImageFilter.GaussianBlur(6 * ss))
        draw = ImageDraw.Draw(big)
        accent = self.theme.accent
        draw.rounded_rectangle((pad * ss, pad * ss, (pad + bw) * ss, (pad + bh) * ss), radius=14 * ss, fill=(*accent, 255))
        if pointer:
            side, tcx, tcy = pointer
            size = 12
            if side == "below":
                px = min(max(tcx - x, 22), bw - 22)
                tri = [(px - size, 0), (px + size, 0), (px, -size)]
            elif side == "above":
                px = min(max(tcx - x, 22), bw - 22)
                tri = [(px - size, bh), (px + size, bh), (px, bh + size)]
            elif side == "right":
                py = min(max(tcy - y, 18), bh - 18)
                tri = [(0, py - size), (0, py + size), (-size, py)]
            else:
                py = min(max(tcy - y, 18), bh - 18)
                tri = [(bw, py - size), (bw, py + size), (bw + size, py)]
            draw.polygon([((pad + px_) * ss, (pad + py_) * ss) for px_, py_ in tri], fill=(*accent, 255))
        big_face = font("demi", 24 * ss)
        for index, line in enumerate(lines):
            draw.text(((pad + pad_x) * ss, (pad + pad_y + index * line_h + line_h / 2) * ss), line, font=big_face, fill=(*self.theme.on_accent, 255), anchor="lm")
        sprite = big.resize((bw + pad * 2, bh + pad * 2), Image.Resampling.LANCZOS)
        layer.alpha_composite(sprite, (max(0, x - pad), max(0, y - pad)), (max(0, pad - x), max(0, pad - y)))
        occupied.append((x, y, x + bw, y + bh))

    # -- captions ---------------------------------------------------------
    def cue_index(self, t):
        for index, cue in enumerate(self.cues):
            if cue["start"] <= t < cue["end"]:
                return index
        return -1

    def caption(self, index):
        if index not in self._captions:
            self._captions[index] = caption_layer(self.cues[index]["text"]) if index >= 0 else (None, 0)
        return self._captions[index]

    # -- frame ------------------------------------------------------------
    def frame(self, t):
        cue = self.cue_index(t)
        static = not self.ken_burns and t >= self.static_from and t >= M.CROSSFADE
        if static and cue in self._static_cache:
            return self._static_cache[cue]
        camera = self.camera(t)
        frame = self.base(camera)
        if not self.ken_burns:
            if self.spot is not None:
                paste(frame, self.spot, alpha=M.ease_out(M.progress(t, *M.SPOTLIGHT_FADE)))
            if self.rings is not None:
                paste(frame, self.rings, alpha=M.progress(t, *M.RING_FADE))
            if self.callouts is not None:
                p = M.ease_out(M.progress(t, *M.CALLOUT_FADE))
                if p > 0:
                    paste(frame, self.callouts, (0, int(round((1 - p) * 14))), alpha=p)
            if self.point is not None:
                self._draw_cursor(frame, t, camera)
        paste(frame, self.chrome)
        paste(frame, self.badge, self.badge_pos, alpha=M.progress(t, *M.BADGE_FADE))
        if cue >= 0:
            layer, height = self.caption(cue)
            if layer is not None:
                paste(frame, layer, ((W - layer.width) // 2, H - 28 - height))
        if self.prev_frame is not None and t < M.CROSSFADE:
            frame = Image.blend(self.prev_frame, frame, M.ease_in_out(t / M.CROSSFADE))
        if static:
            self._static_cache[cue] = frame
        return frame

    def _draw_cursor(self, frame, t, camera):
        nx, ny = M.cursor_position(t, self.prev_point, self.point)
        fx, fy = M.to_frame((nx, ny), camera, self.iw, self.ih)
        state = M.ripple(t)
        if state is not None:
            grow, alpha = state
            for factor, weight in ((1.0, 1.0), (0.6, 0.7)):
                radius = 10 + 46 * grow * factor
                sprite, r = ring_sprite(radius, 4, self.theme.accent, alpha * weight, fill=(*self.theme.accent, int(60 * alpha * weight)))
                frame.paste(sprite, (int(fx - r), int(fy - r)), sprite)
        sprite, offset = cursor_sprite(M.cursor_pressed(t))
        frame.paste(sprite, (int(fx - offset), int(fy - offset)), sprite)

    def last_frame(self):
        return self.frame(max(0.0, M.frame_times(self.duration)[-1]))


# --------------------------------------------------------------------------
# Intro / outro cards


class CardScene:
    def __init__(self, kind, title, text, theme, duration, backdrop=None, total_steps=0):
        self.kind = kind
        self.theme = theme
        self.duration = float(duration)
        self.prev_frame = None
        background = theme.background().copy()
        if backdrop is not None:
            cover = backdrop.convert("RGB")
            scale = max(W / cover.width, H / cover.height)
            cover = cover.resize((max(W, round(cover.width * scale)), max(H, round(cover.height * scale))), Image.Resampling.BILINEAR)
            left, top = (cover.width - W) // 2, (cover.height - H) // 2
            cover = cover.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(22))
            background = Image.blend(cover, background, 0.82)
        self.background = background
        paste(self.background, chrome_layer(theme))
        ink, sub = theme.card_ink, theme.card_sub
        # Headline + body blocks are pre-rendered and animated in.
        if kind == "intro":
            size = {"standard": 60, "compact": 46, "display": 72}[theme.title_style]
            head_text = title.upper() if theme.title_style == "compact" else title
            head_face = font("bold", size)
            head_lines = wrap(head_text, head_face, 1040, 3)
            body_lines = wrap(text, font("medium", 30), 940, 3)
            label = "VIDEO GUIDE" + (f"  ·  {total_steps} STEPS" if total_steps else "")
        else:
            head_face = font("bold", 52)
            head_lines = wrap(text or "That's it!", head_face, 1040, 3)
            body_lines = wrap(title, font("medium", 30), 940, 2)
            label = "ALL DONE"
        self.label = self._label(label)
        self.head = self._block(head_lines, head_face, int(size * 1.18) if kind == "intro" else 64, ink)
        self.body = self._block(body_lines, font("medium", 30), 42, sub) if body_lines else None
        self.icon = self._check_icon() if kind == "outro" else None

    def _label(self, text):
        face = font("demi", 20)
        width = int(face.getlength(text)) + 36
        layer = Image.new("RGBA", (width, 40), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        draw.rounded_rectangle((0, 0, width - 1, 39), radius=20, fill=(*self.theme.accent, 255))
        draw.text((width / 2, 20), text, font=face, fill=(*self.theme.on_accent, 255), anchor="mm")
        return layer

    def _block(self, lines, face, line_h, color):
        if not lines:
            return None
        width = int(max(face.getlength(line) for line in lines)) + 8
        layer = Image.new("RGBA", (width, line_h * len(lines) + 12), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        for index, line in enumerate(lines):
            draw.text((width / 2, index * line_h + line_h / 2 + 4), line, font=face, fill=(*color, 255), anchor="mm")
        return layer

    def _check_icon(self):
        ss = 3
        size = 96
        big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(big)
        draw.ellipse((0, 0, size * ss - 1, size * ss - 1), fill=(*self.theme.accent, 255))
        draw.line([(28 * ss, 50 * ss), (43 * ss, 64 * ss), (70 * ss, 34 * ss)], fill=(*self.theme.on_accent, 255), width=9 * ss, joint="curve")
        return big.resize((size, size), Image.Resampling.LANCZOS)

    def frame(self, t):
        frame = self.background.copy()
        blocks = [block for block in (self.icon, self.label, self.head, self.body) if block is not None]
        gaps = 22
        total_h = sum(block.height for block in blocks) + gaps * (len(blocks) - 1)
        y = (H - total_h) // 2
        for index, block in enumerate(blocks):
            start = 0.1 + 0.18 * index
            p = M.ease_out(M.progress(t, start, start + 0.6))
            if p > 0:
                paste(frame, block, ((W - block.width) // 2, y + int(round((1 - p) * 26))), alpha=p)
            y += block.height + gaps
        if self.prev_frame is not None and t < M.CROSSFADE:
            frame = Image.blend(self.prev_frame, frame, M.ease_in_out(t / M.CROSSFADE))
        return frame

    def last_frame(self):
        return self.frame(max(0.0, M.frame_times(self.duration)[-1]))
