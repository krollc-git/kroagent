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
    """Run a kroagent CLI command (start/stop/restart/kill). Returns (success, output)."""
    if action not in ("start", "stop", "restart", "kill"):
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


DOMAIN = "morrison.internal"
KROCLAW_IP = "192.168.1.103"
NGINX_CONFIG = "/etc/nginx/sites-enabled/kroclaw"
DNS_ZONE_FILE = "/etc/bind/zones/db.morrison.internal"


def next_available_port():
    """Find the next available port by scanning agent configs."""
    used_ports = set()
    for d in KROAGENTS_DIR.iterdir():
        config_file = d / "agent.json"
        if config_file.is_file():
            try:
                config = json.loads(config_file.read_text())
                p = config.get("port", 0)
                if p:
                    used_ports.add(p)
            except (json.JSONDecodeError, OSError):
                pass
    # Start at 18880, increment by 10
    port = 18880
    while port in used_ports:
        port += 10
    return port


def create_agent(name, description, port, workdir):
    """Create a new agent end-to-end. Returns list of step results."""
    steps = []

    # Validate name
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_-]*$', name):
        return [{"step": "validate", "ok": False, "msg": "Name must start with a letter and contain only letters, numbers, hyphens, underscores"}]
    if (KROAGENTS_DIR / name / "agent.json").exists():
        return [{"step": "validate", "ok": False, "msg": f"Agent '{name}' already exists"}]

    # Step 1: Create workspace via CLI
    try:
        env = {**os.environ, "HOME": str(Path.home()), "KROAGENT_DOMAIN": DOMAIN}
        result = subprocess.run(
            [KROAGENT_CLI, "create", name],
            capture_output=True, text=True, timeout=15, env=env
        )
        ok = result.returncode == 0
        steps.append({"step": "create", "ok": ok, "msg": (result.stdout + result.stderr).strip()})
        if not ok:
            return steps
    except Exception as e:
        steps.append({"step": "create", "ok": False, "msg": str(e)})
        return steps

    # Step 2: Update agent.json with port, description, workdir
    try:
        config_file = KROAGENTS_DIR / name / "agent.json"
        config = json.loads(config_file.read_text())
        config["port"] = port
        config["description"] = description
        config["workdir"] = workdir
        config["domain"] = f"{name}.{DOMAIN}"
        config_file.write_text(json.dumps(config, indent=2) + "\n")
        steps.append({"step": "config", "ok": True, "msg": f"Set port={port}, workdir={workdir}"})
    except Exception as e:
        steps.append({"step": "config", "ok": False, "msg": str(e)})
        return steps

    # Step 3: Add DNS record on dhcprouter
    try:
        # Check if record already exists
        check = subprocess.run(
            ["ssh", "dhcprouter", f"grep -q '^{name}' {DNS_ZONE_FILE}"],
            capture_output=True, timeout=10
        )
        if check.returncode == 0:
            steps.append({"step": "dns", "ok": True, "msg": "DNS record already exists"})
        else:
            # Get current serial, increment it
            get_serial = subprocess.run(
                ["ssh", "dhcprouter", f"grep -oP '\\d{{10}}(?=\\s+; Serial)' {DNS_ZONE_FILE}"],
                capture_output=True, text=True, timeout=10
            )
            old_serial = get_serial.stdout.strip()
            today = time.strftime("%Y%m%d")
            if old_serial.startswith(today):
                seq = int(old_serial[-2:]) + 1
                new_serial = f"{today}{seq:02d}"
            else:
                new_serial = f"{today}01"

            # Add A record and update serial
            dns_entry = f"{name}     IN      A       {KROCLAW_IP}"
            cmd = (
                f"sudo sed -i 's/{old_serial}/{new_serial}/' {DNS_ZONE_FILE} && "
                f"echo '{dns_entry}' | sudo tee -a {DNS_ZONE_FILE} > /dev/null && "
                f"sudo systemctl reload bind9"
            )
            result = subprocess.run(
                ["ssh", "dhcprouter", cmd],
                capture_output=True, text=True, timeout=15
            )
            ok = result.returncode == 0
            msg = "DNS record added" if ok else (result.stdout + result.stderr).strip()
            steps.append({"step": "dns", "ok": ok, "msg": msg})
            if not ok:
                return steps
    except Exception as e:
        steps.append({"step": "dns", "ok": False, "msg": str(e)})
        return steps

    # Step 4: Add nginx server block
    try:
        # Check if server block already exists
        check = subprocess.run(
            ["grep", "-q", f"server_name {name}.{DOMAIN}", NGINX_CONFIG],
            capture_output=True, timeout=5
        )
        if check.returncode == 0:
            steps.append({"step": "nginx", "ok": True, "msg": "Nginx block already exists"})
        else:
            nginx_block = f"""
server {{
    listen 443 ssl;
    server_name {name}.{DOMAIN};
    ssl_certificate /etc/openclaw/certs/kroclaw.pem;
    ssl_certificate_key /etc/openclaw/certs/kroclaw-key.pem;
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
    }}
}}
"""
            # Append to nginx config
            result = subprocess.run(
                ["sudo", "tee", "-a", NGINX_CONFIG],
                input=nginx_block, capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                steps.append({"step": "nginx", "ok": False, "msg": "Failed to write nginx config"})
                return steps

            # Test config
            test = subprocess.run(
                ["sudo", "nginx", "-t"],
                capture_output=True, text=True, timeout=10
            )
            if test.returncode != 0:
                steps.append({"step": "nginx", "ok": False, "msg": f"Nginx test failed: {test.stderr}"})
                return steps

            # Reload
            reload_result = subprocess.run(
                ["sudo", "systemctl", "reload", "nginx"],
                capture_output=True, text=True, timeout=10
            )
            ok = reload_result.returncode == 0
            msg = "Nginx configured and reloaded" if ok else reload_result.stderr.strip()
            steps.append({"step": "nginx", "ok": ok, "msg": msg})
            if not ok:
                return steps
    except Exception as e:
        steps.append({"step": "nginx", "ok": False, "msg": str(e)})
        return steps

    # Step 5: Start the agent
    try:
        ok, output = run_kroagent_cmd("start", name)
        steps.append({"step": "start", "ok": ok, "msg": output})
    except Exception as e:
        steps.append({"step": "start", "ok": False, "msg": str(e)})

    return steps


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

/* Fullscreen pane */
#grid.has-fullscreen { grid-template-columns: 1fr !important; grid-template-rows: 1fr !important; }
#grid.has-fullscreen .pane:not(.fullscreen) { display: none; }
#grid.has-fullscreen .pane.fullscreen { border-radius: 0; border: none; }
.pane.fullscreen .pane-terminal { font-size: 13px; line-height: 1.5; padding: 12px 16px; }
.pane.fullscreen .pane-input textarea { font-size: 14px; min-height: 40px; max-height: 200px; padding: 8px 12px; }
.pane.fullscreen .pane-header { padding: 8px 12px; }
.pane.fullscreen .pane-header .agent-name { font-size: 14px; }
.pane-header .max-btn { font-size: 12px; }

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

/* Modal overlays */
#modal-overlay, .modal-overlay-generic {
  display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.7); z-index: 100;
  justify-content: center; align-items: center;
}
#modal-overlay.visible, .modal-overlay-generic.visible { display: flex; }

