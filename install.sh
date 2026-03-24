#!/bin/bash
# install.sh — Install the KroAgent framework
#
# Usage: curl -fsSL <url>/install.sh | bash
#   or:  ./install.sh
#
# Run as root. Installs all dependencies, generates TLS certs,
# configures nginx, and starts the dashboard.

set -euo pipefail

INSTALL_DIR="${KROAGENT_INSTALL_DIR:-$HOME/kroagents}"
CERTS_DIR="$HOME/.config/kroagents/certs"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${BLUE}[kroagent]${NC} $*"; }
ok()    { echo -e "${GREEN}[kroagent]${NC} $*"; }
warn()  { echo -e "${YELLOW}[kroagent]${NC} $*"; }
error() { echo -e "${RED}[kroagent]${NC} $*" >&2; }

# --- Domain ---
echo ""
echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║        KroAgent Installer            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

read -p "Enter dashboard domain (default: kroagent.local): " DOMAIN
DOMAIN="${DOMAIN:-kroagent.local}"
DASHBOARD_DOMAIN="kroagent-dashboard.${DOMAIN}"

info "Domain: $DOMAIN"
info "Dashboard: $DASHBOARD_DOMAIN"
echo ""

# --- Check root ---
if [ "$(id -u)" -ne 0 ]; then
    error "This script must be run as root (sudo ./install.sh)"
    exit 1
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")

# Adjust paths for real user
INSTALL_DIR="${REAL_HOME}/kroagents"
CERTS_DIR="${REAL_HOME}/.config/kroagents/certs"

# --- Install dependencies ---
info "Installing dependencies..."
apt-get update -qq
apt-get install -y -qq tmux nginx openssl python3 curl > /dev/null 2>&1
ok "Dependencies installed"

# --- Install Node.js and Claude Code CLI ---
if ! command -v node >/dev/null 2>&1; then
    info "Installing Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
    apt-get install -y -qq nodejs > /dev/null 2>&1
    ok "Node.js installed"
fi

if ! command -v claude >/dev/null 2>&1; then
    info "Installing Claude Code CLI..."
    # Install as the real user
    su - "$REAL_USER" -c "mkdir -p ~/.npm-global && npm config set prefix ~/.npm-global && npm install -g @anthropic-ai/claude-code" > /dev/null 2>&1
    ok "Claude Code CLI installed"
fi

# --- Create directory structure ---
info "Setting up KroAgent directory..."
su - "$REAL_USER" -c "mkdir -p '$INSTALL_DIR'/{web,skills,templates}"

# --- Copy files ---
if [ -d "$SCRIPT_DIR/bin" ]; then
    # Installing from source
    cp "$SCRIPT_DIR/bin/kroagent" "$INSTALL_DIR/kroagent"
    cp "$SCRIPT_DIR/web/kroagent_server.py" "$INSTALL_DIR/web/kroagent_server.py"
    cp "$SCRIPT_DIR/web/dashboard_server.py" "$INSTALL_DIR/web/dashboard_server.py"
    cp "$SCRIPT_DIR/web/setup_server.py" "$INSTALL_DIR/web/setup_server.py"
    if [ -d "$SCRIPT_DIR/skills" ]; then
        cp -r "$SCRIPT_DIR/skills/"* "$INSTALL_DIR/skills/" 2>/dev/null || true
    fi
    if [ -d "$SCRIPT_DIR/templates" ]; then
        cp -r "$SCRIPT_DIR/templates/"* "$INSTALL_DIR/templates/" 2>/dev/null || true
    fi
else
    error "Source files not found. Run from the kroagent source directory."
    exit 1
fi

chmod +x "$INSTALL_DIR/kroagent"
chown -R "$REAL_USER:$REAL_USER" "$INSTALL_DIR"

# Symlink to PATH
ln -sf "$INSTALL_DIR/kroagent" /usr/local/bin/kroagent
ok "KroAgent files installed to $INSTALL_DIR"

# --- Generate TLS certificates ---
info "Generating TLS certificates..."
su - "$REAL_USER" -c "mkdir -p '$CERTS_DIR'"

# Generate CA
if [ ! -f "$CERTS_DIR/kroagent-ca.pem" ]; then
    openssl genrsa -out "$CERTS_DIR/kroagent-ca-key.pem" 4096 2>/dev/null
    openssl req -x509 -new -nodes \
        -key "$CERTS_DIR/kroagent-ca-key.pem" \
        -sha256 -days 3650 \
        -out "$CERTS_DIR/kroagent-ca.pem" \
        -subj "/CN=KroAgent CA/O=KroAgent" 2>/dev/null
    ok "CA certificate generated"
else
    ok "CA certificate already exists"
fi

# Generate server cert for dashboard
if [ ! -f "$CERTS_DIR/kroagent-server.pem" ]; then
    openssl genrsa -out "$CERTS_DIR/kroagent-server-key.pem" 2048 2>/dev/null

    # Create SAN config
    cat > "$CERTS_DIR/server.cnf" << SANCNF
