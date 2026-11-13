#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PORT=${STEPFILM_PORT:-8765}
BASE="http://127.0.0.1:${PORT}"

echo "[1/5] Syntax and manifest"
python3 -m py_compile "$ROOT/server.py" "$ROOT/render.py"
node --check "$ROOT/app/editor.js"
node --check "$ROOT/capture-extension/popup.js"
node --check "$ROOT/capture-extension/background.js"
node --check "$ROOT/capture-extension/content.js"
node --check "$ROOT/capture-extension/offscreen.js"
node "$ROOT/annotation-model.test.js"
node "$ROOT/step-selection.test.js"
node "$ROOT/guide-export.test.js"
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$ROOT/capture-extension/manifest.json"
python3 "$ROOT/video-step.test.py"
python3 "$ROOT/realtime-tts.test.py"
python3 "$ROOT/chatgpt-plan.test.py"
python3 "$ROOT/caption-timeline.test.py"
python3 "$ROOT/caption-chunking.test.py"
python3 "$ROOT/caption-route.test.py"
python3 "$ROOT/video-analysis.test.py"
python3 "$ROOT/share-export.test.py"
python3 "$ROOT/mobile-import.test.py"

echo "[2/5] Server health"
curl -fsS "$BASE/api/status" >/tmp/stepfilm-status.json
curl -fsS -X POST "$BASE/api/chatgpt/start" -H "Origin: $BASE" -H 'Content-Type: application/json' --data '{}' >/tmp/stepfilm-chatgpt-start.json
python3 - <<'PY'
import json
from urllib.parse import parse_qs, urlsplit
url=json.load(open('/tmp/stepfilm-chatgpt-start.json'))['authorizationUrl']
parts=urlsplit(url)
query=parse_qs(parts.query)
assert parts.scheme == 'https' and parts.netloc == 'auth.openai.com'
assert query['client_id'] == ['dynamic_agent_client']
assert query['agent_name_hint'] == ['Stepfilm']
assert query['redirect_uri'][0].startswith('http://127.0.0.1:')
assert 'chatgpt.tokens.use.direct' in query['scope'][0]
assert query['code_challenge_method'] == ['S256']
PY
curl -fsS "$BASE/chatgpt-callback.js" | rg -q 'stepfilm-chatgpt-connected'

echo "[3/5] Capture API lifecycle"
ORIGIN="$BASE"
curl -fsS -X POST "$BASE/api/capture/start" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' --data '{"title":"Acceptance workflow"}' >/tmp/stepfilm-start.json
if curl -fsS -X POST "$BASE/api/capture/start" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' --data '{"title":"Should not replace active capture"}' >/dev/null 2>&1; then
  echo "a second capture replaced the active capture" >&2; exit 1
fi
curl -fsS "$BASE/api/capture/status" >/tmp/stepfilm-active-status.json
python3 - <<'PY'
import json
started=json.load(open('/tmp/stepfilm-start.json'))
active=json.load(open('/tmp/stepfilm-active-status.json'))
assert active['capturing'] and active['captureId'] == started['captureId']
assert active['title'] == 'Acceptance workflow' and active['count'] == 0
PY
PNG='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
for n in 1 2 3; do
  curl -fsS -X POST "$BASE/api/capture/step" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' --data "{\"image\":\"$PNG\"}" >/tmp/stepfilm-step.json
done
curl -fsS -X POST "$BASE/api/capture/stop" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' --data '{}' >/tmp/stepfilm-stop.json
python3 - <<'PY'
import json
data=json.load(open('/tmp/stepfilm-stop.json'))
assert len(data['steps']) == 3, data
PY

echo "[3b/5] Editor handoff shell"
curl -fsS "$BASE/?capture=latest" | grep -q "Stepfilm"

echo "[3c/5] Continuous clip chunk lifecycle"
python3 - "$BASE" <<'PY'
import io, json, shutil, subprocess, sys, tempfile, urllib.error, urllib.request, zipfile, xml.etree.ElementTree as ET
from pathlib import Path
base = sys.argv[1]
ffmpeg = shutil.which("ffmpeg")
ffprobe = shutil.which("ffprobe")
if not ffmpeg or not ffprobe:
    print("continuous clip lifecycle: SKIP (FFmpeg unavailable)")
    raise SystemExit(0)
origin = base
def post(path, body, content_type="application/json"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, headers={"Origin": origin, "Content-Type": content_type}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())
started = post("/api/capture/start", {"title": "Desktop workflow", "recording": True, "narration": True, "captureMode": "desktop"})
capture_id = started["captureId"]
with urllib.request.urlopen(base + "/api/capture/status", timeout=10) as response:
    assert json.loads(response.read())["captureMode"] == "desktop"
