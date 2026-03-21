#!/bin/bash
# send-message.sh — Send a message to another KroAgent and wait for the response.
#
# Usage: send-message.sh <agent-name> <message>
#
# Sends "[sender] message" to the target agent's tmux session,
# polls for completion (prompt reappears), and prints the response.

set -euo pipefail

KROAGENTS_DIR="$HOME/kroagents"

[ $# -ge 2 ] || { echo "Usage: $0 <target-agent> <message>" >&2; echo "  Env: KROAGENT_SENDER — name of the sending agent (required)" >&2; exit 1; }

TARGET="$1"
shift
MESSAGE="$*"
SENDER="${KROAGENT_SENDER:-unknown}"

# Resolve target tmux session from agent.json
AGENT_JSON="$KROAGENTS_DIR/$TARGET/agent.json"
if [ ! -f "$AGENT_JSON" ]; then
    echo "ERROR: Agent '$TARGET' not found (no agent.json)" >&2
    exit 1
fi

TMUX_SESSION=$(python3 -c "import json; print(json.load(open('$AGENT_JSON'))['tmux_session'])")

# Check target session exists
if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "ERROR: Target agent '$TARGET' tmux session '$TMUX_SESSION' is not running" >&2
    exit 1
fi

# Hand off to Python for the send/poll/extract cycle
exec python3 "$(dirname "$0")/send-message.py" "$TMUX_SESSION" "$SENDER" "$TARGET" "$MESSAGE"