[req]
default_bits = 2048
prompt = no
distinguished_name = dn
req_extensions = v3_req

[dn]
CN = $DASHBOARD_DOMAIN

[v3_req]
subjectAltName = DNS:$DASHBOARD_DOMAIN,DNS:*.${DOMAIN}
SANCNF

    openssl req -new \
        -key "$CERTS_DIR/kroagent-server-key.pem" \
        -out "$CERTS_DIR/kroagent-server.csr" \
        -config "$CERTS_DIR/server.cnf" 2>/dev/null

    openssl x509 -req \
        -in "$CERTS_DIR/kroagent-server.csr" \
        -CA "$CERTS_DIR/kroagent-ca.pem" \
        -CAkey "$CERTS_DIR/kroagent-ca-key.pem" \
        -CAcreateserial \
        -out "$CERTS_DIR/kroagent-server.pem" \
        -days 825 -sha256 \
        -extensions v3_req \
        -extfile "$CERTS_DIR/server.cnf" 2>/dev/null

    rm -f "$CERTS_DIR/kroagent-server.csr" "$CERTS_DIR/server.cnf"
    ok "Server certificate generated for $DASHBOARD_DOMAIN"
else
    ok "Server certificate already exists"
fi

chown -R "$REAL_USER:$REAL_USER" "$CERTS_DIR"

# --- Configure nginx ---
info "Configuring nginx..."

cat > /etc/nginx/sites-available/kroagent << NGINXEOF
# KroAgent Dashboard — managed by kroagent installer
server {
    listen 443 ssl;
    server_name $DASHBOARD_DOMAIN;
    ssl_certificate $CERTS_DIR/kroagent-server.pem;
    ssl_certificate_key $CERTS_DIR/kroagent-server-key.pem;
    location / {
        proxy_pass http://127.0.0.1:18900;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_read_timeout 300s;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/kroagent /etc/nginx/sites-enabled/kroagent

nginx -t 2>/dev/null || { error "Nginx config test failed"; exit 1; }
systemctl reload nginx
ok "Nginx configured"

# --- Add /etc/hosts entry ---
if ! grep -q "$DASHBOARD_DOMAIN" /etc/hosts; then
    echo "127.0.0.1 $DASHBOARD_DOMAIN" >> /etc/hosts
    ok "Added $DASHBOARD_DOMAIN to /etc/hosts"
else
    ok "$DASHBOARD_DOMAIN already in /etc/hosts"
fi

# --- Save config ---
su - "$REAL_USER" -c "mkdir -p '${REAL_HOME}/.config/kroagents'"
cat > "${REAL_HOME}/.config/kroagents/config.json" << CFGEOF
{
  "domain": "$DOMAIN",
  "dashboard_domain": "$DASHBOARD_DOMAIN",
  "dashboard_port": 18900,
  "certs_dir": "$CERTS_DIR",
  "install_dir": "$INSTALL_DIR"
}
CFGEOF
chown "$REAL_USER:$REAL_USER" "${REAL_HOME}/.config/kroagents/config.json"

# --- Install systemd services ---
info "Setting up systemd services..."

# Dashboard service
sed -e "s|{{USER}}|$REAL_USER|g" \
    -e "s|{{HOME}}|$REAL_HOME|g" \
    -e "s|{{INSTALL_DIR}}|$INSTALL_DIR|g" \
    "$INSTALL_DIR/templates/kroagent-dashboard.service" \
    > /etc/systemd/system/kroagent-dashboard.service

# Setup server service
sed -e "s|{{CERTS_DIR}}|$CERTS_DIR|g" \
    -e "s|{{DASHBOARD_DOMAIN}}|$DASHBOARD_DOMAIN|g" \
    -e "s|{{INSTALL_DIR}}|$INSTALL_DIR|g" \
    "$INSTALL_DIR/templates/kroagent-setup.service" \
    > /etc/systemd/system/kroagent-setup.service

systemctl daemon-reload
systemctl enable --now kroagent-dashboard
systemctl enable --now kroagent-setup
ok "Services installed and started (will survive reboot)"

# --- Done ---
SERVER_IP=$(python3 -c "import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('8.8.8.8',80)); print(s.getsockname()[0]); s.close()" 2>/dev/null || echo "UNKNOWN")

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           KroAgent installed!                    ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Setup page: ${BLUE}http://${SERVER_IP}${NC}"
echo -e "  Dashboard:  ${BLUE}https://${DASHBOARD_DOMAIN}${NC}"
echo ""
echo "  From another machine:"
echo "    1. Visit http://${SERVER_IP} to download the CA cert"
echo "    2. Import the cert into your browser/OS"
echo "    3. Add to /etc/hosts: ${SERVER_IP} ${DASHBOARD_DOMAIN}"
echo "    4. Open https://${DASHBOARD_DOMAIN}"
echo ""
echo "  Create your first agent:"
echo "    kroagent create my-agent"
echo "    Edit ~/kroagents/my-agent/agent.json (set port, description)"
echo "    kroagent start my-agent"
echo ""
