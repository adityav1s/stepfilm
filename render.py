import base64
import binascii
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class GuideParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.steps = []
        self.step = None
        self.in_title = False
        self.in_description = False
        self.description_done = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "section" and "data-step" in attrs:
            self.step = {"text": "", "image": ""}
            self.description_done = False
        if self.step is None:
            return
        if tag == "p" and not self.description_done:
            self.in_description = True
        if tag in {"a", "span"} and self.in_description:
            self.in_description = False
            self.description_done = True
        if tag == "img" and not self.step["image"]:
            self.step["image"] = attrs.get("src", "")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "p" and self.step is not None:
            self.in_description = False
            self.description_done = True
        if tag == "section" and self.step is not None:
            self.step["text"] = self.step["text"].strip()
            self.steps.append(self.step)
            self.step = None
            self.in_description = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_description:
            self.step["text"] += data


def image_bytes(url):
    if not isinstance(url, str):
        raise ValueError("A screenshot is missing.")
    match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", url)
    if not match:
        raise ValueError("Each step needs an embedded PNG, JPEG, or WebP screenshot. Enable screenshots in Mimik’s HTML export.")
    kind, encoded = match.groups()
    try:
        data = base64.b64decode(encoded, validate=True)
    except binascii.Error as error:
        raise ValueError("A screenshot is damaged.") from error
    signatures = {"png": b"\x89PNG\r\n\x1a\n", "jpeg": b"\xff\xd8\xff", "webp": b"RIFF"}
    if not data.startswith(signatures[kind]) or (kind == "webp" and data[8:12] != b"WEBP"):
        raise ValueError("A screenshot is damaged.")
    return kind, data


def validate_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 50:
        raise ValueError("A guide must have 1–50 steps.")
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("text"), str) or len(step["text"]) > 400:
            raise ValueError("Keep each narration line within 400 characters.")
        image_bytes(step.get("image"))


def parse_guide(html):
    parser = GuideParser()
    parser.feed(html)
    parser.close()
    validate_steps(parser.steps)
    return {"title": parser.title.strip()[:150] or "Untitled guide", "steps": parser.steps}


class Renderer:
    def __init__(self, model=None):
        self.ffmpeg = shutil.which("ffmpeg")
        self.ffprobe = shutil.which("ffprobe")
        if not self.ffmpeg or not self.ffprobe:
            raise ValueError("Install FFmpeg and make sure ffmpeg and ffprobe are on your PATH.")
        self.model = model
        self.piper = shutil.which("piper") if model else None
        if model:
            if not self.piper or not model.is_file() or not Path(f"{model}.json").is_file():
                raise ValueError("Piper requires the piper command, a voice .onnx file, and its matching .onnx.json file.")
            self.engine = "Piper"
        elif sys.platform == "darwin" and shutil.which("say"):
            self.engine = "macOS speech"
        else:
            filters = subprocess.check_output([self.ffmpeg, "-hide_banner", "-filters"], text=True, timeout=15)
            if not re.search(r"\bflite\s", filters):
                raise ValueError("Use an FFmpeg build with the flite filter or start Stepfilm with --piper-model.")
            self.engine = "Flite"

    def ff(self, folder, *args):
        subprocess.run([self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
                       cwd=folder, check=True, capture_output=True, timeout=120)

    def speech(self, folder, text, output):
        if not text.strip():
            self.ff(folder, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1.8", output)
            return
        (folder / "line.txt").write_text(text, encoding="utf-8")
        if self.piper:
            subprocess.run([self.piper, "--model", str(self.model), "--output_file", output],
                           cwd=folder, input=text, text=True, check=True, capture_output=True, timeout=90)
        elif self.engine == "macOS speech":
            subprocess.run(["say", "-f", "line.txt", "-o", "line.aiff"], cwd=folder,
                           check=True, capture_output=True, timeout=60)
            self.ff(folder, "-i", "line.aiff", output)
        else:
            self.ff(folder, "-f", "lavfi", "-i", "flite=textfile=line.txt:voice=slt", output)

    def preview(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
            raise ValueError("Enter a narration line of 1–400 characters.")
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.speech(folder, text, "preview.wav")
            return (folder / "preview.wav").read_bytes()

    def video(self, steps, folder):
        validate_steps(steps)
        segments = []
        for index, step in enumerate(steps):
            kind, screenshot = image_bytes(step["image"])
            image = f"scene.{kind}"
            (folder / image).write_bytes(screenshot)
            self.speech(folder, step["text"], "scene.wav")
            duration_text = subprocess.check_output([
                self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", "scene.wav"
            ], cwd=folder, text=True, timeout=15).strip()
            try:
                duration = float(duration_text)
            except ValueError as error:
                raise RuntimeError("Local speech produced no audio. Check macOS speech permissions or use blank narration for a silent scene.") from error
            if duration <= 0:
                raise RuntimeError("Local speech produced an empty audio track. Check macOS speech permissions or use blank narration for a silent scene.")
            name = f"scene-{index:02}.mp4"
            self.ff(folder, "-f", "image2", "-pattern_type", "none", "-loop", "1", "-framerate", "24",
                    "-i", image, "-i", "scene.wav",
                    "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1",
                    "-af", "adelay=180:all=1,apad", "-t", str(max(2.0, duration + 0.55)),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ac", "1", "-ar", "44100", name)
            segments.append(f"file '{name}'")
        (folder / "segments.txt").write_text("\n".join(segments) + "\n", encoding="utf-8")
        self.ff(folder, "-f", "concat", "-safe", "1", "-i", "segments.txt",
                "-c", "copy", "-movflags", "+faststart", "tutorial.mp4")
        return folder / "tutorial.mp4"
