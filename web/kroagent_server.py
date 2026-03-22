#!/usr/bin/env python3
"""KroAgent web server — thin web UI over tmux-backed Claude Code sessions.

Each Claude Code KroAgent runs in a tmux session. This server:
- Serves a chat UI at /
- POST /send — sends text to the tmux pane via send-keys
- GET /buffer — returns the current pane buffer (capture-pane)
- Device pairing required before sending commands
- Both web and tmux terminal see the same session
"""

import base64
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("KROAGENT_PORT", "18850"))
TMUX_SESSION = os.environ.get("KROAGENT_TMUX_SESSION", "kroagent-kroroku-dev")
AGENT_NAME = os.environ.get("KROAGENT_NAME", "kroroku-dev")
BUFFER_LINES = 2000
DATA_DIR = Path(os.environ.get("KROAGENT_DATA_DIR", str(Path.home() / ".config" / "kroagents")))
UPLOADS_DIR = Path(os.environ.get("KROAGENT_UPLOADS_DIR", str(Path.home() / "kroagents" / AGENT_NAME / "uploads")))

# --- Device pairing ---
_devices = {"paired": {}, "pending": {}}


def _devices_file():
    return DATA_DIR / f"paired_devices_{AGENT_NAME}.json"


def _load_devices():
    global _devices
    try:
        f = _devices_file()
        if f.exists():
            _devices = json.loads(f.read_text())
    except Exception:
        pass


def _save_devices():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _devices_file().write_text(json.dumps(_devices, indent=2))


def _is_paired(device_id):
    return device_id in _devices.get("paired", {})


def _add_pending(device_id, info):
    _devices.setdefault("pending", {})[device_id] = {
        "info": info,
        "requested_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
    _save_devices()


def _approve_device(device_id):
    pending = _devices.get("pending", {})
    if device_id in pending:
        _devices.setdefault("paired", {})[device_id] = {
            "info": pending[device_id].get("info", ""),
            "paired_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        }
        del pending[device_id]
        _save_devices()
        return True
    return False


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


CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AGENT_NAME_PLACEHOLDER</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; }
body {
  font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
  background: #0d1117; color: #c9d1d9;
  display: flex; flex-direction: column; overflow: hidden;
}
#header {
  background: #161b22; padding: 10px 20px;
  display: flex; align-items: center; gap: 12px;
  border-bottom: 1px solid #30363d; flex-shrink: 0;
}
#header h1 { font-size: 16px; color: #58a6ff; font-weight: 600; }
#header .status { font-size: 12px; padding: 2px 8px; border-radius: 10px; }
#header .status.online { background: #1b4332; color: #4ade80; }
#header .status.offline { background: #3b1c1c; color: #f87171; }
#header .status.pairing { background: #3b2e1c; color: #fbbf24; }
#header .controls { margin-left: auto; display: flex; gap: 8px; }
#header button {
  background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
  padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 12px;
}
#header button:hover { background: #30363d; }
#terminal {
  flex: 1; overflow-y: auto; padding: 12px 16px;
  font-size: 13px; line-height: 1.5;
  white-space: pre-wrap; word-wrap: break-word;
  background: #0d1117;
}
#input-bar {
  background: #161b22; padding: 10px 16px;
  border-top: 1px solid #30363d; display: flex; gap: 8px; flex-shrink: 0;
}
#input-bar textarea {
  flex: 1; background: #0d1117; border: 1px solid #30363d;
  color: #c9d1d9; padding: 8px 12px; border-radius: 6px;
  font-family: inherit; font-size: 14px; outline: none;
  resize: none; min-height: 40px; max-height: 200px;
  line-height: 1.5; overflow-y: auto;
}
#input-bar textarea:focus { border-color: #58a6ff; }
#input-bar button {
  background: #238636; color: white; border: none;
  padding: 8px 16px; border-radius: 6px; cursor: pointer;
  font-size: 13px; font-weight: 500;
}
#input-bar button:hover { background: #2ea043; }
#input-bar button:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }
#input-bar .img-preview {
  position: relative; display: inline-block; flex-shrink: 0;
}
#input-bar .img-preview img {
  height: 60px; border-radius: 4px; border: 1px solid #30363d;
}
#input-bar .img-preview .remove {
  position: absolute; top: -6px; right: -6px;
  width: 18px; height: 18px; border-radius: 50%;
  background: #f87171; color: #fff; border: none;
  font-size: 12px; line-height: 1; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
