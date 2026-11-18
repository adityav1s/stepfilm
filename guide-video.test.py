"""Guidde-style video renderer: timing, camera, cursor path, redaction, voices, full render."""
import base64
import json
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageStat

import guide_motion as M
import guide_frames as F
from render import Renderer, best_voice, parse_say_voices, speech_text

# ---- frame timing ---------------------------------------------------------
assert M.frame_count(1.0) == 30 and M.frame_count(1.01) == 31
times = M.frame_times(2.0)
assert len(times) == 60 and times[0] == 0 and abs(times[1] - 1 / 30) < 1e-9
short, long = M.scene_duration(0.8), M.scene_duration(6.0)
assert short == M.MIN_SCENE, short  # choreography never cut short
assert abs(long - (M.AUDIO_LEAD + 6.0 + M.TAIL)) < 1e-6, long  # scene follows the voiceover
assert M.CLICK_AT < M.SPOTLIGHT_FADE[1] <= M.CALLOUT_FADE[1] < M.MIN_SCENE
assert M.card_duration(3.0) > 3.0
cues = M.caption_chunks("Open the billing page. Then choose the plan you want and confirm the change with the blue button at the bottom.", 6.0)
assert len(cues) >= 2 and cues[0]["start"] == M.AUDIO_LEAD
assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(cues, cues[1:]))
assert abs(cues[-1]["end"] - (M.AUDIO_LEAD + 6.0 + M.TAIL * 0.8)) < 0.01
assert M.caption_chunks("", 2) == []