/* Create agent modal */
#create-modal {
  background: #161b22; border: 1px solid #30363d; border-radius: 10px;
  padding: 24px; width: 480px; max-width: 90vw;
}
#create-modal h2 { color: #58a6ff; font-size: 16px; margin-bottom: 16px; }
#create-modal label { display: block; color: #8b949e; font-size: 12px; margin-bottom: 4px; margin-top: 12px; }
#create-modal input {
  width: 100%; background: #0d1117; border: 1px solid #30363d; color: #c9d1d9;
  padding: 8px 10px; border-radius: 6px; font-family: inherit; font-size: 13px; outline: none;
}
#create-modal input:focus { border-color: #58a6ff; }
#create-modal .hint { font-size: 11px; color: #484f58; margin-top: 2px; }
#create-modal .modal-buttons { margin-top: 20px; display: flex; gap: 8px; justify-content: flex-end; }
#create-modal .modal-buttons button {
  padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 500; border: none;
}
#create-modal .btn-create { background: #238636; color: white; }
#create-modal .btn-create:hover { background: #2ea043; }
#create-modal .btn-create:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }
#create-modal .btn-cancel { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; }
#create-modal .btn-cancel:hover { background: #30363d; }
#create-steps {
  margin-top: 16px; display: none;
}
#create-steps .step {
  display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px;
}
#create-steps .step .icon { width: 16px; text-align: center; }
#create-steps .step.ok .icon { color: #4ade80; }
#create-steps .step.fail .icon { color: #f87171; }
#create-steps .step.pending .icon { color: #fbbf24; }
#create-steps .step .msg { color: #8b949e; }
#topbar .btn-new-agent {
  background: #238636; color: white; border: none; font-weight: 600;
}
#topbar .btn-new-agent:hover { background: #2ea043; }
#topbar .btn-start-agent {
  background: #1f6feb; color: white; border: none; font-weight: 600;
}
#topbar .btn-start-agent:hover { background: #388bfd; }

