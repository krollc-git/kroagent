#!/usr/bin/env python3
"""Send a message to another KroAgent via file-based messaging with tmux signaling.

Messages are stored in JSONL files. Tmux is used only for signaling (handshake),
never for content delivery. Supports two modes:

- frontchannel (default): Conversation is visible in both agents' tmux windows.
  The agents read/write from the message file but display the conversation.
- backchannel: Conversation is invisible. Agents read/write silently.

Protocol:
  1. Sender writes message to target's inbox: ~/kroagents/<target>/comms/<session_id>.jsonl
  2. Sender injects signal into target's tmux: [agent-comm:<mode>:<session_id>]
  3. Target reads message from file, processes it, writes response to same file
  4. Target injects signal into sender's tmux: [agent-comm:reply:<session_id>]
  5. Sender reads response from file
"""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

KROAGENTS_DIR = Path(os.environ.get("KROAGENTS_DIR", str(Path.home() / "kroagents")))
POLL_INTERVAL = 2
STALE_TIMEOUT = 120
PROMPT_CHAR = "\u276f"  # ❯


def get_tmux_session(agent_name: str) -> str:
    config = json.loads((KROAGENTS_DIR / agent_name / "agent.json").read_text())
    return config["tmux_session"]


def comms_dir(agent_name: str) -> Path:
    d = KROAGENTS_DIR / agent_name / "comms"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_message(agent_name: str, session_id: str, sender: str, content: str, msg_type: str = "message"):
    f = comms_dir(agent_name) / f"{session_id}.jsonl"
    entry = {
        "type": msg_type,
        "from": sender,
        "content": content,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(f, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def read_messages(agent_name: str, session_id: str) -> list:
    f = comms_dir(agent_name) / f"{session_id}.jsonl"
    if not f.exists():
        return []
    messages = []
    for line in f.read_text().strip().split("\n"):
        if line.strip():
            messages.append(json.loads(line))
    return messages


def send_signal(tmux_session: str, signal: str):
    """Inject a signal into a tmux session. This is the only tmux content injection."""
    subprocess.run(
        ["tmux", "send-keys", "-t", tmux_session, signal, "Enter"],
        capture_output=True, text=True, timeout=10,
    )


def capture_pane(session: str) -> str:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-S", "-2000"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout


def has_prompt(buffer: str) -> bool:
    lines = buffer.rstrip().split("\n")
    for line in reversed(lines[-10:]):
        stripped = line.strip()
        if not stripped:
            continue
        if "bypass" in stripped or "permissions" in stripped:
            continue
        if len(stripped) > 10 and all(c == "\u2500" for c in stripped):
            continue
        if stripped.startswith(PROMPT_CHAR) and len(stripped) <= 3:
            return True
        return False
    return False


def wait_for_reply(sender: str, session_id: str, timeout: int = STALE_TIMEOUT) -> str | None:
    """Poll the sender's comms dir for a reply message in the session file."""
    start = time.time()
    last_count = 0
    stale_start = time.time()

    while True:
        time.sleep(POLL_INTERVAL)
        messages = read_messages(sender, session_id)
        replies = [m for m in messages if m.get("type") == "reply"]

        if replies:
            return replies[-1]["content"]

        # Check for staleness
        if len(messages) == last_count:
            if time.time() - stale_start >= timeout:
                return None
        else:
            last_count = len(messages)
            stale_start = time.time()


def wait_for_target_prompt(tmux_session: str, session_id: str, sender: str) -> bool:
    """Wait for the target agent to process the signal and return to prompt."""
    last_buffer = ""
    stale_seconds = 0

    # Give the agent a moment to start processing
    time.sleep(2)

    while True:
        time.sleep(POLL_INTERVAL)
        current = capture_pane(tmux_session)

        if current != last_buffer:
            stale_seconds = 0
            last_buffer = current
        else:
            stale_seconds += POLL_INTERVAL

        # Check if prompt reappeared and our signal was processed
        if has_prompt(current) and f"agent-comm:" in current:
            return True

        if stale_seconds >= STALE_TIMEOUT:
            return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Send a message to another KroAgent")
    parser.add_argument("target", help="Target agent name")
    parser.add_argument("message", help="Message to send")
    parser.add_argument("--sender", default=os.environ.get("KROAGENT_SENDER", "unknown"))
    parser.add_argument("--back", action="store_true", help="Use backchannel (invisible)")
    args = parser.parse_args()

    mode = "back" if args.back else "front"
    session_id = uuid.uuid4().hex[:12]

    # Verify target exists and is running
    target_json = KROAGENTS_DIR / args.target / "agent.json"
    if not target_json.exists():
        print(f"ERROR: Agent '{args.target}' not found", file=sys.stderr)
        sys.exit(1)

    target_tmux = get_tmux_session(args.target)
    try:
        r = subprocess.run(["tmux", "has-session", "-t", target_tmux], capture_output=True, timeout=5)
        if r.returncode != 0:
            print(f"ERROR: Agent '{args.target}' tmux session not running", file=sys.stderr)
            sys.exit(1)
    except Exception:
        print(f"ERROR: Cannot check tmux session for '{args.target}'", file=sys.stderr)
        sys.exit(1)

    # Step 1: Write message to target's comms dir
    write_message(args.target, session_id, args.sender, args.message, "message")

    # Also write the session file in sender's comms dir (for receiving the reply)
    write_message(args.sender, session_id, args.sender, args.message, "sent")

    # Step 2: Signal the target
    signal = f"[agent-comm:{mode}:{session_id}]"
    send_signal(target_tmux, signal)

    # Step 3: Wait for reply in our comms dir
    reply = wait_for_reply(args.sender, session_id, STALE_TIMEOUT)

    if reply is None:
        print(f"ERROR: Timed out waiting for response from '{args.target}'", file=sys.stderr)
        sys.exit(2)

    print(reply)


if __name__ == "__main__":
    main()
