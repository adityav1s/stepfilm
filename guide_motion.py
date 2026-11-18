"""Pure timing, camera and cursor math for Stepfilm's animated guide video.

Everything here is deterministic and free of Pillow/FFmpeg so it can be unit
tested. Coordinates of the screenshot are normalised (0..1); frame
coordinates are output pixels (1280x720 by default).
"""

import re
from math import ceil

FPS = 30
WIDTH, HEIGHT = 1280, 720

# Scene choreography (seconds from the start of a screenshot scene).
AUDIO_LEAD = 0.35        # voice starts while the camera begins to move
ZOOM_START, ZOOM_END = 0.0, 0.9
CURSOR_START, CURSOR_END = 0.1, 0.95
CLICK_AT = 1.0           # ripple begins; spotlight/ring/callout follow
RIPPLE_LENGTH = 0.65
SPOTLIGHT_FADE = (1.0, 1.3)
RING_FADE = (1.05, 1.3)
CALLOUT_FADE = (1.15, 1.5)
BADGE_FADE = (0.0, 0.35)
CROSSFADE = 0.3
TAIL = 0.7               # silence held after the voice ends
MIN_SCENE = 2.8
CARD_LEAD = 0.3           # intro/outro voice starts as the title lands
INSET = 0.9              # zoomed-out screenshot sits as a card inside the branded frame