/* Start agent modal */
#start-modal {
  background: #161b22; border: 1px solid #30363d; border-radius: 10px;
  padding: 24px; width: 400px; max-width: 90vw;
}
#start-modal h2 { color: #58a6ff; font-size: 16px; margin-bottom: 16px; }
#start-modal .agent-list { max-height: 300px; overflow-y: auto; }
#start-modal .agent-item {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border: 1px solid #30363d; border-radius: 6px;
  margin-bottom: 6px; cursor: pointer; transition: border-color 0.15s;
}
#start-modal .agent-item:hover { border-color: #58a6ff; }
#start-modal .agent-item .agent-info { flex: 1; }
#start-modal .agent-item .agent-item-name { color: #c9d1d9; font-size: 13px; font-weight: 600; }
#start-modal .agent-item .agent-item-desc { color: #8b949e; font-size: 11px; margin-top: 2px; }
#start-modal .agent-item .agent-item-btn {
  background: #238636; color: white; border: none;
  padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 500;
}
#start-modal .agent-item .agent-item-btn:hover { background: #2ea043; }
#start-modal .agent-item .agent-item-btn:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }
#start-modal .no-agents { color: #8b949e; font-size: 13px; text-align: center; padding: 20px; }
#start-modal .modal-buttons { margin-top: 16px; display: flex; justify-content: flex-end; }
#start-modal .btn-cancel { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }
#start-modal .btn-cancel:hover { background: #30363d; }

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
    <button class="btn-new-agent" onclick="openCreateModal()">+ New Agent</button>
    <button class="btn-start-agent" onclick="openStartModal()">Start Agent</button>
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
  <p>Create agents with <code>kroagent create &lt;name&gt;</code> on kroclaw, or click <b>+ New Agent</b> above.</p>
</div>

<div id="modal-overlay" onclick="if(event.target===this)closeCreateModal()">
  <div id="create-modal">
    <h2>Create New Agent</h2>
    <label for="ca-name">Name</label>
    <input id="ca-name" placeholder="my-agent" oninput="onNameInput()">
    <div class="hint">Letters, numbers, hyphens, underscores. Must start with a letter.</div>
    <label for="ca-desc">Description</label>
    <input id="ca-desc" placeholder="What this agent does">
    <label for="ca-port">Port</label>
    <input id="ca-port" type="number" placeholder="auto">
    <div class="hint">Leave blank for next available port.</div>
    <label for="ca-workdir">Working Directory</label>
    <input id="ca-workdir" placeholder="auto (~/kroagents/name)">
    <div class="hint">Leave blank for default.</div>
    <div id="create-steps"></div>
    <div class="modal-buttons">
      <button class="btn-cancel" onclick="closeCreateModal()">Cancel</button>
      <button class="btn-create" id="btn-do-create" onclick="doCreateAgent()">Create Agent</button>
    </div>
  </div>
</div>