#input-bar .img-preview .remove:hover { background: #ef4444; }
body.dragging #terminal {
  border: 2px dashed #58a6ff;
  background: #0d1117ee;
}
body.dragging #terminal::after {
  content: 'Drop image here';
  position: absolute; top: 50%; left: 50%;
  transform: translate(-50%, -50%);
  color: #58a6ff; font-size: 18px; font-weight: bold;
  pointer-events: none;
}
#terminal { position: relative; }
.upload-notice {
  background: #1b4332; color: #4ade80; padding: 6px 12px;
  border-radius: 6px; font-size: 12px; margin: 4px 16px;
  flex-shrink: 0;
}
#pairing {
  display: none; text-align: center; padding: 60px 20px;
  flex: 1; flex-direction: column; justify-content: center;
}
#pairing h2 { color: #58a6ff; margin-bottom: 12px; font-size: 20px; }
#pairing p { color: #8b949e; margin-bottom: 16px; line-height: 1.6; }
#pairing .device-id {
  font-family: monospace; background: #161b22; padding: 8px 20px;
  border-radius: 6px; display: inline-block; margin: 12px 0;
  font-size: 18px; color: #58a6ff; border: 1px solid #30363d;
}
#pairing button {
  background: #238636; color: white; border: none;
  padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 14px;
}
#pairing button:hover { background: #2ea043; }
#pair-status { margin-top: 12px; color: #8b949e; }
</style>
</head>
<body>
<div id="header">
  <h1>AGENT_NAME_PLACEHOLDER</h1>
  <span class="status" id="status">checking...</span>
  <div class="controls">
    <button onclick="sendKey('Escape')" title="Send Escape">Esc</button>
    <button onclick="sendKey('C-c')" title="Send Ctrl+C">Ctrl+C</button>
    <button onclick="sendKey('Enter')" title="Send Enter">Enter</button>
    <button onclick="sendKey('Space')" title="Send Space">Space</button>
    <button onclick="refreshBuffer()">Refresh</button>
    <button onclick="toggleAutoRefresh()" id="auto-btn">Auto: ON</button>
  </div>
</div>

<div id="pairing">
  <h2>Device Pairing Required</h2>
  <p>This browser needs to be paired before you can interact with this agent.</p>
  <div class="device-id" id="pair-device-id"></div>
  <p>Approve this device via CLI:<br><code style="color:#58a6ff">kroagent approve AGENT_NAME_PLACEHOLDER &lt;device-id&gt;</code></p>
  <button onclick="checkPairing()">Check Pairing</button>
  <p id="pair-status"></p>
</div>

<div id="terminal"></div>
<div id="input-bar">
  <textarea id="msg-input" placeholder="Type a message..." autocomplete="off" rows="1"></textarea>
  <button id="send-btn" onclick="sendMessage()">Send</button>
</div>

<script>
let deviceId = localStorage.getItem('kroagent-device-id');
if (!deviceId) {
  deviceId = ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
    (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16));
  localStorage.setItem('kroagent-device-id', deviceId);
}

const terminalEl = document.getElementById('terminal');
const inputEl = document.getElementById('msg-input');
const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status');
const autoBtn = document.getElementById('auto-btn');
const pairingEl = document.getElementById('pairing');
const inputBar = document.getElementById('input-bar');

let autoRefresh = true;
let autoInterval = setInterval(refreshBuffer, 2000);
let lastBuffer = '';
let userScrolled = false;
let paired = false;