def clamp(value, low=0.0, high=1.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = low
    return max(low, min(high, value))


def ease_in_out(t):
    """Cubic ease-in-out on 0..1."""
    t = clamp(t)
    return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def progress(t, start, end):
    """Linear 0..1 progress of time t through [start, end]."""
    if end <= start:
        return 1.0 if t >= end else 0.0
    return clamp((t - start) / (end - start))


def scene_duration(audio_duration, has_target=True):
    """Scene length follows the voiceover plus padding, never shorter than the choreography."""
    audio = max(0.0, float(audio_duration or 0))
    minimum = MIN_SCENE if has_target else 2.4
    return round(max(minimum, AUDIO_LEAD + audio + TAIL), 3)


def frame_count(duration, fps=FPS):
    return max(1, int(ceil(float(duration) * fps - 1e-6)))


def frame_times(duration, fps=FPS):
    return [index / fps for index in range(frame_count(duration, fps))]


def card_duration(audio_duration):
    return round(max(2.0, CARD_LEAD + max(0.0, float(audio_duration or 0)) + 0.4), 3)


# --------------------------------------------------------------------------
# Targets


def _rect(value):
    if not isinstance(value, dict):
        return None
    try:
        box = {key: float(value.get(key, 0)) for key in ("x", "y", "width", "height")}
    except (TypeError, ValueError):
        return None
    if box["width"] <= 0 or box["height"] <= 0:
        return None
    return {key: clamp(val) for key, val in box.items()}


def target_box(step):
    """The element the step is about: first highlight, else the recorded action rect."""
    for annotation in step.get("annotations") or []:
        if isinstance(annotation, dict) and annotation.get("type") == "highlight":
            box = _rect(annotation)
            if box:
                return box
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    if action.get("stale"):
        return None  # screenshot was taken after the page changed; the element is not in it
    return _rect(action.get("rect"))


def click_point(step):
    """Where the cursor clicks: action.point, else a click annotation centre, else the target centre."""
    action = step.get("action") if isinstance(step.get("action"), dict) else {}
    if action.get("stale"):
        action = {}
    point = action.get("point")
    if isinstance(point, dict) and "x" in point and "y" in point:
        return clamp(point.get("x")), clamp(point.get("y"))
    for annotation in step.get("annotations") or []:
        if isinstance(annotation, dict) and annotation.get("type") == "click":
            box = _rect(annotation)
            if box:
                return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    box = target_box(step)
    if box and action.get("type") not in {"navigate", "scene"}:
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    return None


def final_focus(step):
    """Final camera pose {x, y, scale}. Small targets get a zoom even when focus was left at 1x."""
    focus = step.get("focus") if isinstance(step.get("focus"), dict) else {}
    scale = clamp(focus.get("scale", 1), 1.0, 2.4)
    x, y = clamp(focus.get("x", 0.5)), clamp(focus.get("y", 0.5))
    box = target_box(step)
    if box and scale <= 1.01:
        size = max(box["width"], box["height"])
        scale = 1.6 if size < 0.12 else 1.3 if size < 0.3 else 1.12
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    return {"x": x, "y": y, "scale": scale}


# --------------------------------------------------------------------------
# Camera


def fit_scale(image_w, image_h, width=WIDTH, height=HEIGHT):
    return min(width / image_w, height / image_h)


def clamp_center(cx, cy, scale, image_w, image_h, width=WIDTH, height=HEIGHT):
    """Keep the view inside the image where the image is larger than the frame."""
    half_w, half_h = width / 2 / scale, height / 2 / scale
    cx = image_w / 2 if image_w * scale <= width else min(max(cx, half_w), image_w - half_w)
    cy = image_h / 2 if image_h * scale <= height else min(max(cy, half_h), image_h - half_h)
    return cx, cy


def camera_at(t, image_w, image_h, focus, animate=True, ken_burns=False, duration=None, width=WIDTH, height=HEIGHT):
    """Camera (cx, cy, s): image pixel at frame centre and image->frame pixel scale."""
    base = fit_scale(image_w, image_h, width, height)
    if ken_burns:
        span = max(0.1, float(duration or 4.0))
        p = ease_in_out(t / span)
        zoom = INSET + 0.07 * p
        tx, ty = focus.get("x", 0.5), focus.get("y", 0.5)
        cx = image_w * (0.5 + (tx - 0.5) * p)
        cy = image_h * (0.5 + (ty - 0.5) * p)
    else:
        p = ease_in_out(progress(t, ZOOM_START, ZOOM_END)) if animate else 1.0
        zoom = INSET + (focus["scale"] - INSET) * p
        cx = image_w * (0.5 + (focus["x"] - 0.5) * p)
        cy = image_h * (0.5 + (focus["y"] - 0.5) * p)
    scale = base * zoom
    cx, cy = clamp_center(cx, cy, scale, image_w, image_h, width, height)
    if not ken_burns and focus.get("nudge"):
        # Targets hugging the screenshot edge: let the camera overshoot a little
        # so the element clears the frame edge, badge and caption bands.
        nx, ny = focus["nudge"]
        cx, cy = cx + nx * p / scale, cy + ny * p / scale
    return cx, cy, scale


def safe_nudge(frame_box, width=WIDTH, height=HEIGHT, top=64, bottom=150, side=36):
    """Frame-pixel shift (dx, dy) of the camera centre that brings a target box inside the safe area."""
    x0, y0, x1, y1 = frame_box
    dx = dy = 0.0
    if x0 < side:
        dx = x0 - side
    elif x1 > width - side:
        dx = min(x1 - (width - side), x0 - side)
    if y0 < top:
        dy = y0 - top
    elif y1 > height - bottom:
        dy = min(y1 - (height - bottom), y0 - top)
    limit = 220
    return max(-limit, min(limit, dx)), max(-limit, min(limit, dy))


def to_frame(point, camera, image_w, image_h, width=WIDTH, height=HEIGHT):
    """Map a normalised image point into frame pixels for a camera."""
    cx, cy, scale = camera
    return (width / 2 + (point[0] * image_w - cx) * scale,
            height / 2 + (point[1] * image_h - cy) * scale)


def box_to_frame(box, camera, image_w, image_h, width=WIDTH, height=HEIGHT):
    x0, y0 = to_frame((box["x"], box["y"]), camera, image_w, image_h, width, height)
    x1, y1 = to_frame((box["x"] + box["width"], box["y"] + box["height"]), camera, image_w, image_h, width, height)
    return x0, y0, x1, y1


# --------------------------------------------------------------------------
# Cursor


def cursor_position(t, start, end):
    """Cursor glide from start to end (normalised image points) along a gentle arc."""
    p = ease_in_out(progress(t, CURSOR_START, CURSOR_END))
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    # Perpendicular bow, proportional to distance, peaking mid-path.
    bow = 0.12 * 4 * p * (1 - p)
    return sx + dx * p - dy * bow, sy + dy * p + dx * bow


def cursor_pressed(t):
    """Cursor scale: dips while the click happens."""
    if CLICK_AT <= t < CLICK_AT + 0.18:
        return 0.85
    return 1.0


def ripple(t):
    """(radius factor 0..1, alpha 0..1) of the click ripple, or None when inactive."""
    if t < CLICK_AT or t > CLICK_AT + RIPPLE_LENGTH:
        return None
    p = progress(t, CLICK_AT, CLICK_AT + RIPPLE_LENGTH)
    return ease_out(p), 1 - p


DEFAULT_CURSOR_START = (0.5, 0.82)


# --------------------------------------------------------------------------
# Captions


_URL = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,}|localhost|\d{1,3}(?:\.\d{1,3}){3})(?::\d+)?(?:/[^\s,;)]*)?", re.IGNORECASE)
_PLAIN_WORDS = re.compile(r"^[a-z]+\.[a-z]+$")