<div id="start-overlay" class="modal-overlay-generic" onclick="if(event.target===this)closeStartModal()">
  <div id="start-modal">
    <h2>Start Agent</h2>
    <div class="agent-list" id="start-agent-list">
      <div class="no-agents">Loading...</div>
    </div>
    <div class="modal-buttons">
      <button class="btn-cancel" onclick="closeStartModal()">Close</button>
    </div>
  </div>
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
    const allAgents = data.agents || [];
    const newAgents = allAgents.filter(a => a.tmux || a.web);
    const newNames = newAgents.map(a => a.name).join(',');
    const oldNames = agents.map(a => a.name).join(',');
    agents = newAgents;
    document.getElementById('agent-count').textContent = agents.length + ' agent' + (agents.length !== 1 ? 's' : '');
    if (newNames !== oldNames) {
      renderGrid();
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
        <span class="agent-name" ondblclick="toggleFullscreen('${name}')" title="Double-click to maximize">${escapeHtml(name)}</span>
        <div class="pane-controls">
          <button onclick="confirmStop('${name}')" id="stop-btn-${name}" class="mgmt-btn stop-btn" title="Stop agent (kills session)">Stop</button>
          <button onclick="manageAgent('${name}','restart')" id="restart-btn-${name}" class="mgmt-btn restart-btn" title="Restart agent">Restart</button>
          <span class="sep">|</span>
          <button onclick="sendAgentKey('${name}','Escape')" title="Escape">Esc</button>
          <button onclick="sendAgentKey('${name}','C-c')" title="Ctrl+C">^C</button>
          <button onclick="sendAgentKey('${name}','Enter')" title="Enter">↵</button>
          <button onclick="sendAgentKey('${name}','Space')" title="Space">⎵</button>
          <button onclick="refreshPane('${name}')" title="Refresh">↻</button>
          <button onclick="togglePaneAutoRefresh('${name}')" id="auto-btn-${name}" title="Toggle auto-refresh">Auto: ON</button>
          <button onclick="toggleFullscreen('${name}')" id="max-btn-${name}" class="max-btn" title="Maximize/minimize">&#x26F6;</button>
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

// --- Fullscreen ---
let fullscreenAgent = null;

function toggleFullscreen(name) {
  const grid = document.getElementById('grid');
  const pane = document.getElementById('pane-' + name);
  if (!pane) return;

  if (fullscreenAgent === name) {
    // Exit fullscreen
    pane.classList.remove('fullscreen');
    grid.classList.remove('has-fullscreen');
    fullscreenAgent = null;
  } else {
    // Exit previous fullscreen if any
    if (fullscreenAgent) {
      const prev = document.getElementById('pane-' + fullscreenAgent);
      if (prev) prev.classList.remove('fullscreen');
    }
    // Enter fullscreen
    pane.classList.add('fullscreen');
    grid.classList.add('has-fullscreen');
    fullscreenAgent = name;
    // Focus the input
    const input = document.getElementById('input-' + name);
    if (input) input.focus();
    // Scroll to bottom
    const term = document.getElementById('term-' + name);
    if (term && !paneStates[name]?.userScrolled) {
      term.scrollTop = term.scrollHeight;
    }
  }
}

// Fullscreen Escape is handled by the unified Escape handler below

// --- Agent management ---
async function manageAgent(name, action) {
  const btn = document.getElementById(action + '-btn-' + name);
  if (btn) { btn.disabled = true; btn.classList.add('running'); btn.textContent = action + '...'; }

  // Disable all mgmt buttons for this agent during the operation
  ['stop', 'restart'].forEach(a => {
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

  // Wait a moment for processes to settle, then refresh
  const delay = action === 'start' ? 3000 : action === 'kill' ? 3000 : 1000;
  setTimeout(async () => {
    await loadAgents();
    if (action !== 'kill') refreshPane(name);
    // For kill, do a second check in case tmux was slow to die
    if (action === 'kill') {
      setTimeout(() => loadAgents(), 3000);
    }
  }, delay);
}

function confirmStop(name) {
  if (!confirm(`Stop ${name}? This will kill the tmux session and Claude Code instance. The agent will disappear from the dashboard.`)) return;

  // Exit fullscreen if this pane is maximized
  if (fullscreenAgent === name) toggleFullscreen(name);

  // Stop auto-refresh for this pane immediately
  if (paneStates[name] && paneStates[name].refreshTimer) {
    clearInterval(paneStates[name].refreshTimer);
    paneStates[name].refreshTimer = null;
  }

  manageAgent(name, 'kill');
}

// --- Start agent modal ---
async function openStartModal() {
  const overlay = document.getElementById('start-overlay');
  const list = document.getElementById('start-agent-list');
  overlay.classList.add('visible');
  list.innerHTML = '<div class="no-agents">Loading...</div>';

  try {
    const resp = await fetch('/api/stopped-agents?device_id=' + deviceId);
    const data = await resp.json();
    const stopped = data.agents || [];

    if (stopped.length === 0) {
      list.innerHTML = '<div class="no-agents">All agents are already running.</div>';
      return;
    }

    list.innerHTML = stopped.map(a => `
      <div class="agent-item" id="start-item-${a.name}">
        <div class="agent-info">
          <div class="agent-item-name">${escapeHtml(a.name)}</div>
          <div class="agent-item-desc">${escapeHtml(a.description || '')} (port ${a.port})</div>
        </div>
        <button class="agent-item-btn" id="start-agent-btn-${a.name}" onclick="startAgentFromModal('${a.name}')">Start</button>
      </div>
    `).join('');
  } catch(e) {
    list.innerHTML = '<div class="no-agents">Error loading agents.</div>';
  }
}

function closeStartModal() {
  document.getElementById('start-overlay').classList.remove('visible');
}

async function startAgentFromModal(name) {
  const btn = document.getElementById('start-agent-btn-' + name);
  if (btn) { btn.disabled = true; btn.textContent = 'Starting...'; }

  try {
    const resp = await fetch(`/api/agents/${name}/manage`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'start', device_id: deviceId})
    });
    const data = await resp.json();
    if (data.success) {
      if (btn) { btn.textContent = 'Started'; }
      // Reload agents after a delay so the new pane appears
      setTimeout(async () => {
        await loadAgents();
      }, 3000);
    } else {
      if (btn) { btn.textContent = 'Failed'; btn.disabled = false; }
      console.error(`Start ${name} failed:`, data.output);
    }
  } catch(e) {
    if (btn) { btn.textContent = 'Error'; btn.disabled = false; }
  }
}

