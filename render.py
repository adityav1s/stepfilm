import base64
import binascii
import hashlib
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from caption_timeline import build_export_caption_tracks
import guide_motion


# --------------------------------------------------------------------------
# macOS voices

NOVELTY_VOICES = {"albert", "bad news", "bahh", "bells", "boing", "bubbles", "cellos", "wobble", "good news",
                  "jester", "organ", "superstar", "trinoids", "whisper", "zarvox", "fred", "junior", "ralph",
                  "kathy", "deranged", "hysterical", "pipe organ", "princess", "bruce", "agnes", "vicki", "victoria"}
# Natural-sounding classic voices, best first, used when no Premium/Enhanced voice is installed.
PREFERRED_VOICES = ["ava", "zoe", "allison", "evan", "nathan", "susan", "tom", "samantha", "alex", "serena",
                    "daniel", "kate", "karen", "moira", "tessa", "rishi"]
VOICE_RATES = {"slower": 160, "natural": 182, "brisk": 205}


def parse_say_voices(listing):
    """Parse `say -v '?'` output into [{name, locale, quality}]."""
    voices = []
    for line in str(listing or "").splitlines():
        match = re.match(r"^(.*?)\s+([a-z]{2,3}_[A-Za-z0-9]+)\s+#", line)
        if not match:
            continue
        name, locale = match.group(1).strip(), match.group(2)
        quality = "premium" if "(Premium)" in name else "enhanced" if "(Enhanced)" in name else "standard"
        voices.append({"name": name, "locale": locale, "quality": quality})
    return voices


def voice_rank(voice):
    """Lower is better. Premium/Enhanced en_US first, then natural classics (Samantha), never novelty voices."""
    name = voice["name"]
    base = re.sub(r"\s*\(.*\)\s*$", "", name).strip().lower()
    if not voice["locale"].startswith("en_") or base in NOVELTY_VOICES:
        return None
    if base in {"eddy", "flo", "grandma", "grandpa", "reed", "rocko", "sandy", "shelley"}:
        quality_rank = 6  # Eloquence voices: intelligible but robotic
    else:
        quality_rank = {"premium": 0, "enhanced": 1}.get(voice["quality"], 3)
    locale_rank = 0 if voice["locale"] == "en_US" else 1 if voice["locale"] in {"en_GB", "en_AU", "en_IE", "en_CA"} else 2
    preferred = PREFERRED_VOICES.index(base) if base in PREFERRED_VOICES else len(PREFERRED_VOICES)
    return (quality_rank, locale_rank, preferred, name)


def narration_voices(voices):
    ranked = [(voice_rank(voice), voice) for voice in voices]
    return [voice for rank, voice in sorted((item for item in ranked if item[0] is not None), key=lambda item: item[0])]


def best_voice(voices, override=None):
    names = {voice["name"] for voice in voices}
    if override and override in names:
        return override
    ranked = narration_voices(voices)
    return ranked[0]["name"] if ranked else None


PIPER_LABELS = {
    "en_US-lessac-high": "Lessac (natural)",
    "en_US-lessac-medium": "Lessac (natural)",
    "en_US-amy-medium": "Amy (natural)",
    "en_US-hfc_female-medium": "HFC female (natural)",
    "en_US-ryan-high": "Ryan (natural, male)",
}
PIPER_ORDER = ["en_US-lessac-high", "en_US-lessac-medium", "en_US-amy-medium", "en_US-hfc_female-medium", "en_US-ryan-high"]
PIPER_LENGTH = {"slower": 1.12, "natural": 1.0, "brisk": 0.9}


def find_piper(env=None):
    """Locate a Piper binary: STEPFILM_PIPER, ~/.stepfilm/piper-venv/bin/piper, then PATH."""
    env = os.environ if env is None else env
    candidates = [env.get("STEPFILM_PIPER", ""), str(Path.home() / ".stepfilm" / "piper-venv" / "bin" / "piper"), shutil.which("piper") or ""]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def find_piper_voices(env=None):
    """Piper models (*.onnx with a matching .onnx.json) from STEPFILM_VOICES_DIR or ~/.stepfilm/voices."""
    env = os.environ if env is None else env
    folder = Path(env.get("STEPFILM_VOICES_DIR") or (Path.home() / ".stepfilm" / "voices"))
    voices = []
    if folder.is_dir():
        for model in sorted(folder.glob("*.onnx")):
            if Path(f"{model}.json").is_file():
                voices.append(piper_voice_entry(model))
    voices.sort(key=lambda voice: (PIPER_ORDER.index(voice["model_name"]) if voice["model_name"] in PIPER_ORDER else len(PIPER_ORDER), voice["model_name"]))
    return voices