terminalEl.addEventListener('scroll', () => {
  const atBottom = terminalEl.scrollHeight - terminalEl.scrollTop - terminalEl.clientHeight < 50;
  userScrolled = !atBottom;
});

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function checkAuth() {
  try {
    const resp = await fetch('/api/pair-status?device_id=' + deviceId);
    const data = await resp.json();
    if (data.paired) {
      paired = true;
      pairingEl.style.display = 'none';
      terminalEl.style.display = 'block';
      inputBar.style.display = 'flex';
      refreshBuffer();
    } else {
      paired = false;
      pairingEl.style.display = 'block';
      terminalEl.style.display = 'none';
      inputBar.style.display = 'none';
      document.getElementById('pair-device-id').textContent = deviceId.slice(0, 8);
      statusEl.textContent = 'pairing required';
      statusEl.className = 'status pairing';
      // Request pairing
      await fetch('/api/pair', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device_id: deviceId, info: navigator.userAgent.slice(0, 80)})
      });
    }
  } catch(e) {
    statusEl.textContent = 'error';
    statusEl.className = 'status offline';
  }
}

async function checkPairing() {
  document.getElementById('pair-status').textContent = 'Checking...';
  await checkAuth();
  if (!paired) {
    document.getElementById('pair-status').textContent = 'Not yet paired. Run the approve command above.';
  }
}

async function refreshBuffer() {
  if (!paired) {
    await checkAuth();
    if (!paired) return;
  }
  try {
    const resp = await fetch('/buffer?device_id=' + deviceId);
    const data = await resp.json();

    if (data.error === 'not paired') {
      paired = false;
      checkAuth();
      return;
    }

    if (data.status === 'offline') {
      statusEl.textContent = 'offline';
      statusEl.className = 'status offline';
      terminalEl.textContent = 'Session not running. Start the agent with: kroagent start ' + data.agent;
      return;
    }

    statusEl.textContent = 'online';
    statusEl.className = 'status online';

    if (data.buffer !== lastBuffer) {
      lastBuffer = data.buffer;
      terminalEl.innerHTML = escapeHtml(data.buffer);
      if (!userScrolled) {
        terminalEl.scrollTop = terminalEl.scrollHeight;
      }
    }
  } catch(e) {
    statusEl.textContent = 'error';
    statusEl.className = 'status offline';
  }
}

function toggleAutoRefresh() {
  autoRefresh = !autoRefresh;
  autoBtn.textContent = 'Auto: ' + (autoRefresh ? 'ON' : 'OFF');
  if (autoRefresh) {
    autoInterval = setInterval(refreshBuffer, 2000);
  } else {
    clearInterval(autoInterval);
  }
}

async function sendMessage() {
  const msg = inputEl.value;
  const hasImage = !!pendingImage;
  if (!msg.trim() && !hasImage) return;
  inputEl.value = '';
  inputEl.style.height = 'auto';
  sendBtn.disabled = true;

  try {
    let fullMsg = msg;

    // Upload staged image first if present
    if (hasImage) {
      const imgPath = await uploadStagedImage();
      if (imgPath) {
        if (fullMsg.trim()) {
          fullMsg = fullMsg.trim() + ' [Image: ' + imgPath + ']';
        } else {
          fullMsg = 'Please look at this image: ' + imgPath;
        }
      }
    }

    if (fullMsg.trim()) {
      const resp = await fetch('/send', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: fullMsg, device_id: deviceId})
      });
      const data = await resp.json();
      if (data.error === 'not paired') {
        paired = false;
        checkAuth();
      } else {
        setTimeout(refreshBuffer, 500);
        setTimeout(refreshBuffer, 2000);
        setTimeout(refreshBuffer, 5000);
      }
    }
  } catch(e) {
    console.error('Send error:', e);
  }
  sendBtn.disabled = false;
  inputEl.focus();
}

async function sendKey(key) {
  try {
    await fetch('/key', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: key, device_id: deviceId})
    });
    setTimeout(refreshBuffer, 300);
    setTimeout(refreshBuffer, 1000);
  } catch(e) {
    console.error('Key error:', e);
  }
}

inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !sendBtn.disabled) {
    e.preventDefault();
    sendMessage();
  }
});

inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
});

// --- Image paste and drag-and-drop (staged) ---
let pendingImage = null; // {blob, dataUrl}

