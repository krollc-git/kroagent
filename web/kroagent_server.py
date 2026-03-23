#!/usr/bin/env python3
"""KroAgent web server — thin web UI over tmux-backed Claude Code sessions.

Each Claude Code KroAgent runs in a tmux session. This server:
- POST /send — sends text to the tmux pane via send-keys
- GET /buffer — returns the current pane buffer (capture-pane)
- POST /key — sends a raw key (Escape, C-c, etc.)
- POST /upload — saves an uploaded image to the agent's uploads dir
- GET /status — returns agent status

Internal only — accessed by the dashboard proxy on localhost.
No device pairing or authentication (dashboard handles that).
"""

import base64
import json
import os
import subprocess
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("KROAGENT_PORT", "18850"))
TMUX_SESSION = os.environ.get("KROAGENT_TMUX_SESSION", "kroagent-default")
AGENT_NAME = os.environ.get("KROAGENT_NAME", "default")
BUFFER_LINES = 2000
UPLOADS_DIR = Path(os.environ.get("KROAGENT_UPLOADS_DIR", str(Path.home() / "kroagents" / AGENT_NAME / "uploads")))


# --- Tmux interaction ---

def get_pane_buffer():
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", TMUX_SESSION, "-p", "-S", f"-{BUFFER_LINES}"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout
    except Exception as e:
        return f"Error reading buffer: {e}"


def send_to_pane(text):
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", TMUX_SESSION, text, "Enter"],
            capture_output=True, text=True, timeout=5
        )
        return True
    except Exception:
        return False


def send_key_to_pane(key):
    """Send a raw tmux key (Escape, C-c, Enter, etc.) without appending Enter."""
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", TMUX_SESSION, key],
            capture_output=True, text=True, timeout=5
        )
        return True
    except Exception:
        return False


def get_session_status():
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", TMUX_SESSION],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except:
        return False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/buffer":
            if get_session_status():
                buf = get_pane_buffer()
                self._json(200, {"buffer": buf, "status": "online", "agent": AGENT_NAME})
            else:
                self._json(200, {"buffer": "", "status": "offline", "agent": AGENT_NAME})

        elif parsed.path == "/status":
            online = get_session_status()
            self._json(200, {"agent": AGENT_NAME, "status": "online" if online else "offline"})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        parsed = urlparse(self.path)

        if parsed.path == "/send":
            text = body.get("text", "").strip()
            if not text:
                self._json(400, {"error": "empty message"})
                return
            if not get_session_status():
                self._json(503, {"error": "session not running"})
                return
            ok = send_to_pane(text)
            self._json(200, {"sent": ok})

        elif parsed.path == "/upload":
            if not get_session_status():
                self._json(503, {"error": "session not running"})
                return
            image_b64 = body.get("image", "")
            ext = body.get("ext", "png")
            if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
                ext = "png"
            if not image_b64:
                self._json(400, {"error": "no image data"})
                return
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"image-{ts}.{ext}"
            filepath = UPLOADS_DIR / filename
            filepath.write_bytes(base64.b64decode(image_b64))
            self._json(200, {"path": str(filepath), "filename": filename})

        elif parsed.path == "/key":
            key = body.get("key", "")
            allowed = {"Escape", "C-c", "Enter", "Space", "Up", "Down", "Left", "Right"}
            if key not in allowed:
                self._json(400, {"error": f"key not allowed: {key}"})
                return
            if not get_session_status():
                self._json(503, {"error": "session not running"})
                return
            ok = send_key_to_pane(key)
            self._json(200, {"sent": ok})

        else:
            self.send_response(404)
            self.end_headers()


def main():
    bind = os.environ.get("KROAGENT_BIND", "127.0.0.1")
    server = HTTPServer((bind, PORT), Handler)
    print(f"[kroagent-web] {AGENT_NAME} listening on {bind}:{PORT}, tmux={TMUX_SESSION}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
