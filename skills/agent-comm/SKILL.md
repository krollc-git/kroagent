---
name: agent-comm
description: Send messages to other KroAgents and receive their responses via tmux
---

# Agent Communication

Send messages to other KroAgents running on the same machine. Messages are delivered via tmux and responses are captured when the target agent finishes processing.

## When to Use

When the user asks you to communicate with another agent — e.g., "ask kroroku-dev to run the tests", "tell kroroku-dev the API key changed", "check with kroroku-dev what branch it's on".

## How It Works

1. Your message is sent to the target agent's tmux session as `[your-name] message`
2. The target agent sees it as normal input and responds
3. The script polls until the target agent's prompt reappears (meaning it's done)
4. The response is extracted and returned to you

## Sending a Message

Use the helper script:

```bash
KROAGENT_SENDER=<your-agent-name> {baseDir}/send-message.sh <target-agent> <message>
```

Example:
```bash
KROAGENT_SENDER=krogambler ~/kroagents/skills/agent-comm/send-message.sh kroroku-dev "what branch are you on?"
```

## Discovering Other Agents

To see what agents exist:
```bash
ls ~/kroagents/*/agent.json | xargs -I{} bash -c 'echo "$(dirname {} | xargs basename): $(python3 -c "import json; print(json.load(open(\"{}\"))[\"description\"])")"'
```

Or simpler:
```bash
~/kroagents/kroagent list
```

## Important Notes

- **Timeout**: The script waits up to 120 seconds of stale buffer (no output change and no prompt). If the target is actively working (buffer changing), it waits indefinitely.
- **One at a time**: Don't send multiple messages to the same agent simultaneously. Wait for a response before sending the next message.
- **Be specific**: The target agent doesn't have your context. Include enough detail for it to act on your message without needing to ask follow-ups.
- **The target agent sees your name**: Messages arrive as `[krogambler] your message here`, so the other agent knows who's talking.