// --- Create agent modal ---
const STEP_LABELS = {
  validate: 'Validate',
  create: 'Create workspace',
  config: 'Configure agent',
  dns: 'Add DNS record',
  nginx: 'Configure nginx',
  start: 'Start agent'
};
const ALL_STEPS = ['create', 'config', 'dns', 'nginx', 'start'];

async function openCreateModal() {
  document.getElementById('modal-overlay').classList.add('visible');
  document.getElementById('ca-name').value = '';
  document.getElementById('ca-desc').value = '';
  document.getElementById('ca-workdir').value = '';
  document.getElementById('create-steps').style.display = 'none';
  document.getElementById('create-steps').innerHTML = '';
  document.getElementById('btn-do-create').disabled = false;
  document.getElementById('btn-do-create').textContent = 'Create Agent';
  // Fetch next available port
  try {
    const resp = await fetch('/api/next-port?device_id=' + deviceId);
    const data = await resp.json();
    document.getElementById('ca-port').value = data.port || '';
  } catch(e) {}
  document.getElementById('ca-name').focus();
}

function closeCreateModal() {
  document.getElementById('modal-overlay').classList.remove('visible');
}

function onNameInput() {
  const name = document.getElementById('ca-name').value.trim();
  const workdir = document.getElementById('ca-workdir');
  if (!workdir.dataset.userEdited) {
    workdir.placeholder = name ? `~/kroagents/${name}` : 'auto (~/kroagents/name)';
  }
}

