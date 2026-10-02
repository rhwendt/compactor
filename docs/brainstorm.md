# compactor — agent-controlled compaction plugin for Claude Code

> Status: **ready to promote** — brainstormed 2026-09-25; control model and interface decided.

## Idea
A Claude Code plugin that lets the agent decide *when* to compact, so compaction never lands
mid-task (debug chains, multi-file edits) and throws away the context that mattered.

## How the work version behaves (as described)
- Token threshold set via env var (e.g. 300k).
- At the threshold, auto-compact fires and the **PreCompact hook blocks it** — you see the
  attempt fail.
- The agent is supposed to pick a good moment to recommend compacting, or control it itself.
- In practice you usually have to say "release the hold"; the agent runs a Python script
  that flips the state file, and **compaction starts right away** (so a blocked auto-compact
  is retried, not given up on).
- Overall it works well, and it already covers much of the list below in some form. The
  main gaps: **the agent rarely releases on its own**, and **the Python release script is
  unreliable at times**.

## Work version internals (from its own docs, 2026-09-25)
**Why it exists:** native auto-compact fires near the top of the window (~967K in Sonnet 5's
1M mode). Every turn resends the whole history, so running that long is expensive, not just
risky. Setting `CLAUDE_CODE_AUTO_COMPACT_WINDOW=350000` pulls the threshold down; the plugin
adds a gate in front of it. If the env var is **unset, the gate always allows**, so the var
doubles as the on/off switch (blocking at the real ceiling would risk a hard overflow).

**Mechanism**
- `hooks/hooks.json`: PreCompact hook, matcher `"auto"` only (manual `/compact` is never
  gated) → `python3 "$CLAUDE_PLUGIN_ROOT/hooks/precompact_guard.py"`.
- Guard decision order (reads session_id from stdin JSON):
  1. window env var unset → allow
  2. `hold: true` → block in any mode, with a message giving the hold's age and reason
  3. `COMPACTOR_MODE=autonomous` → allow silently (headless, no human to ask)
  4. `approved: true` → allow; otherwise block and tell the agent to ask the human
     (AskUserQuestion)
  - Fail-safe: any exception → exit 0 (allow). Pure `decide(state, mode, window_configured)`
    is unit-tested.
- `hooks/compactor_state.py`: CLI the agent runs via Bash — `status`, `hold "why"`,
  `release`, `approve`. Finds its session through `$CLAUDE_CODE_SESSION_ID`.
- `hooks/_compactor_common.py`: state path, atomic write (tempfile + `os.replace`), 7-day
  prune-on-write, "12m ago" age hints.

**State:** `${XDG_STATE_HOME:-~/.local/state}/claude-compactor/state-<session-id>.json`
(sanitized id). Fields: `hold` (agent), `approved` (user via agent; sticky for the session,
one-way), `reason` (cleared on release), `updated_at`. A missing or corrupt file reads as
the default (fail open).

**Skill rules (skills/compactor/SKILL.md):** hold before long autonomous runs or big
refactors, always with a reason; release at a safe checkpoint; release ≠ compact now (it
only unlocks the gate, and compaction fires at the next threshold check, which means ending
the turn); "compact now, then re-hold" sequence; never end an autonomous run with a hold set;
don't approve without asking the user; don't release and hold in the same turn.

**Path traps (each has broken it) = the root cause of the failing first call**
- `$CLAUDE_PLUGIN_ROOT` is **not set** in Bash tool subprocesses.
- A bare glob matches several cached plugin versions after an update; the cache dir name is
  the commit hash, so a hard-coded path goes stale.
- zsh quoting. Workaround:
  `CLI=$(ls -td ~/.claude/plugins/cache/*/compactor/*/hooks/compactor_state.py | head -1)`

**Observed failure:** a live hold 3 days old, left behind by a finished session. Holds never
clean themselves up; only the 7-day prune removes the file.

## Lessons for the new version
- **Fix discoverability first.** The agent must never have to find a script path. Options:
  MCP tools (does the server process know the session id?), a plugin `bin/` on PATH (verify
  support), or a SessionStart hook that injects the exact absolute command into context
  (cheapest, since hooks do get `$CLAUDE_PLUGIN_ROOT`).
- **Holds need a lifetime.** Clear on SessionEnd, and/or expire after N turns or when the
  task ends, with a Stop-hook reminder.
- **Two concepts (hold vs approve) confuse the agent** — the do-not list has to warn about it.
  Consider simplifying to one control plus a mode.
