# KroAgents

A lightweight framework for managing persistent, long-running AI coding agents. Each agent is a Claude Code CLI instance running in a tmux session, with a web-based dashboard for monitoring and interacting with all agents from a single interface.

## Features

- **Dashboard** — Multi-pane web UI to monitor and interact with all agents. Send messages, view output, manage lifecycle — all from one screen.
- **Agent Lifecycle** — Start, stop, suspend (save conversation), resume, restart, and delete agents from the dashboard or CLI.
- **Persistent Sessions** — Agents run in tmux sessions that persist across web server restarts. Suspend saves the conversation; resume picks up where you left off.
- **Agent Identity** — Each agent has its own workspace with `SOUL.md` (personality/behavior), `CLAUDE.md` (startup instructions), and `NOTES.md` (institutional knowledge that persists across sessions).
- **Inter-Agent Communication** — Agents can message each other via a file-based protocol with tmux signaling. Supports frontchannel (visible) and backchannel (invisible) modes.
- **Skills Framework** — Reusable skill definitions (markdown + scripts) that agents can discover and use. Global skills shared across all agents, plus per-agent local skills.
- **Drag-and-Drop Layout** — Rearrange dashboard panes by dragging. Order persists in localStorage.
- **Intelligent Auto-Refresh** — Panes poll actively after interaction, slow down when idle, stop after 60 seconds of inactivity.

## Quick Start

### Prerequisites

- Linux (Ubuntu/Debian)
- Root access

### Install

```bash
git clone https://github.com/krollc-git/kroagent.git
cd kroagent
sudo ./install.sh
```

The installer will:
1. Ask for a domain name (default: `kroagent.local`)
2. Install dependencies (tmux, nginx, Node.js, Claude Code CLI)
3. Generate TLS certificates (CA + server cert)
4. Configure nginx to proxy the dashboard on port 443
5. Start the dashboard and setup server

### Connect from Your Machine

1. Visit `http://<server-ip>` — the setup page has everything you need
2. Download and import the CA certificate
3. Add the hosts entry to your `/etc/hosts`
4. Open `https://kroagent-dashboard.<your-domain>`

### Create Your First Agent

From the dashboard, click **+ New Agent**, or from the CLI:

```bash
kroagent create my-agent
```

Edit `~/kroagents/my-agent/agent.json` to set the port and description, then:

```bash
kroagent start my-agent
```

The agent appears in the dashboard. Claude Code will prompt for authentication on first run — complete it in the tmux session (`kroagent attach my-agent`).

## CLI Reference

```
kroagent <command> [args]

Lifecycle:
  start <name>          Start an agent (tmux + web server)
  stop <name>           Stop web server (tmux preserved)
  restart <name>        Restart an agent
  kill <name>           Kill everything (web + tmux)
  suspend <name>        Save conversation and stop
  resume <name>         Resume a suspended agent
  kill-all              Kill all agents

Management:
  create <name>         Scaffold a new agent workspace
  delete <name> [-y]    Permanently delete a stopped agent
  status                Show all agents and their state
  list                  List configured agents
  attach <name>         Attach to agent's tmux session
  approve <name> [id]   Manage device pairing

Dashboard:
  dashboard             Start the dashboard
  dashboard-stop        Stop the dashboard
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   Browser                        │
│         https://kroagent-dashboard.domain        │
└──────────────────────┬──────────────────────────┘
                       │ TLS (nginx)
┌──────────────────────┴──────────────────────────┐
│              Dashboard Server (:18900)            │
│     Device pairing · Agent discovery · Proxy     │
└──┬──────────┬──────────┬──────────┬─────────────┘
   │          │          │          │  localhost
┌──┴──┐  ┌───┴──┐  ┌───┴──┐  ┌───┴──┐
│:port│  │:port │  │:port │  │:port │  Agent Web Servers
└──┬──┘  └──┬───┘  └──┬───┘  └──┬───┘  (no auth, internal)
   │        │        │        │
┌──┴──┐  ┌──┴───┐  ┌──┴───┐  ┌──┴───┐
│tmux │  │tmux  │  │tmux  │  │tmux  │  Claude Code CLI
│sess.│  │sess. │  │sess. │  │sess. │  instances
└─────┘  └──────┘  └──────┘  └──────┘
```

Each agent consists of:
- **tmux session** — Claude Code CLI running with `--dangerously-skip-permissions`
- **Web server** — Lightweight Python HTTP server that proxies to tmux via `send-keys` / `capture-pane`
- **Config** — `agent.json` (name, port, tmux session, workdir)
- **Identity** — `SOUL.md` (behavior), `CLAUDE.md` (startup), `NOTES.md` (institutional knowledge)

The dashboard is the single entry point. It discovers agents from `~/kroagents/*/agent.json`, proxies all requests to their internal web servers, and handles device pairing.

## Agent Communication

Agents can message each other using the `agent-comm` skill:

```bash
# Frontchannel (visible conversation)
KROAGENT_SENDER=agent-a ~/kroagents/skills/agent-comm/send-message.sh agent-b "what branch are you on?"

# Backchannel (invisible, silent)
KROAGENT_SENDER=agent-a ~/kroagents/skills/agent-comm/send-message.sh --back agent-b "FYI: deployed new build"
```

Messages travel via files (`~/kroagents/<agent>/comms/`). Tmux is used only for signaling — content never passes through the terminal.

## Directory Structure

```
~/kroagents/
  kroagent              CLI script
  web/
    dashboard_server.py Dashboard (port 18900)
    kroagent_server.py  Per-agent web server
    setup_server.py     HTTP setup page (port 80)
  skills/               Global skills (all agents)
    agent-comm/         Inter-agent messaging
    panel/              Expert panel discussions
  my-agent/             Agent workspace
    agent.json          Config
    SOUL.md             Identity/behavior
    CLAUDE.md           Startup instructions
    NOTES.md            Institutional knowledge
    skills/             Agent-specific skills
    comms/              Message files
```

## License

MIT
