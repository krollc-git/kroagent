---
name: panel
description: Run a moderated expert panel discussion with AI teammates. Supports default or custom roles, multiple rounds, pause/stop, Discord output, and reconvening with memory of past sessions.
user_invocable: true
---

# Expert Panel Discussion

Run a moderated panel of expert perspectives on a topic. You (the KroAgent) are the moderator. Panelists are spawned as teammates using the Agent tool.

## Triggering

When the user says any of: "run a panel", "start a panel", "panel discussion", "convene a panel", "reconvene panel X".

## Phase 1: Discovery

Before running a panel, have a conversation with the user to define:

1. **Topic** — What is the panel discussing? Refine until it's well-scoped.
2. **Panel composition** — Ask: "Use the default panel (Skeptic, Advocate, Domain Expert, Wildcard) or define your own?"
   - If custom, ask the user to name the roles and optionally describe each one.
3. **Rounds** — How many rounds of discussion? Default is 3.
4. **Output backend** — "Post to terminal (default) or a Discord channel?" If Discord, ask which channel.
5. **Ground rules** — Any constraints? e.g. "focus on data not opinions", "consider budget constraints", "keep it under 10 minutes"
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
    {"name": "Skeptic", "prompt": "You challenge assumptions and demand evidence. Push back on weak reasoning."},
    {"name": "Advocate", "prompt": "You argue in favor of the proposal. Find supporting evidence and make the strongest case."},
    {"name": "Domain Expert", "prompt": "You bring deep technical knowledge. Focus on data, methodology, and implementation details."},
    {"name": "Wildcard", "prompt": "You think laterally. Bring unexpected angles, analogies from other fields, and creative alternatives."}
  ],
  "rounds": 3,
  "backend": "terminal",
  "discord_channel_id": null,
  "ground_rules": [],
  "created_at": "2026-03-21",
  "sessions": 0
}
```

## Phase 2: Execution

### Spawning Panelists

For each role, spawn a teammate using the Agent tool. Each panelist gets this prompt:

```
You are a panelist in an expert discussion.

**Your role: {role_name}**
{role_prompt}

**Topic:** {topic}

**Ground rules:**
{ground_rules}

{memory_context}

When asked for your perspective, respond in character. Be concise but substantive.
Address other panelists' points when relevant. Use evidence and reasoning.
Keep responses to 2-3 paragraphs max.

You will receive the discussion so far and be asked for your response.
Respond ONLY with your contribution — no meta-commentary about being an AI or being in a panel.
```

If reconvening, add to the prompt:
```
**Previous session memory:**
{contents of memory.md}

**Your previous positions:**
{extracted from previous transcripts}

Build on what was discussed before. Note what has changed.
```

### Running Rounds

Format all output as `[Role Name] message` — this is how it appears in both terminal and Discord.

**Round flow:**

1. Post: `[Moderator] Welcome to the {panel_name} panel. Topic: {topic}`
2. Post: `[Moderator] Round 1 — Opening statements. Each panelist will give their initial perspective.`
3. For each panelist (sequentially):
   - Send the panelist the topic + discussion so far
   - Collect their response via the Agent tool
   - Post: `[{Role Name}] {response}`
   - **Check for user input.** If user typed "pause" or "stop", handle it (see below).
4. After all panelists respond:
   - Post: `[Moderator] Round 1 complete. {Brief summary of positions}.`
5. For rounds 2+:
   - Post: `[Moderator] Round {N} — {focus}` (identify key disagreements or open questions to focus on)
   - Send each panelist the full discussion so far, ask them to respond to specific points
   - Same flow as round 1
6. Final round: explicitly ask panelists for their final position and any remaining concerns.

### Discord Backend

If backend is "discord", post each `[Role] message` to the specified Discord channel. Use the Discord reader/writer tools available to the agent (or the krobot Discord bot if available). Terminal output still happens simultaneously.

### User Interjection

During execution, check for user input between panelist turns.

- **"pause"** — Stop the current round. Post `[Moderator] Panel paused by user.` Switch to talking with the user directly. User can ask questions, redirect the discussion, add new ground rules, or adjust. When user says "resume", continue from where you left off.
- **"stop"** — End early. Post `[Moderator] Panel stopped by user.` Skip to synthesis with whatever has been discussed so far.

## Phase 3: Synthesis

After all rounds (or after "stop"):

1. Post: `[Moderator] === Panel Synthesis ===`
2. Summarize:
   - **Consensus** — What all panelists agreed on
   - **Disagreements** — Where they differed and why
   - **Key insights** — Non-obvious points that emerged
   - **Recommendations** — Actionable next steps
   - **Open questions** — What remains unresolved
3. Save transcript to `panels/<panel-name>/transcript-{NNN}.md` (incrementing number)
4. Update `panels/<panel-name>/memory.md` — this is the persistent memory:

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
- **Skeptic**: {their final stance}
- **Advocate**: {their final stance}
- **Domain Expert**: {their final stance}
- **Wildcard**: {their final stance}

## Evolution
- Session 1: {one-line summary}
- Session 2: {one-line summary}
```

## Reconvening

When user says "reconvene panel X" or "reconvene {panel-name}":

1. Load `panels/<panel-name>/spec.json` and `panels/<panel-name>/memory.md`
2. Run discovery again but pre-fill from spec — ask: "Same roles and settings? Any changes?"
3. Increment `sessions` in spec.json
4. Spawn panelists with memory context (see Spawning Panelists above)
5. Moderator opens with: `[Moderator] Reconvening the {panel-name} panel. Last session we {summary from memory.md}. Today's focus: {topic or updated topic}.`

## Default Panel Roles

```json
[
  {"name": "Skeptic", "prompt": "You challenge assumptions and demand evidence. Push back on weak reasoning. Ask 'what could go wrong?' and 'where's the data?'"},
  {"name": "Advocate", "prompt": "You argue in favor of the proposal. Find supporting evidence, highlight benefits, and make the strongest possible case."},
  {"name": "Domain Expert", "prompt": "You bring deep technical knowledge. Focus on data, methodology, implementation details, and precedent. Cite specifics."},
  {"name": "Wildcard", "prompt": "You think laterally. Bring unexpected angles, analogies from other fields, contrarian takes, and creative alternatives no one else is considering."}
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
    ...
```

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

[You/Moderator]: Got it. Panel "nhl-salary-cap" ready:
- Topic: Should the NHL prediction model incorporate salary cap data?
- Roles: Skeptic, Advocate, Domain Expert, Wildcard
- Rounds: 3
- Ground rule: Focus on data availability and reliability
- Output: Terminal

Starting now.

[Moderator] Welcome to the nhl-salary-cap panel...
[Moderator] Round 1 — Opening statements.
[Skeptic] Before we add any feature, we need to ask...
[Advocate] Salary cap data could be transformative...
[Domain Expert] The data is available from CapFriendly...
[Wildcard] What if instead of cap numbers, we looked at...
[Moderator] Round 1 complete. Key tension: data availability vs signal strength...
...
[Moderator] === Panel Synthesis ===
...
```
