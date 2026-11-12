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
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$ROOT/capture-extension/manifest.json"

echo "[2/5] Server health"
curl -fsS "$BASE/api/status" >/tmp/stepfilm-status.json

echo "[3/5] Capture API lifecycle"
ORIGIN="$BASE"
curl -fsS -X POST "$BASE/api/capture/start" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' --data '{"title":"Acceptance workflow"}' >/tmp/stepfilm-start.json
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

echo "[4/5] Invalid input rejection"
if curl -fsS -X POST "$BASE/api/capture/step" -H "Origin: $ORIGIN" -H 'Content-Type: application/json' --data '{"image":"not-an-image"}' >/dev/null 2>&1; then
  echo "invalid image was accepted" >&2; exit 1
fi

echo "[5/5] Threshold result"
echo "AUTOMATED PASS: syntax, manifest, health, 3-step capture lifecycle, and invalid-image rejection"
echo "MANUAL REQUIRED: 3 consecutive Chrome runs, annotation visibility, redaction, comprehension, and 3 playable exports"