# ---- easing / camera ------------------------------------------------------
assert M.ease_in_out(0) == 0 and M.ease_in_out(1) == 1 and abs(M.ease_in_out(0.5) - 0.5) < 1e-9
focus = {"x": 0.2, "y": 0.25, "scale": 1.6}
start = M.camera_at(0, 1600, 1000, focus)
end = M.camera_at(5, 1600, 1000, focus)
assert abs(start[2] - M.fit_scale(1600, 1000) * M.INSET) < 1e-9, start  # starts on the whole screenshot
assert abs(end[2] - M.fit_scale(1600, 1000) * 1.6) < 1e-9, end
half_w = M.WIDTH / 2 / end[2]
assert abs(end[0] - half_w) < 1e-6, "camera is clamped inside the screenshot"
scales = [M.camera_at(t, 1600, 1000, focus)[2] for t in M.frame_times(2)]
assert all(b >= a - 1e-9 for a, b in zip(scales, scales[1:])), "zoom eases monotonically"
step = {"action": {"type": "click", "rect": {"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.03}, "point": {"x": 0.12, "y": 0.21}}}
assert M.click_point(step) == (0.12, 0.21)
assert M.final_focus(step)["scale"] == 1.6, "small targets get a zoom even when focus was left at 1x"
assert M.click_point({"annotations": [{"type": "click", "x": 0.5, "y": 0.5, "width": 0.1, "height": 0.1}]}) == (0.55, 0.55)
assert M.click_point({"action": {"type": "navigate"}}) is None

# ---- cursor path ----------------------------------------------------------
a, b = (0.1, 0.8), (0.7, 0.2)
assert M.cursor_position(0, a, b) == a
assert all(abs(u - v) < 1e-9 for u, v in zip(M.cursor_position(M.CURSOR_END + 0.5, a, b), b))
path = [M.cursor_position(t, a, b) for t in M.frame_times(2)]
xs = [p[0] for p in path]
assert all(y >= x - 1e-9 for x, y in zip(xs, xs[1:])), "cursor glides without back-tracking"
mid = M.cursor_position((M.CURSOR_START + M.CURSOR_END) / 2, a, b)
straight = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
assert 0.01 < ((mid[0] - straight[0]) ** 2 + (mid[1] - straight[1]) ** 2) ** 0.5 < 0.2, "path bows gently"
assert M.ripple(M.CLICK_AT - 0.01) is None and M.ripple(M.CLICK_AT + 0.1) is not None
assert M.ripple(M.CLICK_AT + M.RIPPLE_LENGTH + 0.01) is None

# ---- redaction is baked before anything else ------------------------------
image = Image.new("RGB", (1600, 1000), (255, 255, 255))
draw = ImageDraw.Draw(image)
for x in range(400, 800, 8):  # high-contrast "secret" stripes
    draw.rectangle((x, 300, x + 3, 500), fill=(0, 0, 0))
secret_box = (400, 300, 800, 500)
before = ImageStat.Stat(image.crop(secret_box).convert("L")).stddev[0]
blur = {"type": "blur", "x": 0.25, "y": 0.3, "width": 0.25, "height": 0.2}
baked = F.bake_redactions(image, [blur])
after = ImageStat.Stat(baked.crop(secret_box).convert("L")).stddev[0]
assert before > 80 and after < 8, (before, after)
redact_step = {"text": "Open the account", "focus": {"x": 0.37, "y": 0.4, "scale": 2.2},
               "annotations": [blur, {"type": "highlight", "x": 0.3, "y": 0.35, "width": 0.1, "height": 0.08, "origin": "action"},
                               {"type": "callout", "x": 0.5, "y": 0.5, "width": 0.2, "height": 0.08, "text": "Open the account", "origin": "action"}],
               "action": {"type": "click", "point": {"x": 0.35, "y": 0.39}}}
scene = F.ScreenshotScene(image, redact_step, 1, 1, F.Theme({}), 4.0, [], None)
assert all(a.get("type") != "blur" for a in scene.annotations), "blur is consumed by the bake, never drawn as an overlay"
for t in (0.0, 0.6, 1.0, 2.5):
    frame = scene.frame(t)
    camera = scene.camera(t)
    x0, y0, x1, y1 = M.box_to_frame({"x": 0.26, "y": 0.31, "width": 0.23, "height": 0.18}, camera, scene.iw, scene.ih)
    box = (max(0, int(x0)), max(0, int(y0)), min(M.WIDTH, int(x1)), min(M.HEIGHT, int(y1)))
    if box[2] - box[0] > 20 and box[3] - box[1] > 20:
        region = frame.crop(box).convert("L")
        # No stripe pattern survives at any zoom level: column means stay flat.
        columns = [ImageStat.Stat(region.crop((x, 0, x + 1, region.height))).mean[0] for x in range(0, region.width, 2)]
        assert max(columns) - min(columns) < 90, (t, max(columns), min(columns))

# ---- overlays appear on schedule -----------------------------------------
target_frame = M.box_to_frame(M.target_box(redact_step), scene.final_camera, scene.iw, scene.ih)
cx, cy = int((target_frame[0] + target_frame[2]) / 2), int(target_frame[3] + 60)
early = scene.frame(M.ZOOM_END - 0.05).getpixel((40, M.HEIGHT // 2))
late = scene.frame(3.5).getpixel((40, M.HEIGHT // 2))
assert sum(late) < sum(early) - 60, ("spotlight dims outside the target after the click", early, late)
assert scene.frame(0.5).size == (M.WIDTH, M.HEIGHT)

# ---- target + callout always fit, callout never covers the target ---------
for box in [(40, 100, 180, 140), (1100, 560, 1250, 600), (500, 300, 780, 420), (20, 520, 1260, 560), (600, 95, 700, 120)]:
    place = F.place_callout(box, "Check \u201cMake private\u201d so only your team can see the project", force=True)
    side, x, y, lines, bw, bh = place
    safe = F.CALLOUT_SAFE
    assert safe[0] <= x and x + bw <= safe[2] and safe[1] <= y and y + bh <= safe[3], (box, place)
    assert bh <= 4 * F.CALLOUT_LINE + 2 * F.CALLOUT_PAD_Y and F.CALLOUT_FONT >= 22
    if box != (20, 520, 1260, 560):
        assert x + bw <= box[0] or x >= box[2] or y + bh <= box[1] or y >= box[3], ("callout overlaps target", box, place)
edge_step = {"text": "Open History", "action": {"type": "click", "rect": {"x": 0.86, "y": 0.955, "width": 0.08, "height": 0.04}, "point": {"x": 0.9, "y": 0.975}},
             "annotations": [{"type": "highlight", "x": 0.86, "y": 0.955, "width": 0.08, "height": 0.04, "origin": "action"},
                             {"type": "callout", "x": 0.6, "y": 0.8, "width": 0.26, "height": 0.08, "text": "Open History", "origin": "action"}]}
edge = F.ScreenshotScene(image, edge_step, 2, 3, F.Theme({}), 4.0, [], None)
ex0, ey0, ex1, ey1 = M.box_to_frame(M.target_box(edge_step), edge.final_camera, edge.iw, edge.ih)
assert ex0 >= 0 and ey0 >= 0 and ex1 <= M.WIDTH and ey1 <= M.HEIGHT - 140, ("edge target fully visible above captions", ex0, ey0, ex1, ey1)
assert edge.focus["scale"] > 1.0, "still zoomed toward the target"
cside, cx_, cy_, _, cbw, cbh = edge.callout_place
assert cx_ + cbw <= ex0 or cx_ >= ex1 or cy_ + cbh <= ey0 or cy_ >= ey1, "edge callout overlaps target"

# ---- voices ---------------------------------------------------------------
listing = """Albert              en_US    # Hello! My name is Albert.
Daniel              en_GB    # Hello! My name is Daniel.
Eddy (English (US)) en_US    # Hello! My name is Eddy.
Samantha            en_US    # Hello! My name is Samantha.
Zarvox              en_US    # Hello! My name is Zarvox.
Thomas              fr_FR    # Bonjour, je m’appelle Thomas.
"""
voices = parse_say_voices(listing)
assert {"name": "Eddy (English (US))", "locale": "en_US", "quality": "standard"} in voices
assert best_voice(voices) == "Samantha"
assert best_voice(parse_say_voices(listing + "Ava (Premium)       en_US    # Hello! My name is Ava.\n")) == "Ava (Premium)"
assert best_voice(parse_say_voices(listing + "Zoe (Enhanced)      en_US    # Hello! My name is Zoe.\n")) == "Zoe (Enhanced)"
assert best_voice(voices, "Daniel") == "Daniel" and best_voice(voices, "Nope") == "Samantha"
assert best_voice(parse_say_voices("Zarvox              en_US    # x\n")) is None
assert speech_text('Click “Create project” — then save') == "Click Create project, then save"

# ---- full render: intro, animated steps, outro ----------------------------
if not shutil.which("ffmpeg"):
    print("guide video: PASS (unit only; FFmpeg unavailable)")
    raise SystemExit(0)
renderer = Renderer()
calls = []
def fake_speech(folder, text, output, **_options):
    calls.append(text)
    subprocess.run([renderer.ffmpeg, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=500:duration=1.5", "-ac", "1", output],
                   cwd=folder, check=True, capture_output=True, timeout=30)
renderer.speech = fake_speech
buffer = BytesIO()
image.save(buffer, "PNG")
data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
steps = [{"image": data_url, "text": "Open the account", "annotations": redact_step["annotations"][1:], "action": redact_step["action"], "focus": redact_step["focus"]},
         {"image": data_url, "text": "Review the summary", "annotations": [], "action": {"type": "navigate"}}]
with tempfile.TemporaryDirectory() as temp:
    output = renderer.video(steps, Path(temp), {"title": "Account guide", "accent": "#ff3366"}, "This guide shows the account page.", "You're done.")
    assert sorted(calls) == sorted(["This guide shows the account page.", "Open the account", "Review the summary", "You're done."]), calls
    scenes = renderer.last_export_scenes
    assert [scene["kind"] for scene in scenes] == ["intro", "step", "step", "outro"], scenes
    meta = json.loads(subprocess.check_output([renderer.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)], text=True))
    video = next(s for s in meta["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (1280, 720, "30/1"), video
    assert any(s["codec_type"] == "audio" for s in meta["streams"])
    total = float(meta["format"]["duration"])
    assert abs(total - sum(scene["duration"] for scene in scenes)) < 0.25, (total, scenes)
    def grab(t):
        raw = subprocess.check_output([renderer.ffmpeg, "-v", "error", "-ss", f"{t:.3f}", "-i", str(output), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], timeout=30)
        return Image.frombytes("RGB", (1280, 720), raw)
    step_start = scenes[1]["start"]
    accent_bar = grab(1.5).getpixel((640, 2))
    assert accent_bar[0] > 200 and accent_bar[1] < 120, ("intro card carries the brand accent", accent_bar)
    assert grab(step_start + 0.3).tobytes() != grab(step_start + 3.0).tobytes(), "screenshot scene is animated"
print("guide video: PASS (timing, eased camera, cursor arc, ripple, redaction baked first, voice ranking, intro/steps/outro render)")
