import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from math import ceil, isfinite
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
from time import time
import webbrowser
import urllib.error
import urllib.request
from xml.sax.saxutils import escape as xml_escape
from chatgpt_plan import ChatGPTPlan

from render import Renderer, parse_guide
from caption_chunking import build_caption_chunk_ranges


APP = Path(__file__).resolve().parent / "app"
STATIC = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
          "/editor.js": ("editor.js", "text/javascript"), "/annotation-model.js": ("annotation-model.js", "text/javascript"), "/step-selection.js": ("step-selection.js", "text/javascript"), "/guide-export.js": ("guide-export.js", "text/javascript"), "/chatgpt-callback.js": ("chatgpt-callback.js", "text/javascript")}
MAX_REQUEST = 50 * 1024 * 1024
MAX_CAPTURE_VIDEO = 250 * 1024 * 1024
VIDEO_ANALYSIS_MAX_FRAMES = 12
VIDEO_ANALYSIS_MAX_IMAGE_BYTES = 6 * 1024 * 1024
EXPORT_LOCK = Lock()


def group_caption_words(words, max_words=7, max_duration=3.5, max_gap=0.65):
    """Group genuinely timestamped words into compact, readable caption cues."""
    groups = []
    current = []
    for word in words:
        if not isinstance(word, dict):
            continue
        text = str(word.get("text", "")).strip()
        try:
            start, end = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not text or not isfinite(start) or not isfinite(end) or end <= start:
            continue
        if current and (len(current) >= max_words or end - current[0]["start"] > max_duration or start - current[-1]["end"] > max_gap):
            groups.append(current)
            current = []
        current.append({"start": start, "end": end, "text": text})
    if current:
        groups.append(current)
    return [{"start": group[0]["start"], "end": group[-1]["end"], "text": " ".join(word["text"] for word in group)} for group in groups]


def offset_caption_words(words, step_start, chunk_start, chunk_end):
    """Map word times relative to an extracted phrase clip onto the guide timeline."""
    mapped = []
    chunk_duration = max(0.0, float(chunk_end) - float(chunk_start))
    for word in words if isinstance(words, list) else []:
        try:
            word_start = max(0.0, float(word["start"]))
            word_end = min(chunk_duration, float(word["end"]))
            text = str(word.get("word", word.get("text", ""))).strip()
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if text and isfinite(word_start) and isfinite(word_end) and word_end > word_start:
            mapped.append({"start": float(step_start) + float(chunk_start) + word_start,
                           "end": float(step_start) + float(chunk_start) + word_end,
                           "text": text})
    return mapped


def video_analysis_sample_times(duration, max_frames=VIDEO_ANALYSIS_MAX_FRAMES):
    """Return bounded timestamps for a coarse scan used to select visually distinct frames."""
    duration = float(duration)
    if not isfinite(duration) or duration <= 0:
        return []
    count = min(max_frames, max(4, ceil(duration / 3)))
    if duration <= 0.1:
        return [round(duration / 2, 3)]
    start, end = min(0.05, duration / 4), max(duration - 0.5, duration * 3 / 4)
    if end <= start:
        return [round(duration / 2, 3)]
    return [round(start + (end - start) * index / (count - 1), 3) for index in range(count)]


def select_video_analysis_times(scan_times, signatures, max_frames=VIDEO_ANALYSIS_MAX_FRAMES):
    """Choose timeline coverage plus large visual changes from a low-res scan.

    signatures are equal-length grayscale pixel arrays. Greedy temporal coverage
    keeps the result spread across the clip while change scores favor transitions.
    """
    pairs = [(float(t), sig) for t, sig in zip(scan_times, signatures)
             if isinstance(sig, (bytes, bytearray)) and len(sig)]
    if not pairs:
        return []
    limit = min(max(1, int(max_frames)), len(pairs))
    if len(pairs) <= limit:
        return [round(t, 3) for t, _ in pairs]
    times = [t for t, _ in pairs]
    scores = [0.0]
    for (_, left), (_, right) in zip(pairs, pairs[1:]):
        size = min(len(left), len(right))
        scores.append(sum(abs(left[i] - right[i]) for i in range(size)) / max(1, size))
    chosen = {0, len(pairs) - 1}
    while len(chosen) < limit:
        candidates = [i for i in range(len(pairs)) if i not in chosen]
        # Balance temporal spread and scene-change evidence. The distance term
        # encourages broad coverage; score adds preference around visual cuts.
        best = max(candidates, key=lambda i: (min(abs(times[i] - times[j]) for j in chosen) / max(times[-1] - times[0], 1) + scores[i] / 255, -i))
        chosen.add(best)
    return [round(times[i], 3) for i in sorted(chosen)]


