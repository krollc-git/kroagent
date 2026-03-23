#!/usr/bin/env python3
"""KroAgent Setup Server — HTTP server on port 80 that serves the CA cert
and setup instructions for connecting to the dashboard.

Runs alongside the dashboard. Users visit http://<server-ip> to get
the CA cert and /etc/hosts instructions.
"""

import json
import os
import socket
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = int(os.environ.get("SETUP_PORT", "80"))
CERTS_DIR = Path(os.environ.get("KROAGENT_CERTS_DIR", str(Path.home() / ".config" / "kroagents" / "certs")))
DASHBOARD_DOMAIN = os.environ.get("KROAGENT_DASHBOARD_DOMAIN", "kroagent-dashboard.local")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "443"))


def get_server_ip():
    """Get the primary IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "UNKNOWN"


def get_ca_cert_path():
    return CERTS_DIR / "kroagent-ca.pem"


SETUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KroAgent Setup</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'SF Mono', 'Monaco', 'Menlo', 'Consolas', monospace;
  background: #0d1117; color: #c9d1d9;
  max-width: 700px; margin: 0 auto; padding: 40px 20px;
}
h1 { color: #58a6ff; font-size: 24px; margin-bottom: 8px; }
.subtitle { color: #8b949e; margin-bottom: 32px; }
.step {
  background: #161b22; border: 1px solid #30363d; border-radius: 8px;
  padding: 20px; margin-bottom: 16px;
}
.step h2 { color: #58a6ff; font-size: 16px; margin-bottom: 12px; }
.step p { color: #8b949e; line-height: 1.6; margin-bottom: 8px; }
.step a {
  color: #58a6ff; text-decoration: none; font-weight: 600;
}
.step a:hover { text-decoration: underline; }
.code-block {
  background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
  padding: 12px; margin: 8px 0; position: relative;
  font-size: 13px; color: #c9d1d9; word-break: break-all;
}
.copy-btn {
  position: absolute; top: 8px; right: 8px;
  background: #21262d; color: #8b949e; border: 1px solid #30363d;
  padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 11px;
}
.copy-btn:hover { background: #30363d; color: #c9d1d9; }
.os-tabs { display: flex; gap: 4px; margin-bottom: 12px; }
.os-tab {
  background: #21262d; color: #8b949e; border: 1px solid #30363d;
  padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px;
}
.os-tab.active { background: #30363d; color: #c9d1d9; border-color: #58a6ff; }
.os-content { display: none; }
.os-content.active { display: block; }
.dashboard-link {
  display: inline-block; background: #238636; color: white;
  padding: 12px 24px; border-radius: 8px; font-size: 16px;
  text-decoration: none; font-weight: 600; margin-top: 8px;
}
.dashboard-link:hover { background: #2ea043; text-decoration: none; }
</style>
</head>
<body>
<h1>KroAgent Setup</h1>
<p class="subtitle">Connect to the KroAgent Dashboard in 3 steps.</p>

<div class="step">
  <h2>Step 1: Download the CA Certificate</h2>
  <p>Download and import this certificate so your browser trusts the dashboard's TLS connection.</p>
  <p><a href="/ca.pem" download="kroagent-ca.pem">Download kroagent-ca.pem</a></p>

  <div class="os-tabs">
    <div class="os-tab active" onclick="showOS('macos')">macOS</div>
    <div class="os-tab" onclick="showOS('windows')">Windows</div>
    <div class="os-tab" onclick="showOS('linux')">Linux</div>
  </div>

  <div class="os-content active" id="os-macos">
    <p>Open the downloaded file, it opens Keychain Access. Add it to the <b>System</b> keychain.
    Then double-click the cert, expand <b>Trust</b>, set <b>Always Trust</b>.</p>
    <p>Or via terminal:</p>
    <div class="code-block">
      sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain kroagent-ca.pem
      <button class="copy-btn" onclick="copyText(this)">Copy</button>
    </div>
  </div>

  <div class="os-content" id="os-windows">
    <p>Double-click the downloaded file. Click <b>Install Certificate</b>.
    Choose <b>Local Machine</b>, then <b>Place all certificates in the following store</b>,
    browse to <b>Trusted Root Certification Authorities</b>. Finish.</p>
  </div>

  <div class="os-content" id="os-linux">
    <div class="code-block">
      sudo cp kroagent-ca.pem /usr/local/share/ca-certificates/kroagent-ca.crt
sudo update-ca-certificates
      <button class="copy-btn" onclick="copyText(this)">Copy</button>
    </div>
    <p>For Chrome/Chromium, also import via <code>chrome://settings/certificates</code> &rarr; Authorities.</p>
  </div>
</div>

<div class="step">
  <h2>Step 2: Add Hosts Entry</h2>
  <p>Add this line to your <code>/etc/hosts</code> file (or <code>C:\\Windows\\System32\\drivers\\etc\\hosts</code> on Windows):</p>
  <div class="code-block" id="hosts-entry">
    SERVER_IP DASHBOARD_DOMAIN
    <button class="copy-btn" onclick="copyText(this)">Copy</button>
  </div>
  <div class="os-tabs">
    <div class="os-tab active" onclick="showHostsOS('hosts-macos')">macOS / Linux</div>
    <div class="os-tab" onclick="showHostsOS('hosts-windows')">Windows</div>
  </div>
  <div class="os-content active" id="hosts-macos">
    <div class="code-block">
      echo "SERVER_IP DASHBOARD_DOMAIN" | sudo tee -a /etc/hosts
      <button class="copy-btn" onclick="copyText(this)">Copy</button>
    </div>
  </div>
  <div class="os-content" id="hosts-windows">
    <p>Run Notepad as Administrator, open <code>C:\\Windows\\System32\\drivers\\etc\\hosts</code>, add the line above, save.</p>
  </div>
</div>

<div class="step">
  <h2>Step 3: Open the Dashboard</h2>
  <p>You're all set. Click below to open the KroAgent Dashboard:</p>
  <a class="dashboard-link" href="DASHBOARD_URL">Open Dashboard &rarr;</a>
</div>

<script>
function showOS(os) {
  document.querySelectorAll('.os-content').forEach(el => {
    if (el.id.startsWith('os-')) el.classList.remove('active');
  });
  document.querySelectorAll('.os-tabs .os-tab').forEach(el => el.classList.remove('active'));
  document.getElementById('os-' + os).classList.add('active');
  event.target.classList.add('active');
}
function showHostsOS(id) {
  document.querySelectorAll('.os-content').forEach(el => {
    if (el.id.startsWith('hosts-')) el.classList.remove('active');
  });
  document.querySelectorAll('.os-tabs .os-tab').forEach(el => el.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  event.target.classList.add('active');
}
function copyText(btn) {
  const block = btn.parentElement;
  const text = block.textContent.replace('Copy', '').trim();
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
  });
}
</script>
</body>
</html>"""


class SetupHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path == "/ca.pem":
            ca_path = get_ca_cert_path()
            if ca_path.exists():
                data = ca_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-pem-file")
                self.send_header("Content-Disposition", "attachment; filename=kroagent-ca.pem")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"CA certificate not found. Run the install script first.")
        else:
            server_ip = get_server_ip()
            dashboard_url = f"https://{DASHBOARD_DOMAIN}"
            if DASHBOARD_PORT != 443:
                dashboard_url += f":{DASHBOARD_PORT}"

            html = SETUP_HTML
            html = html.replace("SERVER_IP", server_ip)
            html = html.replace("DASHBOARD_DOMAIN", DASHBOARD_DOMAIN)
            html = html.replace("DASHBOARD_URL", dashboard_url)

            body = html.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def main():
    server = HTTPServer(("0.0.0.0", PORT), SetupHandler)
    print(f"[kroagent-setup] Setup server on port {PORT}, dashboard={DASHBOARD_DOMAIN}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
