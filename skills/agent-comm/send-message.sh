#!/bin/bash
# send-message.sh — Send a message to another KroAgent.
#
# Usage: send-message.sh [--back] <agent-name> <message>
#
# By default uses frontchannel (visible conversation).
# Use --back for backchannel (invisible, silent).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse --back flag
MODE_FLAG=""
if [ "${1:-}" = "--back" ]; then
    MODE_FLAG="--back"
    shift
fi

[ $# -ge 2 ] || { echo "Usage: $0 [--back] <target-agent> <message>" >&2; echo "  Env: KROAGENT_SENDER — name of the sending agent (required)" >&2; exit 1; }

TARGET="$1"
shift
MESSAGE="$*"
SENDER="${KROAGENT_SENDER:-unknown}"

exec python3 "$SCRIPT_DIR/send-message.py" \
    --sender "$SENDER" \
    $MODE_FLAG \
    "$TARGET" \
    "$MESSAGE"
