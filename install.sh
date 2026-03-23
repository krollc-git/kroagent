#!/bin/bash
# install.sh — Install kroagent framework on a new machine
#
# Usage: ./install.sh [install-dir]
#   install-dir defaults to ~/kroagents
#
# Prerequisites:
#   - Python 3.12+
#   - tmux
#   - Claude Code CLI (npm install -g @anthropic-ai/claude-code)
#   - nginx (for HTTPS proxying)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${1:-$HOME/kroagents}"

echo "Installing kroagent framework to $INSTALL_DIR"

# Create directory structure
mkdir -p "$INSTALL_DIR"/{skills,web}

# Copy core files
cp "$SCRIPT_DIR/bin/kroagent" "$INSTALL_DIR/kroagent"
chmod +x "$INSTALL_DIR/kroagent"

cp "$SCRIPT_DIR/web/kroagent_server.py" "$INSTALL_DIR/web/kroagent_server.py"
cp "$SCRIPT_DIR/web/dashboard_server.py" "$INSTALL_DIR/web/dashboard_server.py"

# Copy bundled skills
if [ -d "$SCRIPT_DIR/skills" ]; then
    cp -r "$SCRIPT_DIR/skills/"* "$INSTALL_DIR/skills/" 2>/dev/null || true
fi

# Symlink to PATH
if [ -w /usr/local/bin ]; then
    ln -sf "$INSTALL_DIR/kroagent" /usr/local/bin/kroagent
    echo "Symlinked kroagent to /usr/local/bin/kroagent"
else
    echo "NOTE: Run 'sudo ln -sf $INSTALL_DIR/kroagent /usr/local/bin/kroagent' to add to PATH"
fi

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Set your domain:  export KROAGENT_DOMAIN=yourdomain.internal"
echo "     (add to ~/.bashrc or ~/.profile to persist)"
echo "  2. Create an agent:  kroagent create <name>"
echo "  3. Edit agent.json to set port and workdir"
echo "  4. Add DNS entry pointing <name>.\$KROAGENT_DOMAIN to this machine"
echo "  5. Add nginx server block proxying to the agent's port"
echo "  6. Start the agent:  kroagent start <name>"
echo "  7. Authenticate Claude Code in the tmux session"
echo ""
echo "Templates are in: $SCRIPT_DIR/templates/"