- Keep what works: the env var as the on/off switch, "auto" matcher only, fail-open, a pure
  decision function with tests, atomic session-scoped state.

## Confirmed requirements
- **Reliable hold/release.** It has to work on the first call, every time. (Subagents were
  later scoped out; see Decisions.)
- **Handoff note / breadcrumb.** The agent can leave itself important notes that are
  guaranteed to come back after compaction.

## Decisions
- **Control model: hold only, compaction allowed by default** (2026-09-25). At the threshold,
  compaction proceeds unless the agent has an active hold. There is no user-approval state.
  Holds clear on SessionEnd, and a Stop hook reminds the agent while a hold is set. The handoff
  note preserves what matters. An optional "ask me first" setting can come later if wanted.
- **Agent interface: a `bin/compactor` executable** shipped in the plugin's `bin/`, which is on
  the Bash tool's PATH (verified in the docs). No path discovery, so it works the same in
  subagents. This replaces both the glob workaround and the MCP idea (MCP stdio servers don't
  receive a session id).

- **MCP considered and rejected for v1** (2026-09-25): no session id (and one server outlives
  `/clear`), MCP tools can be deferred (which removes the self-advertising benefit), and a
  long-running server is one more thing that can fail. `bin/compactor` reads
  `$CLAUDE_CODE_SESSION_ID` fresh on every call. It makes up for the gap with the SessionStart
  hook reminding the agent of the command, and with helpful usage errors (a hold without a
  reason is an error). An MCP wrapper can come later if Bash restrictions become a problem.
- **Subagents are out of scope** (2026-09-25): they rarely get near the threshold. Subagents
  don't use `compactor`; the PreCompact gate always allows when `agent_id` is present; a hold
  belongs to the session. The main agent holds around subagent dispatch itself.

## Polish directions (not decided yet)
1. **Nudge the agent at good moments.** When the hold is on and usage is past the threshold,
   a hook (Stop / UserPromptSubmit / PostToolUse) reminds the agent: "held since X, now at
   Y tokens — release at the next natural breakpoint." Nudges get firmer as usage grows.
2. **Detect breakpoints.** Task finished, tests green, commit made → strong signal to release.
3. **Reliable release/hold interface** to replace the Python script. Leading option: a small
   MCP server shipped with the plugin (tools like `compactor_hold`, `compactor_release`,
   `compactor_note`). These are typed tool calls with no shell quoting or Python environment
   to break, and subagents get them too. The server writes the state file atomically; hooks
   only read it.
4. **Safety ceiling.** Auto-release before the hard context limit so a held session can't hit
   the wall.
5. **Handoff on release.** Before releasing, the agent writes a short "what matters right
   now" note; `SessionStart` (source=compact) re-injects it after compaction.
6. **Per-session state**, keyed by session_id, so parallel sessions don't share a hold.
7. **Visibility.** Status line shows hold state and tokens past the threshold.

## Verified in the docs (2026-09-25)
- PreCompact: matcher `manual` or `auto`; **exit code 2 blocks** compaction.
- Stop hook: can block the stop and hand Claude a reason → the mechanism for reminders.
- SessionStart matchers include **`compact`**; its stdout / `additionalContext` is added to
  the context → the mechanism for re-injecting the handoff note.
- SessionEnd: can't block, but works for cleanup (clearing holds).
- Hook input includes `agent_id` / `agent_type` inside subagents; SubagentStart and
  SubagentStop events exist.
- Hook input includes **no token-usage fields** (common fields: session_id, transcript_path,
  cwd, ...). Nudges based on usage would need to read the transcript.
- Plugin `bin/` is on the Bash tool's PATH. `CLAUDE_PLUGIN_ROOT` is **not** in the Bash
  environment (main session or subagent), but it is substituted inline in skill bodies.
- MCP stdio servers get `CLAUDE_PLUGIN_ROOT`/`CLAUDE_PLUGIN_DATA`, but no session id.

## Still to verify
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (absolute tokens) vs `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`
  (percent): exact semantics and units.
- Retry behavior after a block: the work version suggests it retries every turn.
- Whether the statusline input exposes context token usage.
- The model can't run `/compact` itself — releasing only takes effect at the next
  auto-compact attempt.
- Do subagents inherit `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and does PreCompact fire for them
  (with `agent_id`)?

## Open questions
- The first call fails because of path discovery (see path traps above), not the logic.
- For personal use only, or a public plugin eventually?
