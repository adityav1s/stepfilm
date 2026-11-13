from email.message import Message
import json
import shutil
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from render import Renderer
from server import Handler, prepare_export_steps


ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
if not ffmpeg or not ffprobe:
    print("mobile video import: SKIP (FFmpeg unavailable)")
    raise SystemExit(0)

image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
renderer = Renderer()
with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    source = folder / "phone-recording.mp4"
    subprocess.run([
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=green:s=640x360:r=24",
        "-f", "lavfi", "-i", "sine=frequency=550:duration=2.2", "-t", "2.2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(source),
    ], check=True, capture_output=True, timeout=60)

    handler = object.__new__(Handler)
    handler.headers = Message()
    handler.headers["Content-Type"] = "video/mp4"
    handler.path = "/api/media/import?filename=phone-recording.mp4"
    handler.rfile = BytesIO(source.read_bytes())
    handler.request = SimpleNamespace(settimeout=lambda _timeout: None)
    response = []
    handler.json = lambda status, body: response.append((status, body))
    handler.server = SimpleNamespace(ffmpeg=ffmpeg, capture_media_dir=folder)
    Handler.import_mobile_video(handler, source.stat().st_size)

    status, result = response[0]
    media_id = result["id"]
    video = folder / f"{media_id}.webm"
    narration = folder / f"{media_id}.narration.webm"
    assert status == 201, response
    assert result["hasAudio"] and result["audioId"] == media_id, result
    assert result["duration"] >= 2.0, result
    video_info = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(video)], text=True))
    assert any(stream["codec_type"] == "video" and stream["codec_name"] == "vp9" for stream in video_info["streams"]), video_info
    assert any(stream["codec_type"] == "audio" and stream["codec_name"] == "opus" for stream in video_info["streams"]), video_info
    audio_info = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(narration)], text=True))
    assert any(stream["codec_type"] == "audio" for stream in audio_info["streams"]), audio_info

    steps = [{"kind": "video", "videoId": media_id, "recordedAudioId": media_id,
              "audioSource": "recorded", "captureStart": 0, "captureEnd": result["duration"],
              "text": "Imported mobile narration"}]
    prepare_export_steps(steps, "", folder)
    assert steps[0]["media_path"] == str(video) and steps[0]["narration_path"] == str(narration), steps[0]
    exported = renderer.video(steps, folder)
    export_info = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(exported)], text=True, timeout=30))
    assert any(stream["codec_type"] == "video" and stream["codec_name"] == "h264" for stream in export_info["streams"]), export_info
    assert any(stream["codec_type"] == "audio" and stream["codec_name"] == "aac" for stream in export_info["streams"]), export_info
    assert float(export_info["format"]["duration"]) >= 2.0, export_info["format"]
print("mobile video import: PASS (MP4 normalization, audio sidecar, original-audio MP4 render)")