function stageImage(blob) {
  const reader = new FileReader();
  reader.onload = () => {
    pendingImage = {blob: blob, dataUrl: reader.result};
    // Show preview in input bar
    let preview = document.getElementById('img-preview');
    if (preview) preview.remove();
    preview = document.createElement('div');
    preview.id = 'img-preview';
    preview.className = 'img-preview';
    const img = document.createElement('img');
    img.src = reader.result;
    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'remove';
    removeBtn.textContent = 'x';
    removeBtn.onclick = (e) => { e.stopPropagation(); pendingImage = null; preview.remove(); };
    preview.appendChild(img);
    preview.appendChild(removeBtn);
    const inputBar = document.getElementById('input-bar');
    inputBar.insertBefore(preview, inputBar.firstChild);
    inputEl.focus();
  };
  reader.readAsDataURL(blob);
}

async function uploadStagedImage() {
  if (!pendingImage) return null;
  const base64 = pendingImage.dataUrl.split(',')[1];
  const ext = pendingImage.blob.type.split('/')[1] || 'png';
  try {
    const resp = await fetch('/upload', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({image: base64, ext: ext, device_id: deviceId})
    });
    const data = await resp.json();
    pendingImage = null;
    const preview = document.getElementById('img-preview');
    if (preview) preview.remove();
    return data.path || null;
  } catch(e) {
    console.error('Upload error:', e);
    return null;
  }
}

// Ctrl+V paste
document.addEventListener('paste', e => {
  if (!paired) return;
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault();
      stageImage(item.getAsFile());
      return;
    }
  }
});

// Drag and drop
let dragCounter = 0;

document.addEventListener('dragenter', e => {
  e.preventDefault();
  dragCounter++;
  document.body.classList.add('dragging');
});

document.addEventListener('dragleave', e => {
  dragCounter--;
  if (dragCounter <= 0) {
    dragCounter = 0;
    document.body.classList.remove('dragging');
  }
});

document.addEventListener('dragover', e => {
  e.preventDefault();
});

document.addEventListener('drop', e => {
  e.preventDefault();
  dragCounter = 0;
  document.body.classList.remove('dragging');
  if (!paired) return;
  const files = e.dataTransfer?.files;
  if (!files) return;
  for (const file of files) {
    if (file.type.startsWith('image/')) {
      stageImage(file);
      return;
    }
  }
});

// Initial load
checkAuth();
</script>
</body>
</html>"""


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

    def _html(self, code, html):
        body = html.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/", ""):
            html = CHAT_HTML.replace("AGENT_NAME_PLACEHOLDER", AGENT_NAME)
            self._html(200, html)

        elif parsed.path == "/api/pair-status":
            device_id = qs.get("device_id", [""])[0]
            self._json(200, {"paired": _is_paired(device_id)})

        elif parsed.path == "/api/pending":
            self._json(200, {"pending": _devices.get("pending", {})})

        elif parsed.path.startswith("/api/approve/"):
            device_id = parsed.path.split("/api/approve/")[1]
            ok = _approve_device(device_id)
            self._json(200, {"approved": ok, "device_id": device_id})

        elif parsed.path == "/buffer":
            device_id = qs.get("device_id", [""])[0]
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
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

        if parsed.path == "/api/pair":
            device_id = body.get("device_id", "")
            info = body.get("info", "")
            if device_id and not _is_paired(device_id):
                _add_pending(device_id, info)
                sys.stderr.write(f"[{AGENT_NAME}] PAIR REQUEST: {device_id[:8]}... from {info[:60]}\n")
                sys.stderr.flush()
            self._json(200, {"status": "pending"})

        elif parsed.path == "/send":
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
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
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
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
            # Save to uploads dir (don't send to tmux — sendMessage handles that)
            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            filename = f"image-{ts}.{ext}"
            filepath = UPLOADS_DIR / filename
            filepath.write_bytes(base64.b64decode(image_b64))
            self._json(200, {"path": str(filepath), "filename": filename})

        elif parsed.path == "/key":
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
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
    _load_devices()
    bind = os.environ.get("KROAGENT_BIND", "127.0.0.1")
    server = HTTPServer((bind, PORT), Handler)
    print(f"[kroagent-web] {AGENT_NAME} listening on {bind}:{PORT}, tmux={TMUX_SESSION}", flush=True)
    server.serve_forever()

if __name__ == "__main__":
    main()