def map_video_analysis_events(events, sampled_at, duration, limit=12):
    """Anchor concise editable event suggestions to successfully supplied frames."""
    samples = sorted({float(t) for t in sampled_at
                      if isinstance(t, (int, float)) and isfinite(float(t))
                      and 0 <= float(t) <= duration})
    if not samples:
        return []
    mapped, seen = [], set()
    for event in events if isinstance(events, list) else []:
        if len(mapped) >= limit:
            break
        if not isinstance(event, dict) or not isinstance(event.get("action"), str):
            continue
        action = event["action"].strip()[:160]
        try:
            time = float(event.get("time"))
        except (TypeError, ValueError):
            continue
        if not action or not isfinite(time):
            continue
        snapped = min(samples, key=lambda sample: (abs(sample - time), sample))
        identity = (snapped, action.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        mapped.append({"time": round(snapped, 1), "action": action})
    return sorted(mapped, key=lambda event: event["time"])


def chatgpt_responses_text(plan, messages):
    """Run an eligible text/image task through a user's ChatGPT plan allowance."""
    instructions, inputs = [], []
    for message in messages if isinstance(messages, list) else []:
        if not isinstance(message, dict):
            continue
        role = message.get("role", "user")
        content = message.get("content", "")
        if role in {"system", "developer"}:
            instructions.append(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            continue
        parts = []
        if isinstance(content, str):
            parts.append({"type": "input_text", "text": content})
        elif isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    parts.append({"type": "input_text", "text": str(item.get("text", ""))})
                elif item.get("type") == "image_url":
                    image = item.get("image_url", {})
                    if isinstance(image, dict) and isinstance(image.get("url"), str):
                        parts.append({"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "auto")})
        if parts:
            inputs.append({"role": "assistant" if role == "assistant" else "user", "content": parts})
    payload = {"model": plan.model_slug(), "instructions": "\n\n".join(instructions),
               "input": inputs, "store": False, "stream": True}
    request = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(), headers={
        "Authorization": "Bearer " + plan.access_token(),
        "Content-Type": "application/json", "Accept": "text/event-stream",
    })
    output, completed = [], False
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                raw_event = line[5:].strip()
                if not raw_event or raw_event == "[DONE]":
                    continue
                event = json.loads(raw_event)
                if event.get("type") == "response.output_text.delta":
                    output.append(event.get("delta", ""))
                elif event.get("type") == "response.failed":
                    error = event.get("response", {}).get("error", {})
                    raise RuntimeError(error.get("message", "ChatGPT plan request failed. Check your plan usage and try again."))
                elif event.get("type") == "response.completed":
                    completed = True
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read()).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        raise RuntimeError(f"ChatGPT plan request failed ({error.code})" + (f": {detail}" if detail else ".")) from error
    except urllib.error.URLError as error:
        raise RuntimeError("Could not reach ChatGPT. Check your internet connection.") from error
    if not completed:
        raise RuntimeError("ChatGPT plan response did not complete. Retry the action.")
    return "".join(output).strip()


def ai_json_response(server, payload):
    """Use an API key if configured; otherwise use ChatGPT plan text/image access."""
    key = server.openai_api_key
    if key:
        response = json.loads(server.openai_call("https://api.openai.com/v1/chat/completions", key, payload))
        content = response["choices"][0]["message"]["content"]
    elif server.chatgpt_plan.status()["connected"]:
        content = chatgpt_responses_text(server.chatgpt_plan, payload.get("messages", []))
    else:
        raise RuntimeError("Choose Continue with ChatGPT to use your plan allowance, or connect an API key for separate API billing.")
    if isinstance(content, dict):
        return content
    content = str(content).strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        raise RuntimeError("The AI response was not valid JSON. Retry the action.") from error


def prepare_export_steps(steps, narration_audio_id, media_dir):
    """Attach local clip and per-step narration files before rendering an export."""
    if not isinstance(steps, list):
        return
    media_dir = Path(media_dir)
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("kind") == "video":
            media_id = step.get("videoId", "")
            if not re.fullmatch(r"[a-f0-9]{32}", media_id):
                raise ValueError("A video step is missing its local clip file.")
            media_path = media_dir / f"{media_id}.webm"
            if not media_path.is_file():
                raise ValueError("A captured video step is no longer available on this computer.")
            step["media_path"] = str(media_path)
        if step.get("audioSource") == "recorded":
            narration_id = step.get("recordedAudioId") or narration_audio_id
            if not re.fullmatch(r"[a-f0-9]{32}", narration_id or ""):
                raise ValueError("This step uses recorded narration, but its capture audio is no longer linked. Select synthetic speech or recapture the guide.")
            narration_path = media_dir / f"{narration_id}.narration.webm"
            if not narration_path.is_file():
                raise ValueError("The recorded microphone audio is no longer available on this computer. Select synthetic speech or recapture the guide.")
            step["narration_path"] = str(narration_path)
            step["narrationStart"] = step.get("captureStart", 0)
            step["narrationEnd"] = step.get("captureEnd", 0)


def share_vtt_timestamp(value):
    milliseconds = max(0, round(float(value) * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def format_caption_track(tracks, language, format):
    if format not in {"vtt", "srt"}:
        raise ValueError("Choose VTT or SRT for the MP4-timed caption download.")
    cues = tracks.get(language, []) if isinstance(tracks, dict) else []
    if not cues:
        raise ValueError("Render the guide with this caption track selected before downloading MP4-timed captions.")
    rows = ["WEBVTT"] if format == "vtt" else []
    for index, cue in enumerate(cues, 1):
        start = share_vtt_timestamp(cue["start"])
        end = share_vtt_timestamp(cue["end"])
        if format == "srt": start, end = start.replace(".", ","), end.replace(".", ",")
        text = str(cue.get("text", "")).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        rows.append(f"{index}\n{start} --> {end}\n{text}")
    return "\n\n".join(rows) + "\n"


def create_share_bundle(html, media_steps, media_dir, output_path):
    """Write a portable HTML guide ZIP with local video and caption assets."""
    if not isinstance(html, str) or "<!doctype html>" not in html[:100].lower():
        raise ValueError("The share bundle needs a valid standalone HTML guide.")
    if re.search(r"<script\b|<iframe\b|<object\b|<embed\b", html, re.IGNORECASE):
        raise ValueError("The portable guide contains active HTML and cannot be bundled.")
    if not isinstance(media_steps, list) or len(media_steps) > 50:
        raise ValueError("The guide can bundle up to 50 video steps.")
    media_dir = Path(media_dir)
    output_path = Path(output_path)
    total_size = 0
    seen_media = set()
    seen_steps = set()
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("index.html", html, compress_type=zipfile.ZIP_DEFLATED)
        for media_step in media_steps:
            if not isinstance(media_step, dict):
                raise ValueError("A portable guide video entry is invalid.")
            video_id = media_step.get("videoId", "")
            step_number = media_step.get("stepNumber")
            if not re.fullmatch(r"[a-f0-9]{32}", video_id) or not isinstance(step_number, int) or not 1 <= step_number <= 50 or step_number in seen_steps:
                raise ValueError("A portable guide video reference is invalid.")
            seen_steps.add(step_number)
            media_path = media_dir / f"{video_id}.webm"
            if not media_path.is_file():
                raise ValueError("A captured video is missing from this computer and cannot be packaged.")
            if video_id not in seen_media:
                total_size += media_path.stat().st_size
                if total_size > MAX_CAPTURE_VIDEO:
                    raise ValueError("The portable guide exceeds the 250 MB video bundle limit.")
                archive.write(media_path, f"media/{video_id}.webm", compress_type=zipfile.ZIP_STORED)
                seen_media.add(video_id)
            tracks = media_step.get("captionTracks")
            if tracks is None and isinstance(media_step.get("captions"), list):
                tracks = [{"language": "en", "cues": media_step["captions"]}]
            if not isinstance(tracks, list) or len(tracks) > 10:
                raise ValueError("A video step has invalid caption tracks.")
            for track in tracks:
                if not isinstance(track, dict):
                    continue
                language = track.get("language", "")
                captions = track.get("cues", [])
                if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2,4})?", language) or not isinstance(captions, list) or len(captions) > 1000:
                    raise ValueError("A video step has invalid translated captions.")
                cues = []
                for caption in captions:
                    if not isinstance(caption, dict):
                        continue
                    try:
                        start = float(caption.get("start", 0))
                        end = float(caption.get("end", 0))
                    except (TypeError, ValueError):
                        continue
                    text = str(caption.get("text", "")).strip()
                    if not (isfinite(start) and isfinite(end) and start >= 0 and end > start and text):
                        continue
                    cues.append(f"{share_vtt_timestamp(start)} --> {share_vtt_timestamp(end)}\n{text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')}")
                if cues:
                    archive.writestr(f"captions/step-{step_number}-{language}.vtt", "WEBVTT\n\n" + "\n\n".join(cues) + "\n", compress_type=zipfile.ZIP_DEFLATED)
    return output_path


def scorm12_manifest(title, asset_paths):
    manifest_id = f"STEPFILM-{uuid4().hex}"
    organization_id = f"ORG-{uuid4().hex}"
    item_id = f"ITEM-{uuid4().hex}"
    resource_id = f"RES-{uuid4().hex}"
    files = "\n".join(f'        <file href="{xml_escape(path)}" />' for path in asset_paths)
    safe_title = xml_escape(str(title or "Stepfilm tutorial")[:150])
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<manifest identifier="{manifest_id}" version="1.2"
  xmlns="http://www.imsproject.org/xsd/imscp_rootv1p1p2"
  xmlns:adlcp="http://www.adlnet.org/xsd/adlcp_rootv1p2"
  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
  xsi:schemaLocation="http://www.imsproject.org/xsd/imscp_rootv1p1p2 imscp_rootv1p1p2.xsd http://www.adlnet.org/xsd/adlcp_rootv1p2 adlcp_rootv1p2.xsd">
  <metadata><schema>ADL SCORM</schema><schemaversion>1.2</schemaversion></metadata>
  <organizations default="{organization_id}">
    <organization identifier="{organization_id}" structure="hierarchical">
      <title>{safe_title}</title>
      <item identifier="{item_id}" identifierref="{resource_id}" isvisible="true">
        <title>{safe_title}</title>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="{resource_id}" type="webcontent" adlcp:scormtype="sco" href="index.html">
{files}
    </resource>
  </resources>
</manifest>'''


def create_scorm12_bundle(html, media_steps, media_dir, output_path, title="Stepfilm tutorial"):
    """Build a SCORM 1.2 SCO package from the validated portable guide assets."""
    output_path = Path(output_path)
    with tempfile.TemporaryDirectory() as temp:
        portable = create_share_bundle(html, media_steps, media_dir, Path(temp) / "portable.zip")
        with zipfile.ZipFile(portable, "r") as source:
            names = source.namelist()
            if len(names) > 1200 or len(set(names)) != len(names):
                raise ValueError("The SCORM package contains too many or duplicate files.")
            for name in names:
                if name != "index.html" and not (re.fullmatch(r"media/[a-f0-9]{32}\.webm", name) or re.fullmatch(r"captions/step-[1-9][0-9]?-[a-z]{2,3}(?:-[A-Z]{2,4})?\.vtt", name)):
                    raise ValueError("The SCORM package contains an unsafe asset path.")
            html_page = source.read("index.html").decode("utf-8")
            scorm_ui = '''<aside id="stepfilm-scorm-status" style="font:14px system-ui,sans-serif;padding:12px;border-top:1px solid #ccd3df;margin-top:24px">
  <button id="stepfilm-mark-complete" type="button">Mark tutorial complete</button>
  <span id="stepfilm-completion-message" role="status" aria-live="polite"></span>
</aside>
<script>
(function(){
  function findAPI(win){var depth=0;while(win&&depth<10){try{if(win.API)return win.API;if(win.parent===win)break;win=win.parent;}catch(_){break;}depth++;}return null;}
  var api=findAPI(window), initialized=false;
  try{if(!api&&window.opener)api=findAPI(window.opener);if(api){initialized=String(api.LMSInitialize(""))==="true";if(initialized&&!api.LMSGetValue("cmi.core.lesson_status"))api.LMSSetValue("cmi.core.lesson_status","incomplete");}}catch(_){initialized=false;}
  document.getElementById("stepfilm-mark-complete").addEventListener("click",function(){
    if(initialized){try{api.LMSSetValue("cmi.core.lesson_status","completed");api.LMSCommit("");}catch(_){}}
    document.getElementById("stepfilm-completion-message").textContent=initialized?" Completion recorded in the LMS.":" Tutorial marked complete on this page.";
    this.disabled=true;
  });
  window.addEventListener("beforeunload",function(){if(initialized){try{api.LMSFinish("");}catch(_){}}});
})();
</script>'''
            html_page = re.sub(r"</body>", scorm_ui + "</body>", html_page, count=1, flags=re.IGNORECASE)
            if "stepfilm-mark-complete" not in html_page:
                html_page += scorm_ui
            asset_paths = sorted(names, key=lambda name: (name != "index.html", name))
            manifest = scorm12_manifest(title, asset_paths)
            with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
                package.writestr("imsmanifest.xml", manifest)
                package.writestr("index.html", html_page)
                for name in names:
                    if name != "index.html":
                        package.writestr(name, source.read(name), compress_type=zipfile.ZIP_STORED if name.startswith("media/") else zipfile.ZIP_DEFLATED)
    return output_path


def finalize_webm(ffmpeg, media_path, narration_path=None):
    """Remux a live MediaRecorder WebM and optionally preserve its mic track separately."""
    media_path = Path(media_path)
    finalized = media_path.with_name(f".{media_path.name}.finalizing.webm")
    try:
        subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(media_path),
                        "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "webm", str(finalized)],
                       check=True, capture_output=True, timeout=180)
        if not finalized.is_file() or finalized.stat().st_size <= 0:
            raise RuntimeError("FFmpeg produced an empty finalized tab recording.")
        if narration_path:
            probe = shutil.which("ffprobe")
            audio_stream = subprocess.run([probe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(finalized)], capture_output=True, text=True, timeout=15) if probe else None
            if audio_stream and audio_stream.stdout.strip():
                narration_path = Path(narration_path)
                audio_temp = narration_path.with_name(f".{narration_path.name}.finalizing.webm")
                subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(finalized),
                                "-map", "0:a:0", "-vn", "-c:a", "copy", "-f", "webm", str(audio_temp)],
                               check=True, capture_output=True, timeout=180)
                audio_temp.replace(narration_path)
        finalized.replace(media_path)
    except (subprocess.SubprocessError, OSError, RuntimeError) as error:
        finalized.unlink(missing_ok=True)
        raise RuntimeError("Could not finalize the captured WebM for playback. Install FFmpeg or keep the screenshots without video.") from error
    return media_path


def generate_tts_audio(openai_call, key, model, payload):
    """Call the configured Speech API model and give a local-speech recovery on model rejection."""
    try:
        return openai_call("https://api.openai.com/v1/audio/speech", key, {**payload, "model": model})
    except RuntimeError as error:
        if any(code in str(error) for code in ("(400)", "(404)", "(410)")):
            raise RuntimeError(f"OpenAI rejected TTS model '{model}' on the Speech API. Set STEPFILM_TTS_MODEL to a currently supported Speech API model, or use Stepfilm's local speech for export. Realtime-only models need a separate integration.") from error
        raise


def realtime_tts_events(text, voice, instructions, model):
    """Build the documented Realtime TTS event sequence (separate from Speech API)."""
    supported_voices = {"alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar"}
    voice_aliases = {"fable": "alloy", "nova": "shimmer", "onyx": "cedar"}
    voice = voice_aliases.get(voice, voice)
    if voice not in supported_voices:
        voice = "coral"
    return [
        {"type": "session.update", "session": {
            "type": "realtime", "model": model,
            "instructions": "Read the user's text aloud without adding commentary. " + instructions,
            "output_modalities": ["audio"],
            "audio": {"output": {"format": {"type": "audio/pcm", "rate": 24000}, "voice": voice}},
        }},
        {"type": "conversation.item.create", "item": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }},
        {"type": "response.create", "response": {"output_modalities": ["audio"]}},
    ]


def generate_realtime_tts_audio(websocket_connect, ffmpeg, key, model, payload):
    """Generate bounded 24 kHz PCM over Realtime WebSocket and encode it as MP3."""
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to convert Realtime PCM voiceover to MP3. Use local speech for export.")
    text, voice = payload.get("input", ""), payload.get("voice", "coral")
    instructions = payload.get("instructions", "")
    events = realtime_tts_events(text, voice, instructions, model)
    url = "wss://api.openai.com/v1/realtime?model=" + model
    audio = bytearray()
    deadline = time() + 90
    try:
        with websocket_connect(url, additional_headers={"Authorization": f"Bearer {key}"},
                               open_timeout=10, close_timeout=5, max_size=1024 * 1024) as ws:
            def receive(expected):
                remaining = deadline - time()
                if remaining <= 0:
                    raise RuntimeError("Realtime voice generation timed out. Try again or use local speech.")
                event = json.loads(ws.recv(timeout=min(60, remaining)))
                if event.get("type") == "error":
                    detail = event.get("error", {}).get("message", "Realtime request failed")
                    raise RuntimeError(str(detail)[:500])
                if event.get("type") != expected:
                    raise RuntimeError(f"Unexpected Realtime event: {event.get('type', 'unknown')}")
                return event
            receive("session.created")
            ws.send(json.dumps(events[0]))
            receive("session.updated")
            ws.send(json.dumps(events[1]))
            ws.send(json.dumps(events[2]))
            while True:
                remaining = deadline - time()
                if remaining <= 0:
                    raise RuntimeError("Realtime voice generation timed out. Try again or use local speech.")
                event = json.loads(ws.recv(timeout=min(60, remaining)))
                if event.get("type") == "error":
                    detail = event.get("error", {}).get("message", "Realtime request failed")
                    raise RuntimeError(str(detail)[:500])
                if event.get("type") == "response.output_audio.delta":
                    chunk = base64.b64decode(event.get("delta", ""), validate=True)
                    if len(audio) + len(chunk) > 12 * 1024 * 1024:
                        raise RuntimeError("Realtime voiceover exceeded the 12 MB limit. Shorten the narration and try again.")
                    audio.extend(chunk)
                elif event.get("type") == "response.done":
                    response = event.get("response", {})
                    if response.get("status") not in (None, "completed"):
                        raise RuntimeError("Realtime voice generation did not complete. Try again or use local speech.")
                    break
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError("Realtime voice generation failed. Check the API connection and try again, or use local speech.") from error
    if not audio:
        raise RuntimeError("Realtime returned no audio. Try again or use local speech.")
    try:
        converted = subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0", "-f", "mp3", "pipe:1"],
                                   input=bytes(audio), capture_output=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Could not encode Realtime voiceover audio. Use local speech for export.") from error
    if not converted.stdout:
        raise RuntimeError("FFmpeg returned an empty Realtime voiceover. Use local speech for export.")
    return converted.stdout


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(30)
        super().setup()

    def send_media_headers(self, status, kind, length):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Stepfilm-Server", "capture-handoff-v2")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'")
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def respond(self, status, kind, body):
        self.send_media_headers(status, kind, len(body))
        self.wfile.write(body)

    def json(self, status, body):
        self.respond(status, "application/json", json.dumps(body).encode())

    def local(self):
        return self.headers.get("Host") == self.server.origin.removeprefix("http://")

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path or "/"
        if not self.local():
            self.send_error(403)
        elif path == "/auth/callback":
            query = parse_qs(parsed.query)
            try:
                if query.get("error", [""])[0]:
                    self.server.chatgpt_plan.cancel(query.get("state", [""])[0])
                    message = "Sign-in was cancelled. Return to Stepfilm and try again if you want to connect."
                else:
                    result = self.server.chatgpt_plan.finish(query.get("code", [""])[0], query.get("state", [""])[0], query.get("client_id", [None])[0])
                    message = "ChatGPT plan connected" + (f" for {result['email']}" if result.get("email") else "") + ". You can close this tab and return to Stepfilm."
                status = 200
            except (RuntimeError, ValueError) as error:
                message, status = str(error), 400
            html = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Stepfilm ChatGPT sign-in</title>"
                    "<h1>Stepfilm</h1><p>" + xml_escape(message) + "</p><p>You may close this tab.</p>"
                    "<script src='/chatgpt-callback.js' defer></script>").encode()
            self.respond(status, "text/html; charset=utf-8", html)
        elif path == "/" and parsed.query == "capture=latest":
            # Capture stop opens this URL. Always serve the editor shell; the
            # editor then reads the captured project from the API endpoint.
            name, kind = STATIC["/"]
            self.respond(200, f"{kind}; charset=utf-8", (APP / name).read_bytes())
        elif path == "/api/status":
            voice = self.server.renderer.engine if self.server.renderer else "Unavailable — install FFmpeg"
            plan_status = self.server.chatgpt_plan.status()
            self.json(200, {"voice": voice, "aiConfigured": bool(self.server.openai_api_key or plan_status["connected"]),
                            "apiKeyConfigured": bool(self.server.openai_api_key), "chatgptPlan": plan_status})
        elif path == "/api/capture/latest":
            if self.server.capture_latest is None:
                self.json(404, {"error": "No captured workflow is waiting in Stepfilm."})
            else:
                self.json(200, self.server.capture_latest)
        elif path == "/api/capture/status":
            self.json(200, {"capturing": self.server.capture_steps is not None,
                            "count": len(self.server.capture_steps or []),
                            "title": self.server.capture_title,
                            "captureId": self.server.capture_id,
                            "recordingStartedAt": self.server.capture_started_at,
                            "narration": self.server.capture_narration,
                            "captureMode": self.server.capture_mode,
                            "recording": self.server.capture_recording})
        elif path == "/api/export/captions":
            query = parse_qs(parsed.query)
            format = query.get("format", ["vtt"])[0].lower()
            language = query.get("language", ["original"])[0]
            if format not in {"vtt", "srt"} or (language != "original" and not re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2,4})?", language)):
                self.json(400, {"error": "Choose a supported caption format and language."})
                return
            tracks = getattr(self.server, "last_export_caption_tracks", {})
            if not self.server.last_export.is_file():
                self.json(404, {"error": "Render the guide before downloading MP4-timed captions."})
                return
            try:
                text = format_caption_track(tracks, language, format)
            except ValueError as error:
                self.json(404, {"error": str(error)})
                return
            self.respond(200, "text/vtt" if format == "vtt" else "application/x-subrip", text.encode("utf-8"))
        elif path.startswith("/api/narration/"):
            media_id = path.rsplit("/", 1)[-1]
            audio_path = self.server.capture_media_dir / f"{media_id}.narration.webm"
            if not re.fullmatch(r"[a-f0-9]{32}", media_id) or not audio_path.is_file():
                self.send_error(404)
                return
            self.send_range_file(audio_path, "audio/webm")
        elif path.startswith("/api/media/"):
            media_id = path.rsplit("/", 1)[-1]
            media_path = self.server.capture_media_dir / f"{media_id}.webm"
            if not re.fullmatch(r"[a-f0-9]{32}", media_id) or not media_path.is_file():
                self.send_error(404)
                return
            self.send_range_file(media_path, "video/webm")
        elif path == "/api/export/latest" and self.server.last_export.is_file():
            video = self.server.last_export.read_bytes()
            range_header = self.headers.get("Range", "")
            start, end = 0, len(video) - 1
            status = 200
            if range_header.startswith("bytes="):
                requested = range_header[6:].split(",", 1)[0].strip()
                first, _, last = requested.partition("-")
                if first:
                    start = int(first)
                    end = int(last) if last else end
                elif last:
                    start = max(0, len(video) - int(last))
                if start >= len(video) or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(video)}")
                    self.end_headers()
                    return
                end = min(end, len(video) - 1)
                status = 206
            chunk = video[start:end + 1]
            self.send_response(status)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(video)}")
            self.end_headers()
            self.wfile.write(chunk)
        elif path in STATIC:
            name, kind = STATIC[path]
            self.respond(200, f"{kind}; charset=utf-8", (APP / name).read_bytes())
        else:
            self.send_error(404)

    def send_range_file(self, path, kind):
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range", "")
        if range_header.startswith("bytes="):
            first, _, last = range_header[6:].split(",", 1)[0].partition("-")
            start = int(first) if first else max(0, size - int(last or 0))
            end = int(last) if first and last else end
            if start >= size or start > end:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            end = min(end, size - 1); status = 206
        self.send_response(status); self.send_header("Content-Type", kind); self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes"); self.send_header("Cache-Control", "no-store")
        if status == 206: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as media:
            media.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = media.read(min(1024 * 1024, remaining))
                if not chunk: break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_OPTIONS(self):
        if self.headers.get("Origin", "").startswith("chrome-extension://"):
            self.send_media_headers(204, "text/plain", 0)
        else:
            self.send_error(403)

    def do_POST(self):
        origin = self.headers.get("Origin", "")
        if not self.local() or (origin != self.server.origin and not origin.startswith("chrome-extension://")):
            self.send_error(403)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if urlsplit(self.path).path == "/api/media/import":
                self.import_mobile_video(size)
                return
            if not 0 < size <= MAX_REQUEST:
                raise ValueError("This request is empty or exceeds the 50 MB limit.")
            if self.path.startswith("/api/capture/chunk?"):
                if self.headers.get_content_type() != "application/octet-stream":
                    raise ValueError("Video chunks must use binary upload.")
                query = parse_qs(urlsplit(self.path).query)
                capture_id = query.get("captureId", [""])[0]
                index = int(query.get("index", ["-1"])[0])
                if capture_id != self.server.capture_id or self.server.capture_steps is None or not self.server.capture_recording:
                    raise ValueError("This recording is no longer active. Keep the popup open and retry Stop.")
                if index != self.server.capture_chunk_count:
                    raise ValueError("A video chunk arrived out of order. Stop and restart capture to recover.")
                media_path = self.server.capture_media_dir / f"{capture_id}.webm"
                if media_path.stat().st_size + size > MAX_CAPTURE_VIDEO:
                    raise ValueError("The continuous tab video reached the 250 MB limit. Stop video and keep the screenshot guide, or restart with a shorter capture.")
                if shutil.disk_usage(self.server.capture_media_dir).free < size + 64 * 1024 * 1024:
                    raise ValueError("The computer is low on disk space. Stop video and keep the screenshot guide; the captured screenshots are still available.")
                chunk = self.rfile.read(size)
                with media_path.open("ab") as video:
                    video.write(chunk)
                self.server.capture_chunk_count += 1
                self.json(200, {"chunks": self.server.capture_chunk_count})
                return
            if self.path == "/api/import" and self.headers.get_content_type() == "text/plain":
                self.json(200, parse_guide(self.rfile.read(size).decode("utf-8-sig")))
                return
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Unsupported request format.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Invalid project data.")
            if self.path == "/api/ai/configure":
                key = body.get("apiKey", "")
                if not isinstance(key, str) or (key and not key.startswith("sk-")):
                    raise ValueError("Enter a valid OpenAI API key, or leave it blank to disconnect.")
                self.server.openai_api_key = key.strip()
                self.json(200, {"configured": bool(self.server.openai_api_key)})
                return
            if self.path == "/api/chatgpt/start":
                redirect_uri = f"{self.server.origin}/auth/callback"
                url = self.server.chatgpt_plan.start(redirect_uri)
                self.json(200, {"authorizationUrl": url})
                return
            if self.path == "/api/chatgpt/disconnect":
                self.server.chatgpt_plan.disconnect()
                self.json(200, {"connected": False})
                return
            if self.path == "/api/ai/draft":
                steps = body.get("steps")
                if not isinstance(steps, list) or not 1 <= len(steps) <= 50: raise ValueError("The guide needs 1–50 steps to draft.")
                from render import image_bytes
                drafts = []
                for step in steps:
                    if step.get("kind") == "video":
                        drafts.append({"skipped": True, "reason": "Video analysis is not available yet."})
                        continue
                    kind, raw = image_bytes(step.get("sourceImage") or step.get("image"))
                    mime = "image/jpeg" if kind == "jpeg" else f"image/{kind}"
                    prompt = {"model": "gpt-4.1-mini", "response_format": {"type": "json_object"}, "messages": [
                      {"role": "system", "content": "Write concise, accurate browser tutorial content from screenshots. Never invent actions or claim success not visible. Return JSON with title, description (one-sentence summary <=200 chars), instruction (imperative, <=180 chars), callout (<=80 chars; empty if no clear target), and target (normalized [x,y] center coordinates 0..1; use [0.5,0.5] if no target)."},
                      {"role": "user", "content": [{"type": "text", "text": "Draft this tutorial step using visible UI labels. Existing title: " + str(body.get("title", ""))}, {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode()}", "detail": "high"}}]}
                    ], "max_tokens": 350}
                    drafts.append(ai_json_response(self.server, prompt))
                normalized = drafts
                title = next((s.get("title") for s in normalized if s.get("title")), body.get("title", "Untitled guide"))
                self.json(200, {"title": title, "steps": normalized})
                return
            if self.path == "/api/ai/analyze-video":
                video_id = body.get("videoId", "")
                if not re.fullmatch(r"[a-f0-9]{32}", video_id): raise ValueError("Select a valid captured video step.")
                video_path = self.server.capture_media_dir / f"{video_id}.webm"
                if not video_path.is_file(): raise ValueError("This video clip is unavailable on this computer.")
                ffmpeg, ffprobe = self.server.ffmpeg, shutil.which("ffprobe")
                if not ffmpeg or not ffprobe: raise RuntimeError("FFmpeg and ffprobe are required to analyze a video step.")
                duration_text = subprocess.check_output([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)], text=True, timeout=20).strip()
                try: duration = float(duration_text)
                except ValueError as error: raise ValueError("Could not read this video's duration.") from error
                if duration <= 0: raise ValueError("This video has no readable frames.")
                scan_times = video_analysis_sample_times(duration, max_frames=48)
                sample_times = []
                content = [{"type": "text", "text": f"Analyze this browser tutorial clip from {duration:.1f} seconds. The supplied still frames are adaptively selected visual samples, listed in chronological order with timestamps. Reconstruct only actions supported by these samples; preserve their chronological order and assign each event the timestamp of the closest supplied frame that supports it. These stills can miss brief actions and do not prove an action completed. Do not invent intermediate actions or claim completion when evidence is ambiguous. Return JSON with title (<=100 chars), description (one sentence <=200 chars), instruction (imperative <=180 chars), and events (up to 12 objects with time in seconds and concise visible action)."}]
                sampled_at = []
                image_bytes = 0
                with tempfile.TemporaryDirectory() as temp:
                    # Inspect a tiny grayscale contact sheet to place samples
                    # around visual transitions before extracting model frames.
                    scan_frames = []
                    for index, timestamp in enumerate(scan_times):
                        frame = Path(temp) / f"scan-{index}.gray"
                        try:
                            subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-vf", "scale=32:18:flags=area,format=gray", "-f", "rawvideo", str(frame)], check=True, capture_output=True, timeout=20)
                            scan_frames.append(frame.read_bytes() if frame.is_file() else b"")
                        except (subprocess.SubprocessError, OSError):
                            scan_frames.append(b"")
                    sample_times = select_video_analysis_times(scan_times, scan_frames)
                    for index, timestamp in enumerate(sample_times):
                        frame = Path(temp) / f"frame-{index}.jpg"
                        subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-vf", "scale=w='min(640,iw)':h=-2", "-q:v", "7", str(frame)], check=True, capture_output=True, timeout=20)
                        if not frame.is_file() or not frame.stat().st_size: continue
                        frame_data = frame.read_bytes()
                        if image_bytes + len(frame_data) > VIDEO_ANALYSIS_MAX_IMAGE_BYTES:
                            continue
                        image_bytes += len(frame_data)
                        encoded = base64.b64encode(frame_data).decode("ascii")
                        sampled_at.append(timestamp)
                        content.append({"type": "text", "text": f"Frame at {timestamp:.1f} seconds"})
                        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}", "detail": "high"}})
                if len(sampled_at) < 2: raise RuntimeError("Could not extract enough frames within the analysis size limit.")
                prompt = {"model": "gpt-4.1-mini", "response_format": {"type": "json_object"}, "messages": [
                    {"role": "system", "content": "You create accessible software tutorials from visual evidence. Never invent labels or hidden actions. Return valid JSON only."},
                    {"role": "user", "content": content}], "max_tokens": 900}
                draft = ai_json_response(self.server, prompt)
                events = map_video_analysis_events(draft.get("events"), sampled_at, duration)
                self.json(200, {"title": str(draft.get("title", ""))[:100], "description": str(draft.get("description", ""))[:200],
                                "instruction": str(draft.get("instruction", ""))[:180], "events": events, "duration": duration,
                                "sampledFrames": len(sampled_at), "sampledAt": sampled_at, "sampling": "adaptive-visual-change"})
                return
            if self.path == "/api/ai/translate-captions":
                language = body.get("language", "")
                languages = {"de": "German", "es": "Spanish", "fr": "French", "it": "Italian", "ja": "Japanese", "ko": "Korean", "pt": "Portuguese", "zh": "Chinese"}
                if language not in languages: raise ValueError("Choose one of the supported caption languages.")
                captions = body.get("captions")
                if not isinstance(captions, list) or not 1 <= len(captions) <= 500 or any(not isinstance(text, str) or not text.strip() or len(text) > 500 for text in captions):
                    raise ValueError("Translate 1–500 non-empty captions, each no longer than 500 characters.")
                prompt = {"model": "gpt-4.1-mini", "response_format": {"type": "json_object"}, "messages": [
                    {"role": "system", "content": f"Translate each caption into {languages[language]}. Preserve meaning, names, product UI labels, and punctuation where natural. Return JSON with a translations array containing exactly one translated string for each input, in the same order. Do not merge or split captions."},
                    {"role": "user", "content": json.dumps({"captions": captions}, ensure_ascii=False)}
                ], "temperature": 0, "max_tokens": min(8000, max(500, len(captions) * 40))}
                translated = ai_json_response(self.server, prompt)
                translations = translated.get("translations") if isinstance(translated, dict) else None
                if not isinstance(translations, list) or len(translations) != len(captions) or any(not isinstance(text, str) or not text.strip() for text in translations):
                    raise RuntimeError("The translation service returned an incomplete caption list. Your original captions are unchanged; retry the translation.")
                self.json(200, {"language": language, "translations": [text.strip()[:1000] for text in translations]})
                return
            if self.path == "/api/ai/voice":
                key = self.server.openai_api_key
                if not key: raise RuntimeError("ChatGPT plan access covers text and image tools, not AI voice generation. Connect an API key or use local speech for this step.")
                text, voice = body.get("text", ""), body.get("voice", "coral")
                if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400: raise ValueError("Enter narration of 1–400 characters before generating voiceover.")
                if voice not in {"alloy", "ash", "ballad", "coral", "echo", "fable", "marin", "nova", "onyx", "sage", "cedar", "shimmer", "verse"}: raise ValueError("Choose a supported AI voice.")
                pace = body.get("pace", "natural")
                if pace not in {"natural", "slower", "brisk"}: raise ValueError("Choose a supported speaking pace.")
                direction = body.get("instructions", "")
                if not isinstance(direction, str) or len(direction) > 300: raise ValueError("Keep voice direction within 300 characters.")
                pace_directions = {"natural": "Use a natural, moderate speaking pace.", "slower": "Speak slowly and deliberately, with short pauses between ideas.", "brisk": "Speak at a brisk but clearly understandable pace."}
                instructions = "Speak clearly as a friendly software tutorial narrator. " + pace_directions[pace]
                if direction.strip(): instructions += " Additional direction: " + direction.strip()
                tts_payload = {"voice": voice, "input": text, "instructions": instructions}
                if self.server.openai_tts_provider == "realtime":
                    try:
                        from websockets.sync.client import connect
                    except ImportError as error:
                        raise RuntimeError("Realtime voiceover requires the bounded websockets dependency. Install requirements.txt, or use local speech for export.") from error
                    audio = generate_realtime_tts_audio(connect, self.server.ffmpeg, key, self.server.openai_tts_model, tts_payload)
                else:
                    audio = generate_tts_audio(self.openai_call, key, self.server.openai_tts_model, tts_payload)
                self.respond(200, "audio/mpeg", audio)
                return
            if self.path == "/api/ai/transcribe":
                key = self.server.openai_api_key
                if not key: raise RuntimeError("ChatGPT plan access does not include the audio transcription endpoint. Connect an API key, or add captions manually and use local speech.")
                filename, payload = body.get("filename", "recording.webm"), body.get("audio", "")
                if not isinstance(filename, str) or not isinstance(payload, str) or not payload.startswith("data:"): raise ValueError("Choose an audio or video recording to transcribe.")
                data = base64.b64decode(payload.split(",", 1)[1], validate=True)
                if len(data) > 25 * 1024 * 1024: raise ValueError("Transcription files must be 25 MB or smaller.")
                boundary = "----Stepfilm" + uuid4().hex
                suffix = Path(filename).suffix.lower()
                mime = {".webm": "audio/webm", ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".m4a": "audio/mp4", ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac", ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg"}.get(suffix, "application/octet-stream")
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:120] or "recording.bin"
                form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\ngpt-transcribe\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{safe_name}\"\r\nContent-Type: {mime}\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode())
                transcript = self.openai_call("https://api.openai.com/v1/audio/transcriptions", key, form, f"multipart/form-data; boundary={boundary}")
                self.json(200, {"text": json.loads(transcript).get("text", "")})
                return
            if self.path == "/api/ai/transcribe-capture":
                key = self.server.openai_api_key
                if not key: raise RuntimeError("ChatGPT plan access does not include the audio transcription endpoint. Connect an API key, or add captions manually.")
                capture_id = body.get("captureId", "")
                audio_path = self.server.capture_media_dir / f"{capture_id}.narration.webm"
                if not re.fullmatch(r"[a-f0-9]{32}", capture_id) or not audio_path.is_file():
                    raise ValueError("This capture has no saved microphone narration. Enable narration when recording, then try again.")
                if audio_path.stat().st_size > 25 * 1024 * 1024:
                    raise ValueError("Captured narration exceeds the 25 MB transcription limit. Shorten the recording and retry.")
                boundary = "----Stepfilm" + uuid4().hex
                form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"timestamp_granularities[]\"\r\n\r\nsegment\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"captured-narration.webm\"\r\nContent-Type: audio/webm\r\n\r\n".encode() + audio_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode())
                raw_steps = body.get("steps")
                duration = body.get("duration")
                if not isinstance(raw_steps, list) or not raw_steps or not isinstance(duration, (int, float)) or duration <= 0:
                    raise ValueError("Capture timing is missing. Reopen the captured guide or recapture it.")
                step_indices = sorted((i for i, step in enumerate(raw_steps) if step.get("kind") != "video"), key=lambda i: max(0.0, float(raw_steps[i].get("at", 0))))
                if not step_indices:
                    step_indices = [0]
                boundaries = [0.0]
                boundaries.extend(max(boundaries[-1], min(float(duration), float(raw_steps[index].get("at", 0)))) for index in step_indices[1:])
                boundaries.append(float(duration))
                try:
                    transcript = json.loads(self.openai_call("https://api.openai.com/v1/audio/transcriptions", key, form, f"multipart/form-data; boundary={boundary}"))
                    raw_segments = transcript.get("segments", [])
                    transcript_text = str(transcript.get("text", ""))
                except RuntimeError as error:
                    # Whisper provides timestamped sentences while available. The maintained model
                    # fallback transcribes pause-split, click-bounded audio chunks.
                    if not any(code in str(error) for code in ("(400)", "(404)", "(410)")): raise
                    transcript_text, raw_segments = self.transcribe_click_bounded_audio(key, audio_path, step_indices, boundaries)
                per_step = [[] for _ in raw_steps]
                ranges = [[0.0, 0.0] for _ in raw_steps]
                for slot, step_index in enumerate(step_indices): ranges[step_index] = [boundaries[slot], boundaries[slot + 1]]
                if all(step.get("kind") == "video" for step in raw_steps): ranges[0] = [0.0, float(duration)]
                segments = []
                for segment in raw_segments:
                    start, end = max(0.0, float(segment.get("start", 0))), max(0.0, float(segment.get("end", 0)))
                    text = str(segment.get("text", "")).strip()
                    if not text: continue
                    index = segment.get("stepIndex")
                    if index is None:
                        midpoint = (start + end) / 2
                        slot = next((i for i in range(len(step_indices)) if boundaries[i] <= midpoint < boundaries[i + 1]), len(step_indices) - 1)
                        index = step_indices[slot]
                    caption = {"start": start, "end": end, "text": text}
                    per_step[index].append(caption)
                    segments.append({**caption, "stepIndex": index})
                self.json(200, {"text": transcript_text, "segments": segments,
                                "steps": [{"text": " ".join(caption["text"] for caption in captions), "captions": captions,
                                           "captureStart": ranges[i][0], "captureEnd": ranges[i][1],
                                           "audioSource": "recorded" if (raw_steps[i].get("kind") != "video" or (len(step_indices) == 1 and raw_steps[i].get("kind") == "video")) else "synthetic"}
                                          for i, captions in enumerate(per_step)], "duration": float(duration)})
                return
            if self.path == "/api/capture/start":
                title = body.get("title", "Captured workflow")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("Give the capture a title.")
                self.server.capture_steps = []
                self.server.capture_title = title.strip()[:150]
                self.server.capture_id = uuid4().hex
                self.server.capture_started_at = int(time() * 1000)
                self.server.capture_narration = body.get("narration") is True
                self.server.capture_mode = "desktop" if body.get("captureMode") == "desktop" else "tab"
                self.server.capture_duration = 0.0
                self.server.capture_recording = body.get("recording") is True
                self.server.capture_requires_video = self.server.capture_recording
                self.server.capture_chunk_count = 0
                (self.server.capture_media_dir / f"{self.server.capture_id}.webm").write_bytes(b"")
                self.json(200, {"count": 0, "captureId": self.server.capture_id, "recordingStartedAt": self.server.capture_started_at})
                return
            if self.path == "/api/capture/cancel":
                if body.get("captureId") == self.server.capture_id:
                    (self.server.capture_media_dir / f"{self.server.capture_id}.webm").unlink(missing_ok=True)
                    (self.server.capture_media_dir / f"{self.server.capture_id}.narration.webm").unlink(missing_ok=True)
                    self.server.capture_steps = None
                    self.server.capture_recording = False
                    self.server.capture_narration = False
                    self.server.capture_mode = "tab"
                self.json(200, {"cancelled": True})
                return
            if self.path == "/api/capture/recording/started":
                started_at = body.get("startedAt")
                if body.get("captureId") != self.server.capture_id or self.server.capture_steps is None or not isinstance(started_at, (int, float)):
                    raise ValueError("This capture is no longer active.")
                self.server.capture_started_at = int(started_at)
                self.json(200, {"started": True})
                return
            if self.path == "/api/capture/recording/finish":
                if body.get("captureId") != self.server.capture_id or self.server.capture_steps is None:
                    raise ValueError("This capture is no longer active.")
                if body.get("chunks") != self.server.capture_chunk_count or not self.server.capture_chunk_count:
                    raise ValueError("The tab recording has no complete video data. Keep the popup open and retry Stop.")
                if not self.server.ffmpeg:
                    raise RuntimeError("FFmpeg is required to finalize the captured tab video for playback. Keep the screenshots without video or install FFmpeg.")
                duration = body.get("duration")
                self.server.capture_duration = max(0.0, float(duration)) if isinstance(duration, (int, float)) else max(0.0, (int(time() * 1000) - self.server.capture_started_at) / 1000)
                narration_path = self.server.capture_media_dir / f"{self.server.capture_id}.narration.webm" if self.server.capture_narration else None
                finalize_webm(self.server.ffmpeg, self.server.capture_media_dir / f"{self.server.capture_id}.webm", narration_path)
                self.server.capture_recording = False
                self.server.capture_clip_ready = True
                self.json(200, {"clipId": self.server.capture_id, "chunks": self.server.capture_chunk_count})
                return
            if self.path == "/api/capture/recording/abandon":
                if body.get("captureId") != self.server.capture_id or self.server.capture_steps is None:
                    raise ValueError("This capture is no longer active.")
                self.server.capture_recording = False
                self.server.capture_requires_video = False
                self.server.capture_clip_ready = False
                (self.server.capture_media_dir / f"{self.server.capture_id}.webm").unlink(missing_ok=True)
                (self.server.capture_media_dir / f"{self.server.capture_id}.narration.webm").unlink(missing_ok=True)
                self.json(200, {"screenshotsPreserved": True})
                return
            if self.path == "/api/capture/step":
                if self.server.capture_steps is None:
                    raise ValueError("Start a capture before adding steps.")
                if len(self.server.capture_steps) >= 50:
                    raise ValueError("A guide can have up to 50 steps.")
                image = body.get("image")
                from render import image_bytes
                image_bytes(image)
                if (body.get("captureId") and body.get("captureId") != self.server.capture_id) or (self.server.capture_requires_video and body.get("captureId") != self.server.capture_id):
                    raise ValueError("The screenshot does not belong to the active recording.")
                captured_at = body.get("at", int(time() * 1000))
                if not isinstance(captured_at, (int, float)):
                    raise ValueError("Screenshot timing is invalid.")
                offset = max(0.0, (float(captured_at) - self.server.capture_started_at) / 1000)
                if self.server.capture_steps:
                    offset = max(offset, float(self.server.capture_steps[-1].get("at", 0)))
                self.server.capture_steps.append({"image": image, "text": "", "at": offset})
                self.json(200, {"count": len(self.server.capture_steps)})
                return
            if self.path == "/api/capture/stop":
                if self.server.capture_steps is None:
                    raise ValueError("There is no active capture to stop.")
                if (body.get("captureId") and body.get("captureId") != self.server.capture_id) or (self.server.capture_requires_video and not self.server.capture_clip_ready):
                    raise ValueError("Finish the continuous tab video before stopping the guide.")
                if not self.server.capture_steps and self.server.capture_clip_ready:
                    self.server.capture_steps.append({"kind": "video", "videoId": self.server.capture_id, "text": ""})
                if not self.server.capture_steps:
                    raise ValueError("Capture a screenshot or finish a tab video before stopping.")
                clips = ([{"id": self.server.capture_id, "url": f"/api/media/{self.server.capture_id}", "label": "Continuous tab recording"}]
                         if self.server.capture_clip_ready else [])
                self.server.capture_latest = {"title": self.server.capture_title, "steps": self.server.capture_steps, "capturedClips": clips,
                                              "captureMode": self.server.capture_mode,
                                              "narrationAudioId": self.server.capture_id if (self.server.capture_media_dir / f"{self.server.capture_id}.narration.webm").is_file() else "",
                                              "narrationAudioUrl": f"/api/narration/{self.server.capture_id}" if (self.server.capture_media_dir / f"{self.server.capture_id}.narration.webm").is_file() else "",
                                              "captureDuration": self.server.capture_duration}
                self.server.capture_steps = None
                self.server.capture_recording = False
                self.server.capture_clip_ready = False
                self.server.capture_requires_video = False
                self.server.capture_narration = False
                self.server.capture_mode = "tab"
                self.json(200, self.server.capture_latest)
                return
            if self.path == "/api/export/share":
                html = body.get("html", "")
                media_steps = body.get("mediaSteps", [])
                title = body.get("title", "stepfilm-guide")
                if not isinstance(title, str): title = "stepfilm-guide"
                filename = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-.")[:80] or "stepfilm-guide"
                with tempfile.TemporaryDirectory() as temp:
                    bundle = create_share_bundle(html, media_steps, self.server.capture_media_dir, Path(temp) / f"{filename}.zip")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(bundle.stat().st_size))
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}.zip"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    with bundle.open("rb") as archive:
                        shutil.copyfileobj(archive, self.wfile)
                return
            if self.path == "/api/export/scorm":
                html = body.get("html", "")
                media_steps = body.get("mediaSteps", [])
                title = body.get("title", "stepfilm-course")
                if not isinstance(title, str): title = "stepfilm-course"
                filename = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-.")[:80] or "stepfilm-course"
                with tempfile.TemporaryDirectory() as temp:
                    bundle = create_scorm12_bundle(html, media_steps, self.server.capture_media_dir, Path(temp) / f"{filename}-scorm12.zip", title)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(bundle.stat().st_size))
                    self.send_header("Content-Disposition", f'attachment; filename="{filename}-scorm12.zip"')
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    with bundle.open("rb") as archive:
                        shutil.copyfileobj(archive, self.wfile)
                return
            if self.server.renderer is None:
                raise RuntimeError("Rendering is unavailable. Install FFmpeg and make sure ffmpeg and ffprobe are on your PATH.")
            if self.path == "/api/preview":
                self.respond(200, "audio/wav", self.server.renderer.preview(body.get("text")))
            elif self.path == "/api/export":
                if not EXPORT_LOCK.acquire(blocking=False):
                    self.json(409, {"error": "A video is already exporting. Try again when it finishes."})
                    return
                try:
                    with tempfile.TemporaryDirectory() as temp:
                        steps = body.get("steps")
                        prepare_export_steps(steps, body.get("narrationAudioId", ""), self.server.capture_media_dir)
                        brand = body.get("brand") if isinstance(body.get("brand"), dict) else {}
                        brand = {**brand, "title": str(body.get("title", ""))[:150]}
                        path = self.server.renderer.video(steps, Path(temp), brand)
                        self.server.last_export_caption_tracks = getattr(self.server.renderer, "last_export_caption_tracks", {})
                        shutil.copyfile(path, self.server.last_export)
                        with path.open("rb") as video:
                            self.send_media_headers(200, "video/mp4", path.stat().st_size)
                            shutil.copyfileobj(video, self.wfile)
                finally:
                    EXPORT_LOCK.release()
            else:
                self.send_error(404)
        except ValueError as error:
            self.json(400, {"error": str(error)})
        except RuntimeError as error:
            self.json(503, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError):
            return
        except (subprocess.SubprocessError, OSError):
            self.json(500, {"error": "The local renderer failed. Check your FFmpeg build, voice setup, and screenshot files."})

    def import_mobile_video(self, size):
        self.request.settimeout(300)
        content_type = self.headers.get_content_type()
        if not content_type.startswith("video/"):
            raise ValueError("Choose a video recording from your phone (MP4, MOV, WebM, or another supported video format).")
        if not 0 < size <= MAX_CAPTURE_VIDEO:
            raise ValueError("Mobile video imports must be between 1 byte and 250 MB.")
        ffmpeg, ffprobe = self.server.ffmpeg, shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RuntimeError("FFmpeg and ffprobe are required to import and prepare a mobile video.")
        if shutil.disk_usage(self.server.capture_media_dir).free < size + 128 * 1024 * 1024:
            raise RuntimeError("There is not enough free disk space to import this mobile video. Free space and retry.")
        media_id = uuid4().hex
        upload = self.server.capture_media_dir / f".{media_id}.upload"
        converted = self.server.capture_media_dir / f".{media_id}.converted.webm"
        audio_temp = self.server.capture_media_dir / f".{media_id}.narration.webm"
        final_video = self.server.capture_media_dir / f"{media_id}.webm"
        final_audio = self.server.capture_media_dir / f"{media_id}.narration.webm"
        try:
            remaining = size
            with upload.open("wb") as target:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("The mobile video upload ended early. Retry the import.")
                    target.write(chunk)
                    remaining -= len(chunk)
            try:
                subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(upload),
                                "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,pad=1280:720:(ow-iw)/2:(oh-ih)/2:color=0x101827,setsar=1",
                                "-c:v", "libvpx-vp9", "-deadline", "good", "-cpu-used", "4", "-crf", "34", "-b:v", "0",
                                "-c:a", "libopus", "-b:a", "96k", "-f", "webm", str(converted)], check=True, capture_output=True, timeout=300)
            except subprocess.CalledProcessError as error:
                raise ValueError("Could not read this mobile video. Try a common MP4, MOV, or WebM recording.") from error
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("Preparing this mobile video took too long. Try a shorter recording or a smaller file.") from error
            metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(converted)], text=True, timeout=30))
            streams = metadata.get("streams", [])
            if not any(stream.get("codec_type") == "video" for stream in streams):
                raise ValueError("The selected file does not contain a readable video stream.")
            has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
            if has_audio:
                subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(converted),
                                "-map", "0:a:0", "-vn", "-c:a", "copy", "-f", "webm", str(audio_temp)], check=True, capture_output=True, timeout=120)
            if converted.stat().st_size > MAX_CAPTURE_VIDEO:
                raise ValueError("The prepared mobile video exceeds Stepfilm’s 250 MB media limit. Choose a shorter or lower-resolution clip.")
            converted.replace(final_video)
            if has_audio:
                audio_temp.replace(final_audio)
            query = parse_qs(urlsplit(self.path).query)
            label = Path(query.get("filename", ["Mobile recording"])[0]).name
            label = re.sub(r"[^A-Za-z0-9._ -]", "", label).strip()[:100] or "Mobile recording"
            duration = float(metadata.get("format", {}).get("duration", 0) or 0)
            self.json(201, {"id": media_id, "url": f"/api/media/{media_id}", "label": label,
                            "duration": max(0.0, duration), "hasAudio": has_audio, "audioId": media_id if has_audio else ""})
        except Exception:
            upload.unlink(missing_ok=True)
            converted.unlink(missing_ok=True)
            audio_temp.unlink(missing_ok=True)
            final_video.unlink(missing_ok=True)
            final_audio.unlink(missing_ok=True)
            raise
        finally:
            upload.unlink(missing_ok=True)
            converted.unlink(missing_ok=True)
            audio_temp.unlink(missing_ok=True)

    def openai_call(self, url, key, payload, content_type="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        request = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {key}", "Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=180) as response: return response.read()
        except urllib.error.HTTPError as error: raise RuntimeError(f"AI request failed ({error.code}). Check the key and account access.") from error
        except urllib.error.URLError as error: raise RuntimeError("Could not reach the AI service. Check your internet connection.") from error

    def transcribe_click_bounded_audio(self, key, audio_path, step_indices, boundaries):
        if not self.server.ffmpeg: raise RuntimeError("FFmpeg is required to split recorded narration by screenshot steps.")
        segments = []
        with tempfile.TemporaryDirectory() as temp:
            for slot, step_index in enumerate(step_indices):
                start, end = boundaries[slot], boundaries[slot + 1]
                if end - start < 0.15: continue
                step_clip = Path(temp) / f"step-{slot:02}.mp3"
                subprocess.run([self.server.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(start), "-to", str(end), "-i", str(audio_path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(step_clip)], check=True, capture_output=True, timeout=60)
                if not step_clip.is_file() or not step_clip.stat().st_size: continue
                # The maintained transcription model returns text without timestamps. Split at
                # detected pauses to create useful phrase-level cue ranges; cap long unbroken
                # speech into smaller chunks so a single step does not become one giant caption.
                detected = subprocess.run([self.server.ffmpeg, "-nostdin", "-hide_banner", "-i", str(step_clip), "-af", "silencedetect=noise=-32dB:d=0.35", "-f", "null", "-"], capture_output=True, text=True, timeout=60)
                silence_starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", detected.stderr or "")]
                silence_ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", detected.stderr or "")]
                pauses = list(zip(silence_starts, silence_ends))
                cue_ranges = build_caption_chunk_ranges(end - start, pauses)
                # Whisper is the only currently documented transcription model that
                # returns word timestamps. Ask for them on these short fallback chunks;
                # group timed words into readable cues rather than spreading a phrase
                # over the entire chunk. If unavailable, keep the chunk-span estimate.
                for cue_index, (local_start, local_end) in enumerate(cue_ranges):
                    clip = Path(temp) / f"step-{slot:02}-phrase-{cue_index:03}.mp3"
                    subprocess.run([self.server.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(local_start), "-to", str(local_end), "-i", str(step_clip), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(clip)], check=True, capture_output=True, timeout=60)
                    if not clip.is_file() or not clip.stat().st_size: continue
                    boundary = "----Stepfilm" + uuid4().hex
                    audio_bytes = clip.read_bytes()
                    transcription_url = "https://api.openai.com/v1/audio/transcriptions"
                    form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"timestamp_granularities[]\"\r\n\r\nword\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{clip.name}\"\r\nContent-Type: audio/mpeg\r\n\r\n".encode() + audio_bytes + f"\r\n--{boundary}--\r\n".encode())
                    try:
                        response = json.loads(self.openai_call(transcription_url, key, form, f"multipart/form-data; boundary={boundary}"))
                    except RuntimeError as error:
                        if not any(code in str(error) for code in ("(400)", "(404)", "(410)")): raise
                        # Preserve transcription if Whisper timestamping is unavailable.
                        fallback_boundary = "----Stepfilm" + uuid4().hex
                        fallback_form = (f"--{fallback_boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\ngpt-transcribe\r\n--{fallback_boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{clip.name}\"\r\nContent-Type: audio/mpeg\r\n\r\n".encode() + audio_bytes + f"\r\n--{fallback_boundary}--\r\n".encode())
                        response = json.loads(self.openai_call(transcription_url, key, fallback_form, f"multipart/form-data; boundary={fallback_boundary}"))
                    text = str(response.get("text", "")).strip()
                    words = response.get("words")
                    timed_words = offset_caption_words(words, start, local_start, local_end)
                    segments.extend({**item, "stepIndex": step_index} for item in group_caption_words(timed_words))
                    if text and not timed_words:
                        segments.append({"start": start + local_start, "end": start + local_end, "text": text, "stepIndex": step_index})
        return " ".join(item["text"] for item in segments), segments


def main():
    parser = argparse.ArgumentParser(description="Run the Stepfilm local editor.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--piper-model", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        try:
            renderer = Renderer(args.piper_model.resolve() if args.piper_model else None)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            renderer = None
            print(f"Rendering is unavailable: {error}", flush=True)
        with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
            server.origin = f"http://127.0.0.1:{server.server_port}"
            server.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
            server.chatgpt_plan = ChatGPTPlan()
            server.openai_tts_provider = os.environ.get("STEPFILM_TTS_PROVIDER", "speech").strip().lower()
            if server.openai_tts_provider not in {"speech", "realtime"}:
                parser.exit(2, "STEPFILM_TTS_PROVIDER must be 'speech' or 'realtime'.\n")
            server.openai_tts_model = os.environ.get("STEPFILM_TTS_MODEL", "gpt-realtime-2.1-mini" if server.openai_tts_provider == "realtime" else "gpt-4o-mini-tts").strip()
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", server.openai_tts_model):
                parser.exit(2, "STEPFILM_TTS_MODEL must be a model ID containing only letters, numbers, dots, underscores, or hyphens.\n")
            server.renderer = renderer
            server.ffmpeg = renderer.ffmpeg if renderer else shutil.which("ffmpeg")
            server.capture_steps = None
            server.capture_title = "Captured workflow"
            server.capture_id = ""
            server.capture_started_at = 0
            server.capture_recording = False
            server.capture_narration = False
            server.capture_mode = "tab"
            server.capture_duration = 0.0
            server.capture_requires_video = False
            server.capture_clip_ready = False
            server.capture_chunk_count = 0
            server.capture_media_dir = Path.home() / ".stepfilm" / "captured-clips"
            server.capture_media_dir.mkdir(parents=True, exist_ok=True)
            server.capture_latest = None
            server.last_export = Path(tempfile.gettempdir()) / "stepfilm-last.mp4"
            server.last_export_caption_tracks = {}
            voice = renderer.engine if renderer else "Unavailable — install FFmpeg"
            print(f"Stepfilm is open at {server.origin} ({voice}).", flush=True)
            if not args.no_browser:
                webbrowser.open(server.origin)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Stepfilm could not start: {error}\n")


if __name__ == "__main__":
    main()
