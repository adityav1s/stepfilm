import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Lock
from urllib.parse import urlsplit
import webbrowser

from render import Renderer, parse_guide


APP = Path(__file__).resolve().parent / "app"
STATIC = {"/": ("index.html", "text/html"), "/style.css": ("style.css", "text/css"),
          "/editor.js": ("editor.js", "text/javascript"), "/annotation-model.js": ("annotation-model.js", "text/javascript")}
MAX_REQUEST = 50 * 1024 * 1024
EXPORT_LOCK = Lock()


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
        elif path == "/" and parsed.query == "capture=latest":
            # Capture stop opens this URL. Always serve the editor shell; the
            # editor then reads the captured project from the API endpoint.
            name, kind = STATIC["/"]
            self.respond(200, f"{kind}; charset=utf-8", (APP / name).read_bytes())
        elif path == "/api/status":
            voice = self.server.renderer.engine if self.server.renderer else "Unavailable — install FFmpeg"
            self.json(200, {"voice": voice})
        elif path == "/api/capture/latest":
            if self.server.capture_latest is None:
                self.json(404, {"error": "No captured workflow is waiting in Stepfilm."})
            else:
                self.json(200, self.server.capture_latest)
        elif path == "/api/capture/status":
            self.json(200, {"capturing": self.server.capture_steps is not None,
                            "count": len(self.server.capture_steps or []),
                            "title": self.server.capture_title})
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
            if not 0 < size <= MAX_REQUEST:
                raise ValueError("Choose an HTML guide under 50 MB.")
            if self.path == "/api/import" and self.headers.get_content_type() == "text/plain":
                self.json(200, parse_guide(self.rfile.read(size).decode("utf-8-sig")))
                return
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Unsupported request format.")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError("Invalid project data.")
            if self.path == "/api/capture/start":
                title = body.get("title", "Captured workflow")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("Give the capture a title.")
                self.server.capture_steps = []
                self.server.capture_title = title.strip()[:150]
                self.json(200, {"count": 0})
                return
            if self.path == "/api/capture/step":
                if self.server.capture_steps is None:
                    raise ValueError("Start a capture before adding steps.")
                if len(self.server.capture_steps) >= 50:
                    raise ValueError("A guide can have up to 50 steps.")
                image = body.get("image")
                from render import image_bytes
                image_bytes(image)
                self.server.capture_steps.append({"image": image, "text": ""})
                self.json(200, {"count": len(self.server.capture_steps)})
                return
            if self.path == "/api/capture/stop":
                if not self.server.capture_steps:
                    raise ValueError("Capture at least one step before stopping.")
                self.server.capture_latest = {"title": self.server.capture_title, "steps": self.server.capture_steps}
                self.server.capture_steps = None
                self.json(200, self.server.capture_latest)
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
                        path = self.server.renderer.video(body.get("steps"), Path(temp))
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
            server.renderer = renderer
            server.capture_steps = None
            server.capture_title = "Captured workflow"
            server.capture_latest = None
            server.last_export = Path(tempfile.gettempdir()) / "stepfilm-last.mp4"
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
