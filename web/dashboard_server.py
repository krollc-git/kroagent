#!/usr/bin/env python3
"""KroAgent Dashboard — multi-pane chat UI for managing up to 6 agents.

Serves a single-page dashboard at / with mini chat panes for each active agent.
Proxies all API calls to each agent's kroagent_server.py instance on localhost.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("DASHBOARD_PORT", "18900"))
KROAGENTS_DIR = Path(os.environ.get("KROAGENTS_DIR", str(Path.home() / "kroagents")))
DATA_DIR = Path(os.environ.get("DASHBOARD_DATA_DIR", str(Path.home() / ".config" / "kroagents")))
MAX_PANES = 6

# Fixed device ID the dashboard uses when proxying to agent web servers.
# Auto-paired with each agent on first contact so the user only pairs once (with the dashboard).
DASHBOARD_DEVICE_ID = "kroagent-dashboard-proxy-00000000"

# --- Device pairing (dashboard-level) ---
_devices = {"paired": {}, "pending": {}}


def _devices_file():
    return DATA_DIR / "paired_devices_dashboard.json"


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


# --- Agent discovery ---

def discover_agents():
    """Read all agent.json files from KROAGENTS_DIR, return list of agent configs."""
    agents = []
    if not KROAGENTS_DIR.is_dir():
        return agents
    for d in sorted(KROAGENTS_DIR.iterdir()):
        config_file = d / "agent.json"
        if config_file.is_file():
            try:
                config = json.loads(config_file.read_text())
                if config.get("dashboard") is False:
                    continue
                config["_dir"] = str(d)
                agents.append(config)
            except (json.JSONDecodeError, OSError):
                pass
    return agents


# --- Proxy to agent web servers ---

_auto_paired_agents = set()  # ports we've already auto-paired with


def _ensure_agent_paired(port):
    """Auto-pair the dashboard's proxy device ID with an agent if not already done."""
    if port in _auto_paired_agents:
        return
    try:
        # Check if already paired
        check_url = f"http://127.0.0.1:{port}/api/pair-status?device_id={DASHBOARD_DEVICE_ID}"
        req = urllib.request.Request(check_url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if data.get("paired"):
                _auto_paired_agents.add(port)
                return
        # Request pairing
        pair_url = f"http://127.0.0.1:{port}/api/pair"
        pair_body = json.dumps({"device_id": DASHBOARD_DEVICE_ID, "info": "dashboard-proxy"}).encode()
        req = urllib.request.Request(pair_url, data=pair_body, method="POST",
                                    headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        # Approve immediately (dashboard is trusted localhost)
        approve_url = f"http://127.0.0.1:{port}/api/approve/{DASHBOARD_DEVICE_ID}"
        req = urllib.request.Request(approve_url)
        urllib.request.urlopen(req, timeout=5)
        _auto_paired_agents.add(port)
    except Exception:
        pass  # Agent may be offline, that's fine


def proxy_to_agent(port, path, method="GET", body=None):
    """Forward a request to an agent's web server on localhost:<port>.

    Always uses DASHBOARD_DEVICE_ID so agent-level pairing is transparent.
    """
    _ensure_agent_paired(port)

    # Rewrite device_id in query strings and POST bodies to use the dashboard's proxy ID
    if "device_id=" in path:
        # Replace any device_id in the URL with the dashboard's
        import re
        path = re.sub(r'device_id=[^&]*', f'device_id={DASHBOARD_DEVICE_ID}', path)

    if body is not None and "device_id" in body:
        body = dict(body)
        body["device_id"] = DASHBOARD_DEVICE_ID

    url = f"http://127.0.0.1:{port}{path}"
    try:
        if body is not None:
            data = json.dumps(body).encode()
            req = urllib.request.Request(url, data=data, method=method,
                                        headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"error": f"HTTP {e.code}"}
    except Exception as e:
        return {"error": str(e)}


KROAGENT_CLI = str(KROAGENTS_DIR / "kroagent")


def agent_status(name):
    """Check tmux and web server status for an agent. Returns dict with tmux/web booleans."""
    config_file = KROAGENTS_DIR / name / "agent.json"
    if not config_file.is_file():
        return {"tmux": False, "web": False}
    try:
        config = json.loads(config_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {"tmux": False, "web": False}

    tmux_session = config.get("tmux_session", "")
    port = config.get("port", 0)
    agent_type = config.get("type", "")

    tmux_up = False
    if tmux_session and agent_type == "claude-code":
        try:
            r = subprocess.run(["tmux", "has-session", "-t", tmux_session],
                               capture_output=True, timeout=5)
            tmux_up = r.returncode == 0
        except Exception:
            pass

    web_up = False
    if port:
        try:
            status_path = "/status" if agent_type == "claude-code" else "/"
            req = urllib.request.Request(f"http://127.0.0.1:{port}{status_path}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                web_up = resp.status == 200
        except Exception:
            pass

    return {"tmux": tmux_up, "web": web_up}


def run_kroagent_cmd(action, name):
    """Run a kroagent CLI command (start/stop/restart). Returns (success, output)."""
    if action not in ("start", "stop", "restart"):
        return False, f"Invalid action: {action}"
    # Validate agent name: alphanumeric, hyphens, underscores only
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        return False, f"Invalid agent name: {name}"
    config_file = KROAGENTS_DIR / name / "agent.json"
    if not config_file.is_file():
        return False, f"Agent '{name}' not found"
    try:
        result = subprocess.run(
            [KROAGENT_CLI, action, name],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HOME": str(Path.home())}
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KroAgent Dashboard</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { height: 100%; }
body {
  font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
  background: #0d1117; color: #c9d1d9;
  display: flex; flex-direction: column; overflow: hidden;
}

/* Top bar */
#topbar {
  background: #161b22; padding: 8px 16px;
  display: flex; align-items: center; gap: 12px;
  border-bottom: 1px solid #30363d; flex-shrink: 0;
}
#topbar h1 { font-size: 15px; color: #58a6ff; font-weight: 600; }
#topbar .agent-count { font-size: 12px; color: #8b949e; }
#topbar .controls { margin-left: auto; display: flex; gap: 8px; }
#topbar button {
  background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
  padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 11px;
}
#topbar button:hover { background: #30363d; }

/* Pairing screen */
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

/* Grid of chat panes */
#grid {
  flex: 1; display: grid; gap: 4px; padding: 4px;
  overflow: hidden;
}
.grid-1 { grid-template-columns: 1fr; }
.grid-2 { grid-template-columns: 1fr 1fr; }
.grid-3 { grid-template-columns: 1fr 1fr 1fr; }
.grid-4 { grid-template-columns: 1fr 1fr; grid-template-rows: 1fr 1fr; }
.grid-5 { grid-template-columns: 1fr 1fr 1fr; grid-template-rows: 1fr 1fr; }
.grid-6 { grid-template-columns: 1fr 1fr 1fr; grid-template-rows: 1fr 1fr; }

/* Individual chat pane */
.pane {
  background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
  display: flex; flex-direction: column; overflow: hidden;
  min-height: 0;
}
.pane.focused { border-color: #58a6ff; }
.pane-header {
  background: #161b22; padding: 6px 10px;
  display: flex; align-items: center; gap: 8px;
  border-bottom: 1px solid #30363d; flex-shrink: 0;
}
.pane-header .agent-name { font-size: 12px; color: #58a6ff; font-weight: 600; }
.pane-header .status-dot {
  width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
}
.pane-header .status-dot.online { background: #4ade80; }
.pane-header .status-dot.offline { background: #f87171; }
.pane-header .status-dot.checking { background: #fbbf24; }
.pane-header .pane-controls { margin-left: auto; display: flex; gap: 4px; }
.pane-header button {
  background: #21262d; color: #8b949e; border: 1px solid #30363d;
  padding: 2px 6px; border-radius: 4px; cursor: pointer; font-size: 10px;
}
.pane-header button:hover { background: #30363d; color: #c9d1d9; }
.pane-header .sep { color: #30363d; font-size: 10px; margin: 0 2px; }
.pane-header .mgmt-btn { font-weight: 600; }
.pane-header .start-btn { color: #4ade80; }
.pane-header .stop-btn { color: #f87171; }
.pane-header .restart-btn { color: #fbbf24; }
.pane-header .mgmt-btn:disabled { color: #484f58; cursor: not-allowed; }
.pane-header .mgmt-btn.running { color: #484f58; cursor: wait; }
.pane-terminal {
  flex: 1; overflow-y: auto; padding: 6px 8px;
  font-size: 11px; line-height: 1.4;
  white-space: pre-wrap; word-wrap: break-word;
  min-height: 0;
}
.pane-input {
  background: #161b22; padding: 6px 8px;
  border-top: 1px solid #30363d; display: flex; gap: 4px; flex-shrink: 0;
}
.pane-input textarea {
  flex: 1; background: #0d1117; border: 1px solid #30363d;
  color: #c9d1d9; padding: 4px 8px; border-radius: 4px;
  font-family: inherit; font-size: 12px; outline: none;
  resize: none; min-height: 28px; max-height: 120px;
  line-height: 1.4; overflow-y: auto;
}
.pane-input textarea:focus { border-color: #58a6ff; }
.pane-input button.send-btn {
  background: #238636; color: white; border: none;
  padding: 4px 10px; border-radius: 4px; cursor: pointer;
  font-size: 11px; font-weight: 500;
}
.pane-input button.send-btn:hover { background: #2ea043; }
.pane-input button.send-btn:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }

/* Image staging */
.pane-input .img-preview {
  position: relative; display: inline-block; flex-shrink: 0;
}
.pane-input .img-preview img {
  height: 40px; border-radius: 4px; border: 1px solid #30363d;
}
.pane-input .img-preview .remove {
  position: absolute; top: -4px; right: -4px;
  width: 14px; height: 14px; border-radius: 50%;
  background: #f87171; color: #fff; border: none;
  font-size: 10px; line-height: 1; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}

/* No agents message */
#no-agents {
  display: none; text-align: center; padding: 80px 20px;
  flex: 1;
}
#no-agents h2 { color: #8b949e; font-size: 18px; margin-bottom: 12px; }
#no-agents p { color: #484f58; font-size: 14px; }

/* Drag and drop overlay per pane */
.pane.dragging .pane-terminal {
  border: 2px dashed #58a6ff; background: #0d1117ee;
}
</style>
</head>
<body>
<div id="topbar">
  <h1>KroAgent Dashboard</h1>
  <span class="agent-count" id="agent-count"></span>
  <div class="controls">
    <button onclick="refreshAll()">Refresh All</button>
    <button onclick="location.reload()">Reload</button>
  </div>
</div>

<div id="pairing">
  <h2>Device Pairing Required</h2>
  <p>This browser needs to be paired with the KroAgent Dashboard.</p>
  <div class="device-id" id="pair-device-id"></div>
  <p>Approve via CLI on kroclaw:<br>
  <code style="color:#58a6ff">curl http://127.0.0.1:DASHBOARD_PORT_PLACEHOLDER/api/approve/&lt;device-id&gt;</code></p>
  <button onclick="checkDashboardPairing()">Check Pairing</button>
  <p id="pair-status"></p>
</div>

<div id="grid" style="display:none;"></div>
<div id="no-agents">
  <h2>No agents found</h2>
  <p>Create agents with <code>kroagent create &lt;name&gt;</code> on kroclaw.</p>
</div>

<script>
// --- Dashboard device pairing ---
let deviceId = localStorage.getItem('kroagent-dashboard-device-id');
if (!deviceId) {
  deviceId = ([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g, c =>
    (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16));
  localStorage.setItem('kroagent-dashboard-device-id', deviceId);
}

let dashboardPaired = false;
let agents = [];
let paneStates = {}; // agentName -> {lastBuffer, userScrolled, pendingImage, autoRefresh, refreshTimer}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// --- Dashboard pairing ---
async function checkDashboardPairing() {
  try {
    const resp = await fetch('/api/pair-status?device_id=' + deviceId);
    const data = await resp.json();
    if (data.paired) {
      dashboardPaired = true;
      document.getElementById('pairing').style.display = 'none';
      await loadAgents();
    } else {
      dashboardPaired = false;
      document.getElementById('pairing').style.display = 'block';
      document.getElementById('grid').style.display = 'none';
      document.getElementById('no-agents').style.display = 'none';
      document.getElementById('pair-device-id').textContent = deviceId.slice(0, 8);
      await fetch('/api/pair', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({device_id: deviceId, info: navigator.userAgent.slice(0, 80)})
      });
      document.getElementById('pair-status').textContent = 'Waiting for approval...';
    }
  } catch(e) {
    document.getElementById('pair-status').textContent = 'Connection error';
  }
}

// --- Agent loading ---
async function loadAgents() {
  try {
    const resp = await fetch('/api/agents?device_id=' + deviceId);
    const data = await resp.json();
    if (data.error === 'not paired') {
      dashboardPaired = false;
      checkDashboardPairing();
      return;
    }
    const newAgents = data.agents || [];
    const newNames = newAgents.map(a => a.name).join(',');
    const oldNames = agents.map(a => a.name).join(',');
    agents = newAgents;
    document.getElementById('agent-count').textContent = agents.length + ' agent' + (agents.length !== 1 ? 's' : '');
    if (newNames !== oldNames) {
      renderGrid();
    }
    // Update management buttons based on live status
    for (const agent of agents) {
      updateMgmtButtons(agent.name, agent);
    }
    refreshAll();
  } catch(e) {
    console.error('Load agents error:', e);
  }
}

// --- Grid rendering ---
function renderGrid() {
  const grid = document.getElementById('grid');
  const noAgents = document.getElementById('no-agents');

  // Stop all existing pane timers before rebuilding
  for (const name in paneStates) {
    if (paneStates[name].refreshTimer) {
      clearInterval(paneStates[name].refreshTimer);
      paneStates[name].refreshTimer = null;
    }
  }

  if (agents.length === 0) {
    grid.style.display = 'none';
    noAgents.style.display = 'block';
    return;
  }

  noAgents.style.display = 'none';
  grid.style.display = 'grid';

  const count = Math.min(agents.length, MAX_PANES);
  grid.className = 'grid-' + count;
  grid.innerHTML = '';

  for (let i = 0; i < count; i++) {
    const agent = agents[i];
    const name = agent.name;

    if (!paneStates[name]) {
      paneStates[name] = {lastBuffer: '', userScrolled: false, pendingImage: null, autoRefresh: true, refreshTimer: null};
    }

    const pane = document.createElement('div');
    pane.className = 'pane';
    pane.id = 'pane-' + name;
    pane.innerHTML = `
      <div class="pane-header">
        <div class="status-dot checking" id="dot-${name}"></div>
        <span class="agent-name">${escapeHtml(name)}</span>
        <div class="pane-controls">
          <button onclick="manageAgent('${name}','start')" id="start-btn-${name}" class="mgmt-btn start-btn" title="Start agent">Start</button>
          <button onclick="manageAgent('${name}','stop')" id="stop-btn-${name}" class="mgmt-btn stop-btn" title="Stop agent">Stop</button>
          <button onclick="manageAgent('${name}','restart')" id="restart-btn-${name}" class="mgmt-btn restart-btn" title="Restart agent">Restart</button>
          <span class="sep">|</span>
          <button onclick="sendAgentKey('${name}','Escape')" title="Escape">Esc</button>
          <button onclick="sendAgentKey('${name}','C-c')" title="Ctrl+C">^C</button>
          <button onclick="sendAgentKey('${name}','Enter')" title="Enter">↵</button>
          <button onclick="sendAgentKey('${name}','Space')" title="Space">⎵</button>
          <button onclick="refreshPane('${name}')" title="Refresh">↻</button>
          <button onclick="togglePaneAutoRefresh('${name}')" id="auto-btn-${name}" title="Toggle auto-refresh">Auto: ON</button>
        </div>
      </div>
      <div class="pane-terminal" id="term-${name}"></div>
      <div class="pane-input" id="input-area-${name}">
        <textarea id="input-${name}" placeholder="Message ${name}..." autocomplete="off" rows="1"
          onkeydown="paneKeydown(event, '${name}')"
          oninput="autoResize(this)"></textarea>
        <button class="send-btn" id="send-btn-${name}" onclick="sendPaneMessage('${name}')">Send</button>
      </div>
    `;
    grid.appendChild(pane);

    // Scroll tracking
    const term = pane.querySelector('.pane-terminal');
    term.addEventListener('scroll', () => {
      const atBottom = term.scrollHeight - term.scrollTop - term.clientHeight < 50;
      paneStates[name].userScrolled = !atBottom;
    });

    // Image paste per pane
    const input = pane.querySelector('textarea');
    input.addEventListener('paste', (e) => handlePanePaste(e, name));

    // Drag and drop per pane
    pane.addEventListener('dragenter', (e) => { e.preventDefault(); pane.classList.add('dragging'); });
    pane.addEventListener('dragleave', (e) => { pane.classList.remove('dragging'); });
    pane.addEventListener('dragover', (e) => { e.preventDefault(); });
    pane.addEventListener('drop', (e) => {
      e.preventDefault();
      pane.classList.remove('dragging');
      const files = e.dataTransfer?.files;
      if (files) {
        for (const file of files) {
          if (file.type.startsWith('image/')) {
            stagePaneImage(name, file);
            return;
          }
        }
      }
    });

    // Start per-pane auto-refresh timer
    startPaneTimer(name);
  }
}

const MAX_PANES = 6;

function startPaneTimer(name) {
  const state = paneStates[name];
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  if (state.autoRefresh) {
    state.refreshTimer = setInterval(() => refreshPane(name), 2000);
  }
}

function togglePaneAutoRefresh(name) {
  const state = paneStates[name];
  state.autoRefresh = !state.autoRefresh;
  const btn = document.getElementById('auto-btn-' + name);
  if (btn) btn.textContent = 'Auto: ' + (state.autoRefresh ? 'ON' : 'OFF');
  if (state.autoRefresh) {
    startPaneTimer(name);
  } else {
    clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
}

// --- Per-pane buffer refresh ---
async function refreshPane(name) {
  if (!dashboardPaired) return;
  const agent = agents.find(a => a.name === name);
  if (!agent) return;

  try {
    const resp = await fetch(`/api/agents/${name}/buffer?device_id=${deviceId}`);
    const data = await resp.json();
    const dot = document.getElementById('dot-' + name);
    const term = document.getElementById('term-' + name);
    const inputArea = document.getElementById('input-area-' + name);

    if (data.error === 'not paired') {
      dashboardPaired = false;
      checkDashboardPairing();
      return;
    }

    if (data.status === 'offline') {
      dot.className = 'status-dot offline';
      term.textContent = 'Session not running.\nkroagent start ' + name;
      return;
    }

    dot.className = 'status-dot online';

    if (data.buffer !== paneStates[name].lastBuffer) {
      paneStates[name].lastBuffer = data.buffer;
      term.innerHTML = escapeHtml(data.buffer);
      if (!paneStates[name].userScrolled) {
        term.scrollTop = term.scrollHeight;
      }
    }
  } catch(e) {
    const dot = document.getElementById('dot-' + name);
    if (dot) dot.className = 'status-dot offline';
  }
}

async function refreshAll() {
  if (!dashboardPaired) return;
  const count = Math.min(agents.length, MAX_PANES);
  const promises = [];
  for (let i = 0; i < count; i++) {
    promises.push(refreshPane(agents[i].name));
  }
  await Promise.all(promises);
}

// --- Send message to agent ---
async function sendPaneMessage(name) {
  const input = document.getElementById('input-' + name);
  const sendBtn = document.getElementById('send-btn-' + name);
  const msg = input.value;
  const state = paneStates[name];
  const hasImage = !!state.pendingImage;

  if (!msg.trim() && !hasImage) return;
  input.value = '';
  input.style.height = 'auto';
  sendBtn.disabled = true;

  try {
    let fullMsg = msg;

    if (hasImage) {
      const imgPath = await uploadPaneImage(name);
      if (imgPath) {
        fullMsg = fullMsg.trim()
          ? fullMsg.trim() + ' [Image: ' + imgPath + ']'
          : 'Please look at this image: ' + imgPath;
      }
    }

    if (fullMsg.trim()) {
      await fetch(`/api/agents/${name}/send`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text: fullMsg, device_id: deviceId})
      });
      setTimeout(() => refreshPane(name), 500);
      setTimeout(() => refreshPane(name), 2000);
      setTimeout(() => refreshPane(name), 5000);
    }
  } catch(e) {
    console.error('Send error:', e);
  }
  sendBtn.disabled = false;
  input.focus();
}

async function sendAgentKey(name, key) {
  try {
    await fetch(`/api/agents/${name}/key`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: key, device_id: deviceId})
    });
    setTimeout(() => refreshPane(name), 300);
    setTimeout(() => refreshPane(name), 1000);
  } catch(e) {
    console.error('Key error:', e);
  }
}

// --- Image handling ---
function handlePanePaste(e, name) {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault();
      stagePaneImage(name, item.getAsFile());
      return;
    }
  }
}

function stagePaneImage(name, blob) {
  const reader = new FileReader();
  reader.onload = () => {
    paneStates[name].pendingImage = {blob: blob, dataUrl: reader.result};
    const inputArea = document.getElementById('input-area-' + name);
    let preview = inputArea.querySelector('.img-preview');
    if (preview) preview.remove();
    preview = document.createElement('div');
    preview.className = 'img-preview';
    const img = document.createElement('img');
    img.src = reader.result;
    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove';
    removeBtn.textContent = 'x';
    removeBtn.onclick = () => { paneStates[name].pendingImage = null; preview.remove(); };
    preview.appendChild(img);
    preview.appendChild(removeBtn);
    inputArea.insertBefore(preview, inputArea.firstChild);
  };
  reader.readAsDataURL(blob);
}

async function uploadPaneImage(name) {
  const state = paneStates[name];
  if (!state.pendingImage) return null;
  const base64 = state.pendingImage.dataUrl.split(',')[1];
  const ext = state.pendingImage.blob.type.split('/')[1] || 'png';
  try {
    const resp = await fetch(`/api/agents/${name}/upload`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({image: base64, ext: ext, device_id: deviceId})
    });
    const data = await resp.json();
    state.pendingImage = null;
    const inputArea = document.getElementById('input-area-' + name);
    const preview = inputArea?.querySelector('.img-preview');
    if (preview) preview.remove();
    return data.path || null;
  } catch(e) {
    return null;
  }
}

// --- Keyboard helpers ---
function paneKeydown(e, name) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendPaneMessage(name);
  }
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

// --- Agent management ---
async function manageAgent(name, action) {
  const btn = document.getElementById(action + '-btn-' + name);
  if (btn) { btn.disabled = true; btn.classList.add('running'); btn.textContent = action + '...'; }

  // Disable all mgmt buttons for this agent during the operation
  ['start', 'stop', 'restart'].forEach(a => {
    const b = document.getElementById(a + '-btn-' + name);
    if (b) b.disabled = true;
  });

  try {
    const resp = await fetch(`/api/agents/${name}/manage`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action, device_id: deviceId})
    });
    const data = await resp.json();
    if (!data.success) {
      console.error(`${action} ${name} failed:`, data.output);
    }
  } catch(e) {
    console.error(`${action} ${name} error:`, e);
  }

  // Restore button text
  if (btn) { btn.textContent = action.charAt(0).toUpperCase() + action.slice(1); btn.classList.remove('running'); }

  // Wait a moment for processes to settle, then refresh status
  setTimeout(async () => {
    await loadAgents();
    refreshPane(name);
  }, action === 'start' ? 3000 : 1000);
}

function updateMgmtButtons(name, agentInfo) {
  const isOnline = agentInfo && agentInfo.tmux && agentInfo.web;
  const startBtn = document.getElementById('start-btn-' + name);
  const stopBtn = document.getElementById('stop-btn-' + name);
  const restartBtn = document.getElementById('restart-btn-' + name);

  if (startBtn) startBtn.disabled = isOnline;
  if (stopBtn) stopBtn.disabled = !isOnline && !(agentInfo && agentInfo.tmux);
  if (restartBtn) restartBtn.disabled = !isOnline && !(agentInfo && agentInfo.tmux);
}

// --- Init ---
checkDashboardPairing();

// On window resize, scroll all non-user-scrolled panes to bottom
window.addEventListener('resize', () => {
  for (const name in paneStates) {
    if (!paneStates[name].userScrolled) {
      const term = document.getElementById('term-' + name);
      if (term) term.scrollTop = term.scrollHeight;
    }
  }
});

// Reload agents list every 30s (in case agents are started/stopped)
setInterval(() => {
  if (dashboardPaired) loadAgents();
}, 30000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
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

    def _get_agent_port(self, name):
        """Look up agent port from config."""
        config_file = KROAGENTS_DIR / name / "agent.json"
        if not config_file.is_file():
            return None
        try:
            config = json.loads(config_file.read_text())
            return int(config.get("port", 0))
        except (json.JSONDecodeError, OSError, ValueError):
            return None

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path in ("/", ""):
            html = DASHBOARD_HTML.replace("DASHBOARD_PORT_PLACEHOLDER", str(PORT))
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

        elif parsed.path == "/api/agents":
            device_id = qs.get("device_id", [""])[0]
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            agents = discover_agents()
            result = []
            for a in agents:
                name = a.get("name", "")
                status = agent_status(name) if name else {"tmux": False, "web": False}
                result.append({
                    "name": name,
                    "type": a.get("type", ""),
                    "port": a.get("port", 0),
                    "domain": a.get("domain", ""),
                    "description": a.get("description", ""),
                    "tmux": status["tmux"],
                    "web": status["web"],
                })
            self._json(200, {"agents": result})

        elif parsed.path.startswith("/api/agents/") and "/buffer" in parsed.path:
            device_id = qs.get("device_id", [""])[0]
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            name = parsed.path.split("/api/agents/")[1].split("/buffer")[0]
            port = self._get_agent_port(name)
            if not port:
                self._json(404, {"error": "agent not found"})
                return
            # Get buffer (dashboard auto-pairs with agents transparently)
            result = proxy_to_agent(port, f"/buffer?device_id={device_id}")
            self._json(200, result)

        elif parsed.path == "/status":
            self._json(200, {"status": "ok", "agents": len(discover_agents())})

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
                sys.stderr.write(f"[dashboard] PAIR REQUEST: {device_id[:8]}... from {info[:60]}\n")
                sys.stderr.flush()
            self._json(200, {"status": "pending"})

        elif parsed.path.startswith("/api/agents/") and "/send" in parsed.path:
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            name = parsed.path.split("/api/agents/")[1].split("/send")[0]
            port = self._get_agent_port(name)
            if not port:
                self._json(404, {"error": "agent not found"})
                return
            result = proxy_to_agent(port, "/send", method="POST", body=body)
            self._json(200, result)

        elif parsed.path.startswith("/api/agents/") and "/key" in parsed.path:
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            name = parsed.path.split("/api/agents/")[1].split("/key")[0]
            port = self._get_agent_port(name)
            if not port:
                self._json(404, {"error": "agent not found"})
                return
            result = proxy_to_agent(port, "/key", method="POST", body=body)
            self._json(200, result)

        elif parsed.path.startswith("/api/agents/") and "/upload" in parsed.path:
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            name = parsed.path.split("/api/agents/")[1].split("/upload")[0]
            port = self._get_agent_port(name)
            if not port:
                self._json(404, {"error": "agent not found"})
                return
            result = proxy_to_agent(port, "/upload", method="POST", body=body)
            self._json(200, result)

        elif parsed.path.startswith("/api/agents/") and "/manage" in parsed.path:
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            name = parsed.path.split("/api/agents/")[1].split("/manage")[0]
            action = body.get("action", "")
            if action not in ("start", "stop", "restart"):
                self._json(400, {"error": f"Invalid action: {action}"})
                return
            ok, output = run_kroagent_cmd(action, name)
            self._json(200, {"success": ok, "output": output, "action": action, "agent": name})

        else:
            self.send_response(404)
            self.end_headers()


def main():
    _load_devices()
    bind = os.environ.get("DASHBOARD_BIND", "127.0.0.1")
    server = HTTPServer((bind, PORT), DashboardHandler)
    print(f"[kroagent-dashboard] Listening on {bind}:{PORT}, agents_dir={KROAGENTS_DIR}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
