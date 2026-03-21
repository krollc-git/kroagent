---
name: panel
description: Run a moderated expert panel discussion with persistent AI panelists. Supports custom roles, cross-talk, shared storage, Discord output, and reconvening with memory of past sessions.
user_invocable: true
---

# Expert Panel Discussion

Run a moderated panel of expert perspectives on a topic. You (the KroAgent) are the moderator. Panelists are spawned once as persistent agents using the Agent tool and communicated with via SendMessage throughout the session.

## Triggering

When the user says any of: "run a panel", "start a panel", "panel discussion", "convene a panel", "reconvene panel X".

## Phase 1: Discovery

Before running a panel, have a conversation with the user to define:

1. **Topic** — What is the panel discussing? Refine until it's well-scoped.
2. **Panel composition** — Ask: "Use the default panel (Skeptic, Advocate, Domain Expert, Wildcard) or define your own?"
   - If custom, ask the user to name the roles and optionally describe each one.
3. **Rounds** — How many rounds of discussion? Default is 3. Panel can end early if consensus is reached.
4. **Output backend** — "Post to terminal (default) or a Discord channel?" If Discord, ask which channel.
5. **Ground rules** — Any constraints? e.g. "focus on data not opinions", "consider budget constraints"
6. **Panel name** — Give the panel a short name for saving/reconvening (e.g. "nhl-model-review"). Suggest one, let user override.

Once confirmed, save the spec:

```bash
mkdir -p ~/kroagents/$(basename $PWD)/panels/<panel-name>
```

Write `spec.json`:
```json
{
  "name": "nhl-model-review",
  "topic": "Should we remove glicko2 from the consensus model?",
  "roles": [
    {"name": "Skeptic", "prompt": "You challenge assumptions and demand evidence."},
    {"name": "Advocate", "prompt": "You argue in favor of the proposal."},
    {"name": "Domain Expert", "prompt": "You bring deep technical knowledge."},
    {"name": "Wildcard", "prompt": "You think laterally."}
  ],
  "rounds": 3,
  "backend": "terminal",
  "discord_channel_id": null,
  "ground_rules": [],
  "shared_dir": "shared/",
  "created_at": "2026-03-21",
  "sessions": 0
}
```

## Phase 2: Execution

### Spawning Panelists

Spawn each panelist **once** at the start using the Agent tool with a `name` parameter. They persist for the entire session — use `SendMessage` for all subsequent communication.

Each panelist's initial spawn prompt:

```
You are a panelist in an expert discussion. You will be contacted multiple times throughout this session via messages. Each message will be a prompt for a round, a cross-talk question, or a consensus check. Respond to each one in character.

**Your role: {role_name}**
{role_prompt}

**Topic:** {topic}

**Ground rules:**
{ground_rules}

**Source code and reference data** are available in: {panel_dir}/shared/
Read any files you need to inform your analysis. You have full access to the source code, Discord channel history, and backtest data in that directory.

**Your previous positions from prior sessions:**
{contents of <role-name>-history.md}

**Panel memory from prior sessions:**
{contents of memory.md}

Build on what was discussed before. Note what has changed since the last session.

**Response guidelines:**
- Respond ONLY with your contribution — no meta-commentary about being an AI or being in a panel.
- Be concise but substantive. 2-3 paragraphs max per response.
- Address other panelists' points when relevant. Use evidence and reasoning.
- When asked if you have a cross-talk question, only ask one if you need a specific clarification that won't come out in normal discussion. Say "no questions" if you don't.
- Reference specific files, functions, or data points from the shared directory when making technical claims.

Wait for the first round prompt.
```

### Round Flow

Each round has three phases: **Responses**, **Cross-talk**, and **Consensus Check**.

```
┌─ ROUND N ─────────────────────────────────────────────┐
│                                                        │
│  1. MODERATOR PROMPT                                   │
│     Post round topic to Discord + SendMessage to all   │
│                                                        │
│  2. PANELIST RESPONSES                                 │
│     Each panelist responds to moderator prompt          │
│     Post each response to Discord as it arrives         │
│                                                        │
│  3. CROSS-TALK (optional)                              │
│     Poll each panelist: "Any direct questions?"         │
│     Route questions → get answers → post to Discord     │
│     Max: 1 question + 1 follow-up per panelist          │
│                                                        │
│  4. CONSENSUS CHECK                                    │
│     Ask each: "Current position in 1-2 sentences.       │
│     Agree, disagree, or want to modify?"                │
│                                                        │
│  5. MODERATOR SUMMARY                                  │
│     Summarize positions, note agreements/disagreements  │
│     If consensus → end panel                            │
│     If not → frame next round on unresolved points      │
│                                                        │
└────────────────────────────────────────────────────────┘
```