def piper_voice_entry(model):
    model = Path(model)
    stem = model.name[:-5] if model.name.endswith(".onnx") else model.stem
    parts = stem.split("-")
    locale = parts[0] if parts and re.fullmatch(r"[a-z]{2,3}_[A-Z]{2}", parts[0]) else ""
    label = PIPER_LABELS.get(stem) or (f"{parts[1].replace('_', ' ').title()} (natural)" if len(parts) > 1 else f"{stem} (natural)")
    return {"id": f"piper:{stem}", "name": label, "label": label, "engine": "piper", "locale": locale,
            "quality": "natural", "model": str(model), "model_name": stem}


def speech_text(text):
    """Small clean-ups so local TTS reads UI copy naturally."""
    text = guide_motion.humanize_urls(str(text or "").strip())
    text = re.sub(r"[\"“”«»]", "", text)
    text = text.replace("→", ", then ").replace(" & ", " and ").replace("…", ".")
    text = re.sub(r"\s+[—–]\s+", ", ", text)
    return text



# Keep narration peaks ~1.5 dB under full scale so loud words never clip.
NARRATION_LIMITER = "alimiter=limit=0.84:level=false"

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
        self.voices = []          # macOS say voices
        self.piper_voices = []    # Piper neural voices
        self.voice = None
        self._preview_cache = OrderedDict()
        self._preview_lock = threading.Lock()
        self.piper = find_piper()
        if model:
            if not self.piper or not model.is_file() or not Path(f"{model}.json").is_file():
                raise ValueError("Piper requires the piper command, a voice .onnx file, and its matching .onnx.json file.")
            self.piper_voices = [piper_voice_entry(model)]
        elif self.piper:
            self.piper_voices = find_piper_voices()
        if sys.platform == "darwin" and shutil.which("say"):
            try:
                listing = subprocess.check_output(["say", "-v", "?"], text=True, timeout=15)
            except (subprocess.SubprocessError, OSError):
                listing = ""
            self.voices = parse_say_voices(listing)
        override = os.environ.get("STEPFILM_VOICE", "").strip() or None
        piper_ids = [voice["id"] for voice in self.piper_voices]
        if override and (override in piper_ids or f"piper:{override}" in piper_ids):
            self.voice = override if override in piper_ids else f"piper:{override}"
        elif override and override in {voice["name"] for voice in self.voices}:
            self.voice = override
        elif piper_ids:
            self.voice = piper_ids[0]
        else:
            self.voice = best_voice(self.voices)
        if self.voice and self.voice.startswith("piper:"):
            self.engine = "Piper"
        elif self.voices:
            self.engine = "macOS speech"
        else:
            filters = subprocess.check_output([self.ffmpeg, "-hide_banner", "-filters"], text=True, timeout=15)
            if not re.search(r"\bflite\s", filters):
                raise ValueError("Use an FFmpeg build with the flite filter, or install Piper (see README).")
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

    def voice_name(self, voice_id):
        for voice in self.piper_voices:
            if voice["id"] == voice_id:
                return voice["label"]
        return voice_id

    @property
    def voice_label(self):
        return f"{self.engine} · {self.voice_name(self.voice)}" if self.voice else self.engine

    def voice_list(self):
        """Natural Piper voices first, then narration-quality macOS voices."""
        piper = [{key: voice[key] for key in ("id", "name", "label", "engine", "locale", "quality")} | {"default": voice["id"] == self.voice, "natural": True}
                 for voice in self.piper_voices]
        system = [{"id": voice["name"], "name": voice["name"], "label": voice["name"], "engine": "say", "locale": voice["locale"],
                   "quality": voice["quality"], "default": voice["name"] == self.voice, "natural": False} for voice in narration_voices(self.voices)]
        return piper + system

    def resolve_voice(self, voice=None):
        if voice in (None, ""):
            return self.voice
        if isinstance(voice, str):
            if voice in {item["id"] for item in self.piper_voices}:
                return voice
            if voice in {item["name"] for item in self.voices}:
                return voice
        raise ValueError("That voice is not installed on this computer. Choose one from the voice list.")

    def speech(self, folder, text, output, pace="natural", voice=None):
        folder = Path(folder)
        text = speech_text(text)
        if not text:
            self.ff(folder, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "0.6", output)
            return
        line = folder / f"{Path(output).stem}.txt"
        line.write_text(text, encoding="utf-8")
        chosen = self.resolve_voice(voice)
        piper_voice = next((item for item in self.piper_voices if item["id"] == chosen), None)
        if piper_voice and self.piper:
            subprocess.run([self.piper, "--model", piper_voice["model"], "--output_file", output,
                            "--length_scale", str(PIPER_LENGTH.get(pace, 1.0)), "--sentence_silence", "0.25"],
                           cwd=folder, input=text, text=True, check=True, capture_output=True, timeout=120)
        elif self.voices:
            command = ["say", "-f", line.name, "-o", output, "--file-format=WAVE", "--data-format=LEI16@44100",
                       "-r", str(VOICE_RATES.get(pace, VOICE_RATES["natural"]))]
            if chosen and not chosen.startswith("piper:"):
                command[1:1] = ["-v", chosen]
            subprocess.run(command, cwd=folder, check=True, capture_output=True, timeout=90)
        else:
            self.ff(folder, "-f", "lavfi", "-i", f"flite=textfile={line.name}:voice=slt", output)

    def trim_silence(self, folder, audio):
        """Trim leading/trailing silence so scenes end a beat after the voice, not after TTS padding."""
        trimmed = Path(folder) / f"{Path(audio).stem}.trim.wav"
        try:
            self.ff(folder, "-i", str(audio), "-af",
                    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,"
                    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse",
                    "-ac", "1", "-ar", "44100", str(trimmed))
        except subprocess.SubprocessError:
            return Path(audio)
        return trimmed if trimmed.is_file() and trimmed.stat().st_size > 2000 else Path(audio)

    def step_audio(self, folder, step, voice=None):
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
        options = {}
        if step.get("voicePace") in {"slower", "brisk"}:
            options["pace"] = step["voicePace"]
        if voice:
            options["voice"] = voice
        self.speech(folder, step["text"], "scene.wav", **options)
        return "scene.wav"

    def preview(self, text, voice=None, pace="natural"):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
            raise ValueError("Enter a narration line of 1–400 characters.")
        voice = self.resolve_voice(voice)
        pace = pace if pace in VOICE_RATES else "natural"
        key = hashlib.sha256(f"{voice}\0{pace}\0{text.strip()}".encode()).hexdigest()
        with self._preview_lock:
            if key in self._preview_cache:
                self._preview_cache.move_to_end(key)
                return self._preview_cache[key]
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.speech(folder, text, "preview.wav", pace=pace, voice=voice)
            audio = (folder / "preview.wav").read_bytes()
        with self._preview_lock:
            self._preview_cache[key] = audio
            while len(self._preview_cache) > 96:
                self._preview_cache.popitem(last=False)
        return audio

    def audio_duration(self, folder, audio):
        text = subprocess.check_output([
            self.ffprobe, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(audio)
        ], cwd=folder, text=True, timeout=15).strip()
        try:
            return max(0.0, float(text))
        except ValueError as error:
            raise RuntimeError("Local speech produced no audio. Check macOS speech permissions or use blank narration for a silent scene.") from error

    def video_step_segment(self, folder, step, brand, is_first, name, voice=None):
        """Captured clip step: the clip plays as recorded, with its narration and burned captions."""
        source = Path(step["media_path"])
        scene_audio = self.step_audio(folder, step, voice)
        try:
            source_duration = self.audio_duration(folder, source)
            audio_duration = self.audio_duration(folder, scene_audio)
        except RuntimeError as error:
            raise RuntimeError("The captured video or its narration has no readable duration.") from error
        duration = max(source_duration, audio_duration)
        scene_duration = max(2.0, duration + 0.55)
        overlay_inputs, overlay_graph = self.caption_overlay_inputs(folder, step, scene_duration, brand, is_first, audio_duration)
        video_args = ["-stream_loop", "-1", "-i", str(source), "-i", scene_audio, *overlay_inputs]
        if overlay_graph:
            video_args += ["-filter_complex", overlay_graph, "-map", "[vout]", "-map", "1:a:0"]
        else:
            video_args += ["-map", "0:v:0", "-map", "1:a:0", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1"]
        video_args += ["-af", f"adelay=180:all=1,{NARRATION_LIMITER},apad", "-t", str(scene_duration), "-r", str(guide_motion.FPS),
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-ac", "1", "-ar", "44100", str(folder / name)]
        self.ff(folder, *video_args)
        return audio_duration, 0.18

    def encode_frames(self, folder, scene, duration, audio, audio_lead, output):
        """Pipe Pillow frames to x264 and mux the narration, delayed by audio_lead."""
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{guide_motion.WIDTH}x{guide_motion.HEIGHT}",
                   "-r", str(guide_motion.FPS), "-i", "pipe:0"]
        if audio:
            command += ["-i", str(audio)]
        else:
            command += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono"]
        delay = int(round(audio_lead * 1000))
        command += ["-map", "0:v:0", "-map", "1:a:0", "-af", f"adelay={delay}:all=1,{NARRATION_LIMITER},apad" if audio else "anull",
                    "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-r", str(guide_motion.FPS), "-g", str(guide_motion.FPS * 2),
                    "-c:a", "aac", "-b:a", "160k", "-ac", "1", "-ar", "44100", str(output)]
        with open(folder / f"{Path(output).stem}.log", "wb") as log:
            process = subprocess.Popen(command, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            try:
                for t in guide_motion.frame_times(duration):
                    process.stdin.write(scene.frame(t).tobytes())
                process.stdin.close()
                code = process.wait(timeout=180)
            except BaseException:
                process.kill()
                process.wait()
                raise
        if code != 0:
            raise subprocess.CalledProcessError(code, command)

    def step_cues(self, step, audio_duration, audio_lead):
        """Burned-in caption cues for a screenshot scene."""
        captions = step.get("captions") or []
        language = step.get("captionLanguage")
        if captions:
            base = max(0.0, float(step.get("captureStart", 0) or 0))
            capture_end = max(base, float(step.get("captureEnd", base) or base))
            span = capture_end - base
            scale = audio_duration / span if span > 0 and audio_duration > 0 else 1.0
            cues = []
            for caption in captions:
                if not isinstance(caption, dict):
                    continue
                translations = caption.get("translations") if isinstance(caption.get("translations"), dict) else {}
                text = str((translations.get(language) if language else "") or caption.get("text", "")).strip()
                try:
                    start = (float(caption.get("start", 0)) - base) * scale + audio_lead
                    end = (float(caption.get("end", 0)) - base) * scale + audio_lead
                except (TypeError, ValueError):
                    continue
                if text and end > start:
                    cues.append({"start": max(0.0, start), "end": end, "text": text})
            if cues:
                return cues
        return guide_motion.caption_chunks(step.get("text", ""), audio_duration)

    def video(self, steps, folder, brand=None, intro="", outro="", voice=None):
        """Render the Guidde-style MP4: intro card, animated steps, captured clips, outro card."""
        from PIL import Image
        import guide_frames

        validate_steps(steps)
        voice = self.resolve_voice(voice) if voice else None
        folder = Path(folder)
        brand = brand if isinstance(brand, dict) else {}
        theme = guide_frames.Theme(brand)
        title = str(brand.get("title", "") or "").strip()[:150]
        intro = str(intro or "").strip()[:300]
        outro = str(outro or "").strip()[:300]
        screenshot_total = len(steps)

        # Scene plan: ("intro"|"outro"|"step"|"clip", payload)
        plan = []
        if title or intro:
            plan.append(("intro", None))
        for index, step in enumerate(steps):
            plan.append(("clip" if step.get("kind") == "video" else "step", index))
        if outro:
            plan.append(("outro", None))

        def scene_folder(position):
            path = folder / f"scene-{position:02}"
            path.mkdir(exist_ok=True)
            return path

        # Phase 1: narration for every scene, in parallel (say is single-threaded and slow to start).
        def make_audio(item):
            position, (kind, index) = item
            sub = scene_folder(position)
            if kind == "clip":
                return None
            if kind in {"intro", "outro"}:
                text = intro if kind == "intro" else outro
                if not text:
                    return None, 0.0
                self.speech(sub, text, "card.wav", **({"voice": voice} if voice else {}))
                card = self.trim_silence(sub, sub / "card.wav")
                return card, self.audio_duration(sub, card)
            step = steps[index]
            if not str(step.get("text", "")).strip() and step.get("audioSource") not in {"recorded", "synthetic"}:
                return None, 0.0
            audio = sub / self.step_audio(sub, step, voice)
            if step.get("audioSource") not in {"recorded", "synthetic"}:
                audio = self.trim_silence(sub, audio)
            duration = self.audio_duration(sub, audio)
            if duration <= 0:
                raise RuntimeError("Local speech produced an empty audio track. Check macOS speech permissions or use blank narration for a silent scene.")
            return audio, duration

        workers = max(2, min(4, (os.cpu_count() or 4) // 2))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            audio = list(pool.map(make_audio, enumerate(plan)))

        # Phase 2: build scene drawers (cheap); chain cursor and crossfade frames.
        scenes = []
        prev_point = None
        backdrop = None
        first_image = last_image = None
        for kind, index in plan:
            if kind == "step":
                _, data = image_bytes(steps[index]["image"])
                with Image.open(BytesIO(data)) as opened:
                    image = opened.convert("RGB")
                first_image = first_image or image
                last_image = image
        for position, (kind, index) in enumerate(plan):
            if kind == "clip":
                scenes.append(None)
                continue
            path, voice_length = audio[position]
            if kind in {"intro", "outro"}:
                duration = guide_motion.card_duration(voice_length)
                drawer = guide_frames.CardScene(kind, title, guide_motion.humanize_urls(intro if kind == "intro" else outro), theme, duration,
                                                first_image if kind == "intro" else last_image, screenshot_total)
                scenes.append({"drawer": drawer, "duration": duration, "audio": path, "lead": guide_motion.CARD_LEAD, "voice": voice_length})
                continue
            step = steps[index]
            _, data = image_bytes(step["image"])
            with Image.open(BytesIO(data)) as opened:
                image = opened.convert("RGB")
            point = guide_motion.click_point(step)
            has_target = point is not None or guide_motion.target_box(step) is not None
            duration = guide_motion.scene_duration(voice_length, has_target)
            lead = guide_motion.AUDIO_LEAD
            caption_mode = brand.get("captions") if brand.get("captions") in {"auto", "always", "off"} else "auto"
            cues = self.step_cues(step, voice_length, lead) if guide_motion.burn_captions(step, caption_mode) else []
            drawer = guide_frames.ScreenshotScene(image, step, index + 1, screenshot_total, theme, duration, cues, prev_point)
            if point is not None:
                prev_point = point
            scenes.append({"drawer": drawer, "duration": duration, "audio": path, "lead": lead, "voice": voice_length})
        for position, scene in enumerate(scenes):
            if scene and position and scenes[position - 1]:
                scene["drawer"].prev_frame = scenes[position - 1]["drawer"].last_frame()

        # Phase 3: encode every scene in parallel.
        def encode(position):
            kind, index = plan[position]
            sub = scene_folder(position)
            name = f"scene-{position:02}.mp4"
            if kind == "clip":
                voice_length, lead = self.video_step_segment(sub, steps[index], brand, position == 0, name, voice)
            else:
                scene = scenes[position]
                self.encode_frames(sub, scene["drawer"], scene["duration"], scene["audio"], scene["lead"], sub / name)
                voice_length, lead = scene["voice"], scene["lead"]
            return sub / name, voice_length, lead

        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(encode, range(len(plan))))

        segments = [f"file '{path.relative_to(folder)}'" for path, _, _ in results]
        caption_steps, audio_durations, scene_durations = [], [], []
        for (kind, index), (path, voice_length, _) in zip(plan, results):
            caption_steps.append(steps[index] if kind in {"step", "clip"} else {"id": kind, "captions": []})
            audio_durations.append(voice_length)
            scene_durations.append(self._duration(path))
        self.last_export_caption_tracks = build_export_caption_tracks(caption_steps, audio_durations, scene_durations)
        self.last_export_scenes = [{"kind": kind, "start": round(sum(scene_durations[:i]), 3), "duration": round(scene_durations[i], 3),
                                    **({"badge": list(scenes[i]["drawer"].badge_pos)} if kind == "step" else {}),
                                    **({"captions": bool(scenes[i]["drawer"].cues)} if kind == "step" else {})}
                                   for i, (kind, _) in enumerate(plan)]
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