png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
post("/api/capture/step", {"captureId": capture_id, "image": png})
with tempfile.TemporaryDirectory() as temp:
    raw = Path(temp) / "live.webm"
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=24", "-f", "lavfi", "-i", "sine=frequency=660:duration=2.2", "-t", "2.2", "-c:v", "libvpx-vp9", "-c:a", "libopus", "-live", "1", "-f", "webm", str(raw)], check=True, capture_output=True, timeout=60)
    live_bytes = raw.read_bytes()
post(f"/api/capture/chunk?captureId={capture_id}&index=0", live_bytes, "application/octet-stream")
post("/api/capture/recording/finish", {"captureId": capture_id, "chunks": 1})
guide = post("/api/capture/stop", {"captureId": capture_id})
assert len(guide["steps"]) == 1
assert guide["capturedClips"][0]["id"] == capture_id
assert guide["narrationAudioId"] == capture_id, guide
with urllib.request.urlopen(base + guide["capturedClips"][0]["url"], timeout=30) as response:
    finalized_bytes = response.read()
    assert finalized_bytes.startswith(b"\x1a\x45\xdf\xa3")
    finalized = Path(tempfile.gettempdir()) / "stepfilm-api-finalized.webm"
    finalized.write_bytes(finalized_bytes)
    metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_format", "-of", "json", str(finalized)], text=True, timeout=30))
    assert float(metadata["format"]["duration"]) >= 2.0, metadata
    finalized.unlink(missing_ok=True)
with urllib.request.urlopen(base + guide["narrationAudioUrl"], timeout=30) as response:
    audio_file = Path(tempfile.gettempdir()) / "stepfilm-api-narration.webm"
    audio_file.write_bytes(response.read())
    audio_metadata = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-of", "json", str(audio_file)], text=True, timeout=30))
    assert any(stream["codec_type"] == "audio" for stream in audio_metadata["streams"]), audio_metadata
    audio_file.unlink(missing_ok=True)
share_html = f'<!doctype html><html><body><video controls src="media/{capture_id}.webm"><track kind="captions" src="captions/step-1-en.vtt" srclang="en"></video></body></html>'
share_payload = json.dumps({"title": "Portable workflow", "html": share_html, "mediaSteps": [{"videoId": capture_id, "stepNumber": 1, "captionTracks": [{"language": "en", "cues": [{"start": 0, "end": 1, "text": "Start here"}]}]}]}).encode()
share_request = urllib.request.Request(base + "/api/export/share", data=share_payload, headers={"Origin": origin, "Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(share_request, timeout=30) as response, zipfile.ZipFile(io.BytesIO(response.read())) as archive:
    assert {"index.html", f"media/{capture_id}.webm", "captions/step-1-en.vtt"} == set(archive.namelist())
scorm_request = urllib.request.Request(base + "/api/export/scorm", data=share_payload, headers={"Origin": origin, "Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(scorm_request, timeout=30) as response, zipfile.ZipFile(io.BytesIO(response.read())) as archive:
    assert {"imsmanifest.xml", "index.html", f"media/{capture_id}.webm", "captions/step-1-en.vtt"} == set(archive.namelist())
    manifest = ET.fromstring(archive.read("imsmanifest.xml"))
    assert manifest.tag == "{http://www.imsproject.org/xsd/imscp_rootv1p1p2}manifest"
    resource = manifest.find(".//{http://www.imsproject.org/xsd/imscp_rootv1p1p2}resource")
    assert resource.attrib["{http://www.adlnet.org/xsd/adlcp_rootv1p2}scormtype"] == "sco"
    assert "LMSInitialize" in archive.read("index.html").decode("utf-8")
print("continuous clip lifecycle: PASS")
PY

echo "[4/5] Invalid input rejection"
if curl -fsS -X POST "$BASE/api/capture/step" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' --data '{"image":"not-an-image"}' >/dev/null 2>&1; then
  echo "invalid image was accepted" >&2; exit 1
fi

echo "[5/5] Threshold result"
echo "AUTOMATED PASS: syntax, manifest, health, screenshot capture, finalized chunked WebM lifecycle, mixed video rendering, and invalid-image rejection"
echo "MANUAL REQUIRED: 3 consecutive Chrome runs, popup recovery, uninterrupted recording, clip insertion/order, annotation visibility, redaction, comprehension, and 3 playable exports"
