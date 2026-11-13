import base64
import binascii
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from caption_timeline import build_export_caption_tracks


class GuideParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.steps = []
        self.step = None
        self.in_title = False
        self.in_description = False
        self.in_step_description = False
        self.description_done = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "section" and "data-step" in attrs:
            self.step = {"text": "", "description": "", "image": ""}
            self.description_done = False
        if self.step is None:
            return
        if tag == "p" and attrs.get("data-description") == "true":
            self.in_step_description = True
        elif tag == "p" and not self.description_done:
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
            if self.in_step_description:
                self.in_step_description = False
            else:
                self.description_done = True
        if tag == "section" and self.step is not None:
            self.step["text"] = self.step["text"].strip()
            self.step["description"] = self.step["description"].strip()
            self.steps.append(self.step)
            self.step = None
            self.in_description = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if self.in_description:
            self.step["text"] += data
        elif self.in_step_description:
            self.step["description"] += data


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
        if step.get("kind") == "video":
            media_path = Path(step.get("media_path", ""))
            if not media_path.is_file() or media_path.suffix.lower() != ".webm":
                raise ValueError("A video step needs an available captured WebM clip.")
        else:
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

    def caption_overlay_inputs(self, folder, step, duration, brand=None, is_first=False, audio_duration=None):
        captions = step.get("captions") or []
        brand = brand if isinstance(brand, dict) else {}
        theme = brand.get("theme", "clean") if brand.get("theme") in {"clean", "editorial", "bold"} else "clean"
        accent = brand.get("accent", "#6d8eff")
        if not isinstance(accent, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent): accent = "#6d8eff"
        title_style = brand.get("titleStyle", "standard") if brand.get("titleStyle") in {"standard", "compact", "display"} else "standard"
        logo_data = brand.get("logo", "")
        has_brand = bool(theme != "clean" or accent.lower() != "#6d8eff" or title_style != "standard" or logo_data)
        if not captions and not has_brand:
            return [], ""
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError as error:
            raise RuntimeError("Caption burn-in needs Pillow. Install it with `python3 -m pip install Pillow`, then restart Stepfilm.") from error
        def load_font(size):
            for candidate in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"):
                try: return ImageFont.truetype(candidate, size)
                except OSError: continue
            try: return ImageFont.load_default(size=size)
            except TypeError: return ImageFont.load_default()
        font = load_font(38)
        base = max(0.0, float(step.get("captureStart", 0)))
        capture_end = max(base, float(step.get("captureEnd", base)))
        capture_span = capture_end - base
        cue_scale = (max(0.0, float(audio_duration)) / capture_span) if audio_duration is not None and capture_span > 0 else 1.0
        caption_language = step.get("captionLanguage")
        inputs, filters = [], []
        previous = "[base]"
        filters.append("[0:v]scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1[base]")
        overlay_count = 0
        if has_brand:
            image = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            accent_rgb = tuple(int(accent[pos:pos + 2], 16) for pos in (1, 3, 5))
            bar_height = {"clean": 5, "editorial": 7, "bold": 12}[theme]
            draw.rectangle((0, 0, 1280, bar_height - 1), fill=(*accent_rgb, 255))
            y = 22
            if logo_data:
                match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", logo_data)
                if not match or len(logo_data) > 500000:
                    raise ValueError("The saved guide logo is invalid or too large. Remove it or upload a smaller PNG, JPEG, or WebP logo.")
                try:
                    raw_logo = base64.b64decode(match.group(2), validate=True)
                    with Image.open(BytesIO(raw_logo)) as source:
                        logo = source.convert("RGBA")
                        logo.thumbnail((170, 60), Image.Resampling.LANCZOS)
                        image.alpha_composite(logo, (38, y))
                    y += 72
                except (ValueError, OSError) as error:
                    raise ValueError("The saved guide logo cannot be decoded. Remove it or upload a valid image.") from error
            title = str(brand.get("title", ""))[:150]
            if is_first and title:
                size = {"standard": 30, "compact": 22, "display": 42}[title_style]
                title_font = load_font(size)
                if title_style == "compact": title = title.upper()
                color = (255, 255, 255, 255) if theme == "bold" else (20, 26, 40, 255)
                left, top, right, bottom = draw.textbbox((0, 0), title, font=title_font)
                text_width = right - left
                max_width = 1280 - 2 * 38
                if text_width > max_width:
                    while title and draw.textbbox((0, 0), title + "…", font=title_font)[2] > max_width:
                        title = title[:-1]
                    title = title.rstrip() + "…"
                draw.text((38, y), title, font=title_font, fill=color)
            brand_png = f"brand-{len(inputs):03}.png"
            image.save(folder / brand_png)
            inputs.extend(["-loop", "1", "-framerate", "24", "-i", brand_png])
            input_index = 2 + overlay_count
            overlay_count += 1
            current = f"[brand{overlay_count}]"
            filters.append(f"{previous}[{input_index}:v]overlay=0:0:format=auto{current}")
            previous = current
        for cue_index, caption in enumerate(captions):
            translations = caption.get("translations") if isinstance(caption.get("translations"), dict) else {}
            text = str((translations.get(caption_language) if caption_language else "") or caption.get("text", "")).strip()
            start = max(0.0, (float(caption.get("start", 0)) - base) * cue_scale + 0.18)
            end = min(float(duration), max(0.0, (float(caption.get("end", 0)) - base) * cue_scale + 0.18))
            if not text or end <= start:
                continue
            image = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            words = text.split()
            lines, current = [], ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and draw.textbbox((0, 0), candidate, font=font)[2] > 1120:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            if current: lines.append(current)
            if len(lines) > 3:
                lines = lines[:3]
                lines[-1] = lines[-1].rstrip(" .") + "…"
            line_height, padding = 46, 22
            box_height = len(lines) * line_height + padding * 2
            top = 700 - box_height
            draw.rounded_rectangle((38, top, 1242, 700), radius=18, fill=(8, 12, 20, 220))
            for line_index, line in enumerate(lines):
                bounds = draw.textbbox((0, 0), line, font=font)
                x = (1280 - (bounds[2] - bounds[0])) / 2
                draw.text((x, top + padding + line_index * line_height), line, font=font, fill=(255, 255, 255, 255))
            png = f"caption-{len(inputs):03}.png"
            image.save(folder / png)
            inputs.extend(["-loop", "1", "-framerate", "24", "-i", png])
            input_index = 2 + overlay_count
            overlay_count += 1
            current = f"[caption{overlay_count}]"
            filters.append(f"{previous}[{input_index}:v]overlay=0:0:format=auto:enable='between(t\\,{start:.3f}\\,{end:.3f})'{current}")
            previous = current
        filters[-1] = filters[-1].replace(previous, "[vout]")
        return inputs, ";".join(filters)

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

    def step_audio(self, folder, step):
        voiceover = step.get("voiceover", "")
        audio_source = step.get("audioSource") or ("synthetic" if isinstance(voiceover, str) and voiceover.startswith("data:audio/mpeg;base64,") else "local")
        if audio_source == "recorded":
            if not step.get("narration_path") or not Path(step["narration_path"]).is_file():
                raise ValueError("This step is set to recorded microphone audio, but that audio is unavailable. Select synthetic speech or recapture the guide.")
            start, end = max(0.0, float(step.get("narrationStart", 0))), max(0.0, float(step.get("narrationEnd", 0)))
            if end > start:
                self.ff(folder, "-ss", str(start), "-to", str(end), "-i", str(step["narration_path"]), "-vn", "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", "scene.wav")
                if (folder / "scene.wav").is_file() and (folder / "scene.wav").stat().st_size:
                    return "scene.wav"
            raise ValueError("This step has no recorded microphone interval. Transcribe the capture again or choose synthetic speech.")
        if audio_source == "synthetic":
            if not isinstance(voiceover, str) or not voiceover.startswith("data:audio/mpeg;base64,"):
                raise ValueError("AI voiceover is selected, but no generated audio is saved. Generate the voiceover or choose Local speech.")
            try:
                data = base64.b64decode(voiceover.split(",", 1)[1], validate=True)
            except (ValueError, binascii.Error) as error:
                raise ValueError("An AI voiceover is damaged. Generate it again before exporting.") from error
            if len(data) > 10 * 1024 * 1024: raise ValueError("An AI voiceover is too large to export.")
            (folder / "scene.mp3").write_bytes(data)
            return "scene.mp3"
        self.speech(folder, step["text"], "scene.wav")
        return "scene.wav"

    def preview(self, text):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
            raise ValueError("Enter a narration line of 1–400 characters.")
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.speech(folder, text, "preview.wav")
            return (folder / "preview.wav").read_bytes()

    def video(self, steps, folder, brand=None):
        validate_steps(steps)
        segments = []
        rendered_audio_durations = []
        rendered_scene_durations = []
        for index, step in enumerate(steps):
            if step.get("kind") == "video":
                source = Path(step["media_path"])
                scene_audio = self.step_audio(folder, step)
                video_duration_text = subprocess.check_output([
                    self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(source)
                ], text=True, timeout=15).strip()
                audio_duration_text = subprocess.check_output([
                    self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", scene_audio
                ], cwd=folder, text=True, timeout=15).strip()
                try:
                    source_duration = float(video_duration_text)
                    audio_duration = float(audio_duration_text)
                    duration = max(source_duration, audio_duration)
                except ValueError as error:
                    raise RuntimeError("The captured video or its narration has no readable duration.") from error
                name = f"scene-{index:02}.mp4"
                scene_duration = max(2.0, duration + 0.55)
                overlay_inputs, overlay_graph = self.caption_overlay_inputs(folder, step, scene_duration, brand, index == 0, audio_duration)
                video_args = ["-stream_loop", "-1", "-i", str(source), "-i", scene_audio, *overlay_inputs]
                if overlay_graph:
                    video_args += ["-filter_complex", overlay_graph, "-map", "[vout]", "-map", "1:a:0"]
                else:
                    video_args += ["-map", "0:v:0", "-map", "1:a:0", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1"]
                video_args += ["-af", "adelay=180:all=1,apad", "-t", str(scene_duration),
                               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                               "-c:a", "aac", "-ac", "1", "-ar", "44100", name]
                self.ff(folder, *video_args)
                rendered_audio_durations.append(audio_duration)
                rendered_scene_durations.append(self._duration(folder / name))
                segments.append(f"file '{name}'")
                continue
            kind, screenshot = image_bytes(step["image"])
            image = f"scene.{kind}"
            (folder / image).write_bytes(screenshot)
            scene_audio = self.step_audio(folder, step)
            duration_text = subprocess.check_output([
                self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", scene_audio
            ], cwd=folder, text=True, timeout=15).strip()
            try:
                duration = float(duration_text)
            except ValueError as error:
                raise RuntimeError("Local speech produced no audio. Check macOS speech permissions or use blank narration for a silent scene.") from error
            if duration <= 0:
                raise RuntimeError("Local speech produced an empty audio track. Check macOS speech permissions or use blank narration for a silent scene.")
            name = f"scene-{index:02}.mp4"
            scene_duration = max(2.0, duration + 0.55)
            overlay_inputs, overlay_graph = self.caption_overlay_inputs(folder, step, scene_duration, brand, index == 0, duration)
            image_args = ["-f", "image2", "-pattern_type", "none", "-loop", "1", "-framerate", "24", "-i", image, "-i", scene_audio, *overlay_inputs]
            if overlay_graph:
                image_args += ["-filter_complex", overlay_graph, "-map", "[vout]", "-map", "1:a:0"]
            else:
                image_args += ["-map", "0:v:0", "-map", "1:a:0", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1"]
            image_args += ["-af", "adelay=180:all=1,apad", "-t", str(scene_duration),
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-ac", "1", "-ar", "44100", name]
            self.ff(folder, *image_args)
            rendered_audio_durations.append(duration)
            rendered_scene_durations.append(self._duration(folder / name))
            segments.append(f"file '{name}'")
        self.last_export_caption_tracks = build_export_caption_tracks(steps, rendered_audio_durations, rendered_scene_durations)
        (folder / "segments.txt").write_text("\n".join(segments) + "\n", encoding="utf-8")
        self.ff(folder, "-f", "concat", "-safe", "1", "-i", "segments.txt",
                "-c", "copy", "-movflags", "+faststart", "tutorial.mp4")
        return folder / "tutorial.mp4"

    def _duration(self, path):
        value = subprocess.check_output([self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                                         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                                        text=True, timeout=15).strip()
        try:
            return max(0.0, float(value))
        except ValueError as error:
            raise RuntimeError("FFmpeg produced a scene with no readable duration.") from error
