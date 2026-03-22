#!/usr/bin/env python3
"""Reply to an agent-comm session. Used by the receiving agent.

Writes a reply to the session file in the sender's comms dir,
then signals the sender's tmux session.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

KROAGENTS_DIR = Path(os.environ.get("KROAGENTS_DIR", str(Path.home() / "kroagents")))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Reply to an agent-comm message")
    parser.add_argument("session_id", help="Session ID from the signal")
    parser.add_argument("reply", help="Reply content")
    parser.add_argument("--sender", default=os.environ.get("KROAGENT_SENDER", "unknown"),
                        help="This agent's name")
    args = parser.parse_args()

    # Read the session file from our own comms dir to find who sent the message
    session_file = KROAGENTS_DIR / args.sender / "comms" / f"{args.session_id}.jsonl"
    if not session_file.exists():
        print(f"ERROR: Session {args.session_id} not found in comms dir", file=sys.stderr)
        sys.exit(1)

    messages = []
    for line in session_file.read_text().strip().split("\n"):
        if line.strip():
            messages.append(json.loads(line))

    # Find who sent us the original message
    original = [m for m in messages if m.get("type") == "message"]
    if not original:
        print(f"ERROR: No message found in session {args.session_id}", file=sys.stderr)
        sys.exit(1)

    original_sender = original[0]["from"]

    # Write reply to the SENDER's comms dir (so they can read it)
    sender_comms = KROAGENTS_DIR / original_sender / "comms"
    sender_comms.mkdir(parents=True, exist_ok=True)
    reply_entry = {
        "type": "reply",
        "from": args.sender,
        "content": args.reply,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(sender_comms / f"{args.session_id}.jsonl", "a") as f:
        f.write(json.dumps(reply_entry) + "\n")

    # Signal the sender's tmux
    try:
        config = json.loads((KROAGENTS_DIR / original_sender / "agent.json").read_text())
        tmux_session = config["tmux_session"]
        subprocess.run(
            ["tmux", "send-keys", "-t", tmux_session,
             f"[agent-comm:reply:{args.session_id}]", "Enter"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        pass  # Sender may not be running, reply is still in the file

    print(f"Reply sent to {original_sender}")


if __name__ == "__main__":
    main()