async function doCreateAgent() {
  const name = document.getElementById('ca-name').value.trim();
  const desc = document.getElementById('ca-desc').value.trim();
  const port = document.getElementById('ca-port').value.trim();
  const workdir = document.getElementById('ca-workdir').value.trim();

  if (!name) { document.getElementById('ca-name').focus(); return; }

  const btn = document.getElementById('btn-do-create');
  btn.disabled = true;
  btn.textContent = 'Creating...';

  // Show pending steps
  const stepsEl = document.getElementById('create-steps');
  stepsEl.style.display = 'block';
  stepsEl.innerHTML = ALL_STEPS.map(s =>
    `<div class="step pending" id="step-${s}"><span class="icon">&#x25CB;</span><span>${STEP_LABELS[s]}</span><span class="msg"></span></div>`
  ).join('');

  try {
    const resp = await fetch('/api/agents/create', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: name,
        description: desc,
        port: port ? parseInt(port) : 0,
        workdir: workdir,
        device_id: deviceId
      })
    });
    const data = await resp.json();

    if (data.error) {
      stepsEl.innerHTML = `<div class="step fail"><span class="icon">&#x2717;</span><span>${escapeHtml(data.error)}</span></div>`;
      btn.disabled = false;
      btn.textContent = 'Create Agent';
      return;
    }

    // Update step indicators from results
    const completed = new Set();
    for (const step of (data.steps || [])) {
      completed.add(step.step);
      const el = document.getElementById('step-' + step.step);
      if (el) {
        el.className = 'step ' + (step.ok ? 'ok' : 'fail');
        el.querySelector('.icon').innerHTML = step.ok ? '&#x2713;' : '&#x2717;';
        el.querySelector('.msg').textContent = step.msg || '';
      }
    }
    // Mark remaining steps as skipped if there was a failure
    if (!data.success) {
      for (const s of ALL_STEPS) {
        if (!completed.has(s)) {
          const el = document.getElementById('step-' + s);
          if (el) {
            el.className = 'step fail';
            el.querySelector('.icon').innerHTML = '&#x2014;';
            el.querySelector('.msg').textContent = 'skipped';
          }
        }
      }
      btn.disabled = false;
      btn.textContent = 'Retry';
    } else {
      btn.textContent = 'Done';
      // Reload agents list after a moment
      setTimeout(async () => {
        await loadAgents();
      }, 2000);
    }
  } catch(e) {
    stepsEl.innerHTML = `<div class="step fail"><span class="icon">&#x2717;</span><span>Request failed: ${escapeHtml(e.message)}</span></div>`;
    btn.disabled = false;
    btn.textContent = 'Retry';
  }
}

// Unified Escape handler (priority: start modal > create modal > fullscreen)
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const startOverlay = document.getElementById('start-overlay');
  if (startOverlay && startOverlay.classList.contains('visible')) {
    closeStartModal(); return;
  }
  const createOverlay = document.getElementById('modal-overlay');
  if (createOverlay && createOverlay.classList.contains('visible')) {
    closeCreateModal(); return;
  }
  if (fullscreenAgent) toggleFullscreen(fullscreenAgent);
});

// Track if user manually edited workdir
document.getElementById('ca-workdir')?.addEventListener('input', function() {
  this.dataset.userEdited = this.value.trim() ? 'true' : '';
});

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

        elif parsed.path == "/api/stopped-agents":
            device_id = qs.get("device_id", [""])[0]
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            agents = discover_agents()
            stopped = []
            for a in agents:
                name = a.get("name", "")
                if not name:
                    continue
                status = agent_status(name)
                if not status["tmux"] and not status["web"]:
                    stopped.append({
                        "name": name,
                        "description": a.get("description", ""),
                        "port": a.get("port", 0),
                    })
            self._json(200, {"agents": stopped})

        elif parsed.path == "/api/next-port":
            device_id = qs.get("device_id", [""])[0]
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            self._json(200, {"port": next_available_port()})

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

        elif parsed.path == "/api/agents/create":
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            name = body.get("name", "").strip()
            description = body.get("description", "").strip() or f"{name} KroAgent"
            port = body.get("port", 0)
            workdir = body.get("workdir", "").strip()
            if not name:
                self._json(400, {"error": "Name is required"})
                return
            if not port:
                port = next_available_port()
            if not workdir:
                workdir = str(KROAGENTS_DIR / name)
            steps = create_agent(name, description, int(port), workdir)
            all_ok = all(s["ok"] for s in steps)
            self._json(200, {"success": all_ok, "steps": steps, "agent": name, "port": port})

        elif parsed.path.startswith("/api/agents/") and "/manage" in parsed.path:
            device_id = body.get("device_id", "")
            if not _is_paired(device_id):
                self._json(403, {"error": "not paired"})
                return
            name = parsed.path.split("/api/agents/")[1].split("/manage")[0]
            action = body.get("action", "")
            if action not in ("start", "stop", "restart", "kill"):
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
