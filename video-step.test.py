import json
import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

from render import Renderer
from server import finalize_webm, generate_tts_audio


ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
if not ffmpeg or not ffprobe:
    print("video step render: SKIP (FFmpeg unavailable)")
    raise SystemExit(0)

image = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
renderer = Renderer()
with tempfile.TemporaryDirectory() as temp:
    folder = Path(temp)
    clip = folder / "captured.webm"
    narration = folder / "captured.narration.webm"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
        "color=c=blue:s=640x360:r=24", "-f", "lavfi", "-i", "sine=frequency=660:duration=2.2", "-t", "2.2", "-c:v", "libvpx-vp9", "-c:a", "libopus", "-live", "1", "-f", "webm", str(clip),
    ], check=True, capture_output=True, timeout=60)
    raw_metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_format", "-of", "json", str(clip)], text=True))
    assert "duration" not in raw_metadata["format"], raw_metadata
    finalize_webm(ffmpeg, clip, narration)
    finalized_metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_format", "-of", "json", str(clip)], text=True))
    assert float(finalized_metadata["format"]["duration"]) >= 2.0, finalized_metadata
    narration_metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(narration)], text=True))
    assert any(stream["codec_type"] == "audio" for stream in narration_metadata["streams"]), narration_metadata
    recorded = renderer.video([{"image": image, "text": "Recorded explanation", "audioSource": "recorded",
                               "narration_path": str(narration), "narrationStart": 0.2, "narrationEnd": 1.8}], folder)
    recorded_metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(recorded)], text=True, timeout=30))
    assert any(stream["codec_type"] == "audio" and stream["codec_name"] == "aac" for stream in recorded_metadata["streams"]), recorded_metadata
    assert float(recorded_metadata["format"]["duration"]) >= 2.0, recorded_metadata["format"]
    plain_output = folder / "plain.mp4"
    shutil.copyfile(recorded, plain_output)
    captioned = renderer.video([{"image": image, "text": "Recorded explanation", "audioSource": "recorded",
                                "narration_path": str(narration), "narrationStart": 0.2, "narrationEnd": 1.8,
                                "captureStart": 0.2, "captions": [{"start": 0.3, "end": 1.5, "text": "Open Billing"}]}], folder)
    captioned_metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(captioned)], text=True, timeout=30))
    assert any(stream["codec_type"] == "video" and stream["codec_name"] == "h264" for stream in captioned_metadata["streams"]), captioned_metadata
    assert (folder / "caption-000.png").is_file(), "caption overlay image was not generated"
    captioned_copy = folder / "captioned-only.mp4"
    shutil.copyfile(captioned, captioned_copy)
    def lower_frame(path):
        return subprocess.check_output([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.8", "-i", str(path), "-frames:v", "1", "-vf", "crop=1200:160:40:540", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], timeout=30)
    assert lower_frame(captioned) != lower_frame(plain_output), "caption cue was not burned into the video frame"
    translated = renderer.video([{"image": image, "text": "Recorded explanation", "audioSource": "recorded",
                                  "narration_path": str(narration), "narrationStart": 0.2, "narrationEnd": 1.8,
                                  "captureStart": 0.2, "captureEnd": 1.8, "captionLanguage": "fr",
                                  "captions": [{"start": 0.3, "end": 1.5, "text": "Open Billing", "translations": {"fr": "Ouvrez la facturation"}}]}], folder)
    assert lower_frame(translated) != lower_frame(captioned_copy), "selected translated captions were not burned into MP4"
    branded = renderer.video([{"image": image, "text": "Recorded explanation", "audioSource": "recorded",
                               "narration_path": str(narration), "narrationStart": 0.2, "narrationEnd": 1.8,
                               "captureStart": 0.2, "captions": [{"start": 0.3, "end": 1.5, "text": "Open Billing"}]}], folder,
                             {"theme": "bold", "accent": "#ff3366", "titleStyle": "display", "title": "Billing guide"})
    branded_metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(branded)], text=True, timeout=30))
    assert any(stream["codec_type"] == "video" and stream["codec_name"] == "h264" for stream in branded_metadata["streams"]), branded_metadata
    assert (folder / "brand-000.png").is_file() and (folder / "caption-006.png").is_file(), "brand and caption overlays must both be built"
    def top_frame(path):
        return subprocess.check_output([ffmpeg, "-hide_banner", "-loglevel", "error", "-ss", "0.6", "-i", str(path), "-frames:v", "1", "-vf", "crop=1200:170:40:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], timeout=30)
    assert top_frame(branded) != top_frame(captioned_copy), "brand treatment was not burned into the MP4"
    output = renderer.video([
        {"image": image, "text": ""},
        {"kind": "video", "videoId": "0123456789abcdef0123456789abcdef", "media_path": str(clip), "text": ""},
        {"image": image, "text": ""},
    ], folder)
    metadata = json.loads(subprocess.check_output([
        ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output),
    ], text=True, timeout=30))
    videos = [stream for stream in metadata["streams"] if stream["codec_type"] == "video"]
    audios = [stream for stream in metadata["streams"] if stream["codec_type"] == "audio"]
    assert videos and videos[0]["codec_name"] == "h264", metadata
    assert videos[0]["width"] == 1280 and videos[0]["height"] == 720, videos[0]
    assert audios and audios[0]["codec_name"] == "aac", metadata
    duration = float(metadata["format"]["duration"])
    assert duration > 5, duration
    synthetic_voice = folder / "synthetic.mp3"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-q:a", "6", str(synthetic_voice)], check=True, capture_output=True, timeout=30)
    voice_data = "data:audio/mpeg;base64," + base64.b64encode(synthetic_voice.read_bytes()).decode("ascii")
    voiced = renderer.video([{"image": image, "text": "This text has a generated track.", "voiceover": voice_data}], folder)
    voiced_meta = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(voiced)], text=True))
    assert any(stream["codec_type"] == "audio" and stream["codec_name"] == "aac" for stream in voiced_meta["streams"]), voiced_meta
    def reject_tts(_url, _key, _payload, _content_type="application/json"):
        raise RuntimeError("AI request failed (400). Check the key and account access.")
    try:
        generate_tts_audio(reject_tts, "test-key", "retired-tts-model", {"voice": "coral", "input": "Preserve these instructions", "instructions": "Say API as A P I."})
        raise AssertionError("the mocked speech API rejection was not surfaced")
    except RuntimeError as error:
        assert "retired-tts-model" in str(error) and "local speech" in str(error), str(error)
    local_speech_calls = []
    def mocked_local_speech(target_folder, text, output):
        local_speech_calls.append(text)
        subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=520:duration=1.3", output], cwd=target_folder, check=True, capture_output=True, timeout=30)
    original_speech = renderer.speech
    renderer.speech = mocked_local_speech
    local_after_rejection = renderer.video([{"image": image, "text": "Preserve these instructions", "audioSource": "local", "voiceover": voice_data, "voiceInstructions": "Say API as A P I."}], folder)
    renderer.speech = original_speech
    assert local_speech_calls == ["Preserve these instructions"], local_speech_calls
    fallback_meta = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(local_after_rejection)], text=True, timeout=30))
    assert any(stream["codec_type"] == "audio" and stream["codec_name"] == "aac" for stream in fallback_meta["streams"]), fallback_meta
print("video step render: PASS (mixed screenshots + clip, supplied and local voiceover, H.264/AAC 1280x720)")
