#!/usr/bin/env python3
"""Send a message to another KroAgent via tmux and capture the response."""

import subprocess
import sys
import time

POLL_INTERVAL = 3
STALE_TIMEOUT = 120  # seconds of no buffer change + no prompt = give up
PROMPT_CHAR = "\u276f"  # ❯


def capture_pane(session: str) -> str:
    result = subprocess.run(
        ["tmux", "capture-pane", "-t", session, "-p", "-S", "-2000"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout


def send_keys(session: str, text: str):
    subprocess.run(
        ["tmux", "send-keys", "-t", session, text, "Enter"],
        capture_output=True, text=True, timeout=10,
    )


def has_prompt(buffer: str) -> bool:
    """Check if the Claude Code prompt (❯) is at the bottom of the buffer."""
    lines = buffer.rstrip().split("\n")
    # Walk backwards from bottom, skipping empty lines and status bar
    for line in reversed(lines[-10:]):
        stripped = line.strip()
        if not stripped:
            continue
        # Status bar line (contains bypass, permissions, etc.)
        if "bypass" in stripped or "permissions" in stripped:
            continue
        # Separator line (all ─ characters)
        if len(stripped) > 10 and all(c == "\u2500" for c in stripped):
            continue
        # Prompt line
        if stripped.startswith(PROMPT_CHAR) and len(stripped) <= 3:
            return True
        # Something else — not at prompt
        return False
    return False


def extract_response(buffer: str, sender: str, message: str) -> str:
    """Extract the response from the buffer after our sent message."""
    lines = buffer.split("\n")

    # Find the last occurrence of our sent message
    marker = f"[{sender}]"
    start_idx = None
    for i, line in enumerate(lines):
        if marker in line:
            start_idx = i

    if start_idx is None:
        return buffer.strip()

    # Find the end: walk backwards from bottom to find the separator/prompt area
    end_idx = len(lines)
    for i in range(len(lines) - 1, start_idx, -1):
        stripped = lines[i].strip()
        if not stripped:
            end_idx = i
            continue
        if "bypass" in stripped or "permissions" in stripped:
            end_idx = i
            continue
        if len(stripped) > 10 and all(c == "\u2500" for c in stripped):
            end_idx = i
            continue
        if stripped.startswith(PROMPT_CHAR) and len(stripped) <= 3:
            end_idx = i
            continue
        break

    # Extract lines between our message and the prompt area
    response_lines = lines[start_idx + 1 : end_idx]
    return "\n".join(response_lines).strip()


def main():
    if len(sys.argv) != 5:
        print("Usage: send-message.py <tmux-session> <sender> <target> <message>", file=sys.stderr)
        sys.exit(1)

    session = sys.argv[1]
    sender = sys.argv[2]
    target = sys.argv[3]
    message = sys.argv[4]

    # Snapshot before sending
    before = capture_pane(session)

    # Send the message
    formatted = f"[{sender}] {message}"
    send_keys(session, formatted)

    # Wait a moment for the agent to start processing
    time.sleep(2)

    # Poll for completion
    last_buffer = before
    stale_seconds = 0

    while True:
        time.sleep(POLL_INTERVAL)
        current = capture_pane(session)

        # Check if the prompt has reappeared AND the buffer has changed
        # (meaning the agent processed our message and is now idle)
        if current != before and has_prompt(current):
            # Make sure our message is in the buffer (agent actually processed it)
            if f"[{sender}]" in current:
                break

        # Track staleness
        if current == last_buffer:
            stale_seconds += POLL_INTERVAL
            if stale_seconds >= STALE_TIMEOUT:
                print(f"ERROR: Timed out waiting for response from '{target}' (buffer stale for {STALE_TIMEOUT}s)", file=sys.stderr)
                sys.exit(2)
        else:
            stale_seconds = 0
            last_buffer = current

    # Extract and print the response
    final_buffer = capture_pane(session)
    response = extract_response(final_buffer, sender, message)
    print(response)


if __name__ == "__main__":
    main()