**Detailed round steps:**

1. Post: `[Moderator] Round {N} — {topic/focus}`
2. For each panelist, SendMessage with the round prompt + summary of what others have said so far in this round:
   - Collect their response
   - Post to Discord: `[{emoji} {Role Name}] {response}`
   - Check for user input ("pause" / "stop")
3. **Cross-talk phase:**
   - Post: `[Moderator] Cross-talk — panelists may ask each other direct questions.`
   - SendMessage to each panelist: "You've heard everyone's responses this round. Do you want to ask another panelist a specific clarifying question? Only ask if you need a clarification you don't think will come out in normal discussion. Reply with the question and who it's for, or 'no questions'."
   - If a panelist has a question:
     - Post to Discord: `[{emoji} {Questioner}] @{Target}: {question}`
     - SendMessage the question to the target panelist
     - Post the target's response to Discord: `[{emoji} {Target}] {response}`
     - SendMessage the response back to the questioner: "Here's their answer. One follow-up allowed if needed, or say 'satisfied'."
     - If follow-up: route it the same way. Then move on.
   - If no panelist has questions, skip cross-talk.
4. **Consensus check:**
   - SendMessage to each panelist: "State your current position in 1-2 sentences. Do you agree with the emerging consensus, disagree, or want to modify it?"
   - Collect positions.
   - If all agree (with minor modifications) → proceed to synthesis.
   - If disagreements remain → frame next round around the specific disagreements.
5. Post: `[Moderator] Round {N} complete. {Summary}. {Consensus status}.`

### Discord Backend

If backend is "discord", post each message to the specified channel via **Discord webhook** (preferred). Store the webhook URL in `panels/<panel-name>/webhook.txt`. Webhooks are simpler and faster than the bot token API — just a POST with no auth headers needed.

```bash
curl -s -X POST "$(cat panels/<panel-name>/webhook.txt)" \
  -H "Content-Type: application/json" \
  -d '{"username": "Role Name", "content": "message"}'
```

The webhook `username` field controls the display name per message, so set it to the panelist's role name (e.g. "🔢 The Quant", "🦈 The Shark", "Moderator"). This makes each post appear to come from the panelist.

If no webhook exists, create one via the Discord bot API and save it. Terminal output still happens simultaneously. Format:

```
[emoji Role Name] message content
```

For cross-talk, use @-mentions to show who is being addressed:
```
[🔢 The Quant] @The Shark: How do you account for...
[🦈 The Shark] The line movement data shows...
```

### User Interjection

During execution, check for user input between panelist turns.

- **"pause"** — Stop the current round. Post `[Moderator] Panel paused by user.` Switch to talking with the user directly. User can ask questions, redirect the discussion, add new ground rules, or adjust. When user says "resume", continue from where you left off.
- **"stop"** — End early. Post `[Moderator] Panel stopped by user.` Skip to synthesis with whatever has been discussed so far.

## Phase 3: Synthesis

After consensus is reached or max rounds completed (or after "stop"):

1. Post: `[Moderator] === Panel Synthesis ===`
2. Summarize:
   - **Consensus** — What all panelists agreed on
   - **Disagreements** — Where they differed and why
   - **Key insights** — Non-obvious points that emerged
   - **Recommendations** — Actionable next steps, prioritized
   - **Open questions** — What remains unresolved
3. Save transcript to `panels/<panel-name>/transcript-{NNN}.md` (incrementing number)
4. Update each `<role-name>-history.md` with the panelist's statements from this session
5. Update `panels/<panel-name>/memory.md`:

```markdown
# Panel Memory: {panel-name}

## Last Session
- Date: {date}
- Session: {N}
- Rounds completed: {N}

## Decisions
- {what was agreed on}

## Open Questions
- {what remains unresolved}

## Action Items
- {what should be done next}

## Key Positions
- **Role 1**: {their final stance}
- **Role 2**: {their final stance}
- **Role 3**: {their final stance}

## Evolution
- Session 1: {one-line summary}
- Session 2: {one-line summary}
```

## Reconvening

When user says "reconvene panel X" or "reconvene {panel-name}":

1. Load `panels/<panel-name>/spec.json` and `panels/<panel-name>/memory.md`
2. Sync shared storage if the source has changed since last session
3. Run discovery again but pre-fill from spec — ask: "Same roles and settings? Any changes?"
4. Increment `sessions` in spec.json
5. Spawn panelists with memory context (history files + memory.md)
6. Moderator opens with: `[Moderator] Reconvening the {panel-name} panel. Last session we {summary from memory.md}. Today's focus: {topic or updated topic}.`

## Default Panel Roles

```json
[
  {"name": "Skeptic", "emoji": "🤨", "prompt": "You challenge assumptions and demand evidence. Push back on weak reasoning. Ask 'what could go wrong?' and 'where's the data?'"},
  {"name": "Advocate", "emoji": "💪", "prompt": "You argue in favor of the proposal. Find supporting evidence, highlight benefits, and make the strongest possible case."},
  {"name": "Domain Expert", "emoji": "🔬", "prompt": "You bring deep technical knowledge. Focus on data, methodology, implementation details, and precedent. Cite specifics."},
  {"name": "Wildcard", "emoji": "🃏", "prompt": "You think laterally. Bring unexpected angles, analogies from other fields, contrarian takes, and creative alternatives no one else is considering."}
]
```

## Panel Storage

```
~/kroagents/<agent-name>/panels/
  <panel-name>/
    spec.json
    memory.md
    transcript-001.md
    transcript-002.md
    shared/              ← shared files accessible to all panelists
      (source code, data, reference docs, etc.)
    <role-name>-history.md  ← per-panelist statement history
    ...
```

### Shared Storage (`shared/`)

The `shared/` directory holds files that ALL panelists can read during the discussion. Use this for:
- **Source code snapshots** — copy relevant code so panelists can reference it without needing live access to the codebase
- **Data exports** — bakeoff results, performance summaries, database schemas
- **Reference documents** — specs, design docs, external research

When spawning panelists, tell them the path to shared storage so they can read files directly.

**Important:** Shared storage is a snapshot/copy, not the live codebase. The moderator is responsible for syncing it before each session if the source has changed.

### Per-Panelist History (`<role-name>-history.md`)

Each panelist gets a history file containing every statement they made in previous sessions. When reconvening, include this in their prompt so they remember their prior positions and can build on them rather than repeating themselves. Update these files after each session.

## Example Session

```
User: run a panel on whether we should add a salary cap feature to the NHL model

[You/Moderator]: Let's set up the panel. I'd suggest the topic:
"Should the NHL prediction model incorporate salary cap data as a feature?"
Does that capture it, or do you want to refine?

User: yeah that works

[You/Moderator]: Panel composition — use the default (Skeptic, Advocate,
Domain Expert, Wildcard) or define custom roles?

User: default is fine

[You/Moderator]: 3 rounds, terminal output. Any ground rules?

User: focus on whether the data is actually available and reliable

[You/Moderator]: Got it. Panel "nhl-salary-cap" ready.
Starting now. Spawning panelists...

[Moderator] Welcome to the nhl-salary-cap panel...
[Moderator] Round 1 — Opening statements.
[🤨 Skeptic] Before we add any feature, we need to ask...
[💪 Advocate] Salary cap data could be transformative...
[🔬 Domain Expert] The data is available from CapFriendly...
[🃏 Wildcard] What if instead of cap numbers, we looked at...
[Moderator] Cross-talk — panelists may ask each other direct questions.
[🤨 Skeptic] @Domain Expert: You say the data is available, but how current is it?
[🔬 Domain Expert] CapFriendly updates within 24 hours of transactions...
[🤨 Skeptic] Satisfied.
[Moderator] Round 1 complete. Key tension: data availability vs signal strength.
  Consensus: Not yet — Skeptic wants more evidence, others are cautiously positive.
...
[Moderator] === Panel Synthesis ===
...
```