def site_name(host):
    host = host.lower()
    if host == "localhost" or re.fullmatch(r"[\d.]+", host):
        return "the site"
    labels = [label for label in host.split(".") if label != "www"]
    if len(labels) >= 2 and len(labels[-1]) == 2 and labels[-2] in {"co", "com", "org", "net", "gov", "ac"}:
        labels = labels[:-1]
    name = labels[-2] if len(labels) >= 2 else labels[0]
    return name[:1].upper() + name[1:]


def humanize_urls(text):
    """Never read URLs aloud: 'Go to https://en.wikipedia.org/wiki/Espresso' -> 'Go to Wikipedia'."""
    def replace(match):
        whole, host = match.group(0), match.group(1)
        if "://" not in whole and "/" not in whole and ":" not in whole and _PLAIN_WORDS.match(whole) and not re.search(r"\.(com|org|net|io|dev|app|edu|gov|co)$", whole, re.I):
            return whole  # ordinary "e.g"-like tokens or file names stay
        return site_name(host)
    return _URL.sub(replace, str(text or ""))


def caption_chunks(text, audio_duration, max_words=14):
    """Split narration into caption chunks timed proportionally across the voice."""
    words = humanize_urls(text).split()
    if not words:
        return []
    chunks, current = [], []
    for word in words:
        current.append(word)
        if len(current) >= max_words or (len(current) >= 6 and word[-1:] in ".!?;:"):
            chunks.append(current)
            current = []
    if current:
        if chunks and len(current) < 3:
            chunks[-1].extend(current)
        else:
            chunks.append(current)
    total = sum(len(chunk) for chunk in chunks)
    audio = max(1.0, float(audio_duration or 0))
    cues, cursor = [], AUDIO_LEAD
    for chunk in chunks:
        length = audio * len(chunk) / total
        cues.append({"start": round(cursor, 3), "end": round(cursor + length, 3), "text": " ".join(chunk)})
        cursor += length
    cues[-1]["end"] = round(cues[-1]["end"] + TAIL * 0.8, 3)
    return cues


def _caption_key(text):
    # Same as guide-player.js: drop trailing whitespace/.!?…, collapse spaces, trim, lowercase.
    return re.sub(r"\s+", " ", re.sub(r"[\s.!?…]+$", "", str(text or ""))).strip().lower()


def callout_repeats_narration(step):
    """True when a callout on the step already shows the narration sentence.

    Rule shared with the in-editor player (StepfilmPlayer): compare the
    callout text with the narration, ignoring case, repeated whitespace and
    trailing punctuation; equal means the caption is not burned in.
    """
    narration = _caption_key(step.get("text", ""))
    if not narration:
        return False
    for annotation in step.get("annotations") or []:
        if isinstance(annotation, dict) and annotation.get("type") == "callout" and _caption_key(annotation.get("text")) == narration:
            return True
    return False


def burn_captions(step, mode="auto"):
    """Whether to burn narration captions for a screenshot step. mode: auto | always | off."""
    if mode == "off":
        return False
    if mode == "always":
        return True
    return not callout_repeats_narration(step)
