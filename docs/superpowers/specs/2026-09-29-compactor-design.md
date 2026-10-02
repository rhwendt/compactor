# compactor — design spec

- **Date:** 2026-09-29
- **Status:** draft for review
- **Source:** `docs/brainstorm.md` (2026-09-25) plus the brainstorming session of 2026-09-26 to 2026-09-29

## 1. Goal and scope

A public Claude Code plugin that lets **the agent control auto-compaction itself**, so
compaction never lands mid-task and never throws away the context that mattered.

**What "control" means (a platform limit, stated up front).** Hooks cannot start a
compaction, and the model cannot run `/compact`. So the agent controls **when compaction is
allowed**, not when it starts:

- Below the threshold, nothing compacts, whatever the agent does.
- At or above the threshold, compaction proceeds unless the agent holds it. Releasing lets
  it through at the next auto-compact check.
- A safety ceiling guarantees that no hold can push the session into the hard context limit.

**Audience.** A public plugin from day one: marketplace packaging, a README, documented
config, and safe defaults for other people's setups.

**Runtime.** Python 3.9+ with the standard library only. No third-party dependencies.

### In scope (v1)
- A single agent control: **hold / release**. Compaction is allowed by default.
- A **handoff note** that is re-injected after every compaction.
- Hold lifecycle: SessionEnd cleanup and a Stop-hook reminder.
- **Usage-aware nudges** that report context % and get firmer as usage grows.
- **Breakpoint detection** (a commit, or passing tests) that suggests releasing.
- A **safety ceiling** that overrides a hold before the hard limit.
- **Status-line output** through `compactor status --line`.
- **Subagent-safe gating** (Task 14): a running subagent's auto-compactions are held until it
  finishes or reaches its own ceiling (`COMPACTOR_SUBAGENTS=hold`, the default), a main hold
  never hides a subagent's ceiling, and the main agent's note isn't injected into a subagent
  after it compacts (§5.1, §5.5).

### Out of scope (v1)
- User-approval state, "ask me first" mode, and autonomous mode. These were rejected
  because they add a second concept, and the goal is agent control.
- Subagent *control*: a subagent can't hold or release (it shares the parent's session, so
  `compactor` in a subagent acts on the main agent's hold). Holds belong to the main agent;
  subagent compactions are only gated as in §5.1.
- An MCP server interface. It was rejected because the server gets no session id and
  deferred tools lose the benefit of advertising themselves. It can be revisited later.
- Starting a compaction early, which the platform does not allow.

## 2. Architecture

```
compactor/                               (repo root)
├── .claude-plugin/
│   ├── plugin.json                      # plugin manifest
│   └── marketplace.json                 # single-plugin marketplace
├── bin/compactor                        # agent CLI; plugin bin/ is on the Bash tool PATH
├── hooks/hooks.json                     # hook registrations
├── compactor/                           # python package, stdlib only
│   ├── config.py                        # env vars → validated Settings
│   ├── state.py                         # session state file I/O
│   ├── usage.py                         # transcript → Usage(tokens, window, pct)
│   ├── model.py                         # plain data types (no I/O), shared by policy/messages
│   ├── policy.py                        # PURE decision functions
│   ├── subagents.py                     # running-subagent markers, subagent transcripts
│   ├── messages.py                      # all agent-facing text in one place
│   ├── cli.py                           # argparse implementation behind bin/compactor
│   └── hooks/
│       ├── precompact.py
│       ├── stop.py
│       ├── user_prompt_submit.py
│       ├── post_tool_use.py
│       ├── session_start.py
│       ├── session_end.py
│       ├── subagent_activity.py         # a subagent's PostToolUse: marker liveness only
│       ├── subagent_start.py
│       └── subagent_stop.py
├── skills/compactor/SKILL.md            # agent guidance
├── tests/
├── README.md  CHANGELOG.md  LICENSE (MIT)
└── .github/workflows/ci.yml
```

### Unit responsibilities

| Unit | Does | Depends on |
|---|---|---|
| `config.py` | Reads the env vars in §6, applies defaults, and clamps or ignores invalid values (a bad value falls back to its default and gets a logged warning) | env |
| `state.py` | `load(session_id) -> State`, `save(session_id, State)` (atomic), `prune()`, path sanitisation | filesystem |
| `usage.py` | `read_usage(transcript_path, settings) -> Usage \| None`: tail-reads the JSONL and finds the last assistant `usage` | filesystem |
| `subagents.py` | Marker files for running subagents; locates `<session>/subagents/agent-<id>.jsonl` and its `.meta.json`, measures its usage and window, and says whether a SessionStart(compact) was a subagent's | filesystem, state, usage |
| `policy.py` | `decide_gate`, `decide_compaction`, `nudge_level`, `should_nudge`, `is_breakpoint`, `should_block_stop`. No I/O at all | none |
| `messages.py` | Formats every string the agent sees, including the context lines | none |
| `cli.py` | The `hold`, `release`, `note` and `status` subcommands | config, state, usage, messages |
| `hooks/*.py` | Parse stdin JSON, call policy, emit hook output, and fail open | all of the above |

Hooks and `bin/compactor` run as `python3 -m compactor...` with `PYTHONPATH` set to the
plugin root. Hooks get the root from `${CLAUDE_PLUGIN_ROOT}`, and `bin/compactor` resolves
it from its own real path, since `CLAUDE_PLUGIN_ROOT` is not set in Bash.

### Hook registrations

| Event | Matcher | Job |
|---|---|---|
| PreCompact | `auto` | The gate (§5.1). Manual `/compact` is never gated. |
| Stop | none | Forgotten-hold check (§5.3). Subagent markers are left alone: they end at SubagentStop, idle expiry, or an error answer to their Agent call |
| UserPromptSubmit | none | Nudge at the start of a turn (§5.2); demotes running subagents to background (the main agent is acting, so not waiting) |
| PostToolUse | all tools (no matcher) | Main agent: nudges during autonomous work (§5.2), breakpoint detection on Bash only (§5.4), and demoting running subagents to background. Subagent (`agent_id`): only refreshes that subagent's marker |
| SessionStart | `startup\|resume\|compact\|clear\|fork` | CLI reminder; handoff note after `compact`, `resume` or `fork`; ceiling-override notice |
| SessionEnd | none | Clear the hold and the subagent markers |
| SubagentStart | none | Record a real subagent (non-empty `agent_type`) as running (§5.1) |
| SubagentStop | none | Forget it. Claude Code also fires SubagentStop, with an empty `agent_type`, for the throwaway agent that writes every compaction summary; those are ignored |

Every hook exits immediately (allow, no output) when any of these is true:

- `COMPACTOR_DISABLE` is set.
- The input contains `agent_id` (a subagent), except for SubagentStart, SubagentStop and
  PostToolUse, which then only track the subagent (start, stop, and liveness).
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is unset. In that case the plugin is inactive, except
  that SessionStart still tells the agent the plugin is off, once.

## 3. State

**Path:** `${XDG_STATE_HOME:-~/.local/state}/claude-compactor/<sanitized-session-id>.json`.
The session id is sanitised to `[A-Za-z0-9_-]`.

```json
{
  "version": 1,
  "hold": {"reason": "mid-refactor of auth", "since": "2026-09-29T14:02:11Z"},
  "note": {"text": "…", "updated_at": "2026-09-29T15:40:00Z"},
  "nudge": {"last_level": 2, "calls_since": 3, "breakpoint_suggested": false},
  "stop_blocked_this_turn": false,
  "ceiling_override": {"at": "…", "pct": 90.4, "reason": "mid-refactor of auth"}
}
```

- **Writes:** atomic, via a tempfile in the same directory followed by `os.replace`. Every
  write also prunes state files older than 7 days.
- **Reads:** a missing file, a corrupt file, or an unknown `version` reads as the empty
  default. The plugin fails open.
- **Writers:** only `cli.py` writes `hold` and `note`. Hooks write only the bookkeeping
  fields (`nudge`, `stop_blocked_this_turn`, `ceiling_override`), plus the running-subagent
  markers, which live outside the state file (one file per agent under
  `claude-compactor/subagents/<session>/`, written atomically, holding `{"demoted": bool}`)
  so that parallel SubagentStart hooks and a concurrent `compactor hold` can't overwrite each
  other. A marker's mtime is its liveness clock: the subagent's own PostToolUse refreshes it
  (recreating an expired marker, demoted: the main agent can't still be waiting on a subagent
  whose marker expired mid-run), and a marker idle for more than 15 minutes is expired and
  removed, along with empty marker directories. Pruning never follows a symlink, enters only
  directories named like a sanitized session id, and removes only files named like a marker
  (a valid agent id) or a marker tempfile (`.tmp-*`); a symlinked marker directory is neither
  read nor written. A main-agent PostToolUse or UserPromptSubmit
  sets `demoted` (keeping the mtime): the main agent is acting, so it isn't waiting on any
  subagent, and none counts as foreground any more. A marker whose `meta.json` says
  `requestShape: "foreground"` changes once the main transcript holds a `tool_result` for its
  `toolUseId`. With `is_error: true` the call is over (an API error ends it that way and fires
  no SubagentStop), so the marker is removed, demoted or not. Any other result may mean the call
  was moved to the background (Claude Code then answers it at once, `async_launched`, and the
  subagent keeps running while `meta.json` still says foreground), so the marker is only
  demoted. It stays measured, so its ceiling still applies, and it ends at SubagentStop or idle
  expiry. The check runs on every read of the markers, but it searches only the last 8 MB of
  the main transcript: a result older than that isn't found, and the marker keeps whatever it
  last recorded (a demotion is written to the marker, so it sticks; a removed marker's subagent
  is gone). When `meta.json` is missing or has no `toolUseId` there is nothing to look for, and
  the marker ends only at SubagentStop, idle expiry, or (for foreground) a main-agent demotion.
  A background call's result arrives at launch, so it doesn't count. The result is on disk before the main agent's next PreCompact: Claude Code
  appends a tool result before the next API call, where auto-compaction is checked. Hooks clear `hold` in two
  cases: a ceiling override and SessionEnd. `window` is written by `status --line` (the exact
  size from the statusline input, which always wins) and by SessionStart, which sets it to
  1,000,000 when it is unknown and the payload's `model` ends with `[1m]`.
- **Resets:** `nudge` is reset whenever a hold is set or released.
  `stop_blocked_this_turn` is set by Stop when it blocks, and cleared by UserPromptSubmit
  at the start of every turn.
- **Lifetimes:**
  - `hold` is cleared on release, SessionEnd, or a ceiling override.
  - `note` persists until the agent overwrites or clears it, so it survives repeated
    compactions and resumes.
  - `ceiling_override` is shown once by SessionStart(`compact`), then cleared.

## 4. CLI (`bin/compactor`)

The CLI reads `$CLAUDE_CODE_SESSION_ID` fresh on every call.

| Command | Effect | Output |
|---|---|---|
| `compactor hold "<reason>"` | Sets the hold. The reason is required and non-empty. Holding again replaces the reason and keeps `since`. | Confirmation, plus the context lines |
| `compactor release [--note "<text>"]` | Clears the hold and optionally sets the note in the same call | Confirmation that compaction will proceed at the next threshold check |
| `compactor note "<text>"` | Sets or replaces the handoff note (maximum 4000 chars; longer notes are rejected with a message) | Confirmation |
| `compactor note --clear` | Clears the note | Confirmation |
| `compactor status` | Shows the hold (reason and age), a note preview, the context lines, and the last hook error if any | Human-readable text |
| `compactor status --json` | The same data, as JSON | JSON |
| `compactor status --line` | A compact status-line string, e.g. `⏸ held 12m · 41%` or `▶ 41%` | One line |

The CLI **fails loudly** with a nonzero exit and a message showing the correct invocation
when:

- the session id is missing
- a reason or note is empty
- the arguments are unknown
- the state directory can't be written

The CLI finds usage by locating the session transcript under
`~/.claude/projects/*/<session-id>.jsonl`. If it isn't found, `status` shows
`usage unknown`.

`--line` may also receive the statusline JSON on stdin. It uses `session_id` and
`transcript_path` from there when the environment doesn't provide them.

## 5. Behaviour

**Definitions:**

- `T` is the threshold in tokens (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`).
- `W` is the window size.
- `C` is the ceiling in tokens (`COMPACTOR_CEILING_PCT` × W).
- `used` is the latest usage: input tokens + cache read tokens + cache creation tokens from
  the last assistant message, plus `pending`: an estimate (characters ÷ 3) of the user and
  tool-result content added since, which that message's usage doesn't include yet.
- `growth` is the expected growth over the next turn: the larger of `pending` and how much the
  last reply's usage grew over the reply before it (0 across a compaction). PreCompact fires
  as a reply arrives, often before its tool results are written, so `pending` alone sees
  little of a large parallel batch (verification notes, "Overflow under a hold").

**Context lines**, included in every nudge, gate block, Stop reminder, `status`, `hold` and
`release` output. Every number names what it measures (decided 2026-09-30):

```
Context: 410k of 1M tokens used (41% of the model's window).
Auto-compact threshold: 350k (CLAUDE_CODE_AUTO_COMPACT_WINDOW) — you are 60k past it; your hold is what's stopping compaction.
Safety ceiling: 900k (90% of window) — your hold is overridden there, 490k from now.
```

- Below the threshold, line 2 ends `— 250k away.` Without a hold, the hold clauses read "a
  hold". At or past the ceiling, line 3 ends `— reached; a hold is overridden at the next
  compaction check.`
- When usage is unknown, line 1 reads `Context: usage unknown (no reply has reported it yet,
  or the transcript couldn't be read).`, line 2 gives the threshold alone, and line 3 names the `COMPACTOR_MAX_HOLD_MIN`
  fallback.
- `status --line` stays compact (`⏸ held 12m · 41%`); it is for the human's status bar.

### 5.1 PreCompact gate: `decide_gate(state, usage, settings, now)`

The checks run in order, and the first that applies decides.

1. **No hold** → allow.
2. **Hold, and `used + growth >= C`** → allow. Record `ceiling_override` and clear the hold.
   This asks whether one more turn like the last would reach the ceiling: a blocked compaction gets no later chance once a turn's tool results
   push context past the hard limit. The same test applies to subagents' ceilings.
3. **Hold, usage unknown, and the hold is older than `COMPACTOR_MAX_HOLD_MIN`** → allow.
   Record `ceiling_override` with `pct: null` and clear the hold.
4. **Otherwise** → block (exit 2). The stderr message is shown to the human, so it is
   worded for them: the agent's hold, its reason and age, that it will be released at a safe
   point and that the safety ceiling overrides it near the context limit, then the context
   lines.

The ceiling is never above the window: `C` is always `COMPACTOR_CEILING_PCT` × W, even when
T is set at or above it. In that case "past the threshold" (nudges, Stop, breakpoints) starts
at `min(T, C)`, and every nudge past it is level 3.

**Subagents (Task 14): `decide_compaction(hold, usage, active_subagents, settings, now)`.**
For a subagent's compaction PreCompact carries the parent's `session_id` and
`transcript_path` and no `agent_id`, so it can't tell whose compaction it is
(verification notes, "Subagents, trigger point and retries"; upstream issue
[anthropics/claude-code#91910](https://github.com/anthropics/claude-code/issues/91910) asks for
agent fields on PreCompact, PostCompact and SessionStart(`compact`) — if that lands, replace
the inference below with the payload's `agent_id`). The gate therefore also
measures every running real subagent (SubagentStart seen, no SubagentStop yet, a tool call
within the last 15 minutes), reading
`<transcript without .jsonl>/subagents/agent-<id>.jsonl`. A subagent's window is 1M if its
transcript identity or its `meta.json` `model` ends in `[1m]`, otherwise 200k is assumed
(a lower ceiling is the safer guess). With no running subagent, the rules above apply
unchanged. Otherwise, first match wins:

1. **Main usage `>= C`** → allow; record `ceiling_override` and clear the hold if one is set,
   unless every running subagent is foreground (rule 4): the compaction is then theirs, so the
   hold and the override wait for the main agent's own next compaction attempt.
2. **Any subagent at or over its own ceiling** → allow; the main hold stays.
3. **`COMPACTOR_SUBAGENTS=hold`, every running subagent's usage is known, and either the main
   usage is known or every running subagent is foreground** → block, with a stderr message
   that a subagent's compaction is held until its task ends or it reaches its safety ceiling,
   and each subagent's usage. (A subagent whose transcript can't be read is not held blind;
   and when the main usage is unknown and the compaction may be the main agent's, rule 6
   keeps the `COMPACTOR_MAX_HOLD_MIN` fallback reachable.)
4. **Every running subagent is foreground** (`meta.json` `requestShape: "foreground"`, and no
   main-agent tool call or prompt since it started) → allow: the main agent is waiting on
   them, so the compaction is a subagent's.
5. **Some running subagent's usage can't be read** (and not every one is foreground) → the
   main rules 1–4 above decide as if the main usage were unknown, so a main hold blocks only
   until it is `COMPACTOR_MAX_HOLD_MIN` old: the compaction may be that subagent's, which could
   be near its hard limit. When they allow, the hold is not overridden (no `ceiling_override`,
   the hold stays), since the compaction may not be the main agent's.
6. **Otherwise** (a background subagent runs alongside the main agent, so the compaction may
   be the main agent's) → the main rules 1–4 above decide.

Ambiguity resolves toward safety: nothing at or over any ceiling is ever blocked; below
that, the compaction is blocked if either the subagent rule or (when the main agent might be
compacting) the main rule says block. The cost, stated plainly: in `hold` mode (the default),
while any **background** subagent runs, the main agent's own compactions are also held until
the main ceiling, even when the main agent has no hold, because they can't be told apart
from the subagent's. Foreground subagents don't affect the main agent (it is waiting on
them). A subagent on a 200k window is held until 180k, one on a 1M window until 900k.

### 5.2 Nudges: `nudge_level(usage, settings)` and `should_nudge(state, level, settings)`

| Level | Condition (hold set) | Message gist |
|---|---|---|
| 0 | No hold, or `used < T` | Silent |
| 1 | `T <= used`, and less than halfway from T to C | "Compaction is being held for: <reason>. Release at your next natural breakpoint." |
| 2 | At least halfway from T to C, and `used < C − 0.05·W` | Firmer: "You are well past the threshold. Finish the current step, write a note, release." |
| 3 | `used >= C − 0.05·W` (within 5 percentage points of the ceiling) | "The ceiling will override your hold at N%. Write a note and release now." |

- **Delivery:** as `additionalContext` from PostToolUse (all tools) and UserPromptSubmit.
- **Rate limit:** a nudge is emitted when the level is **higher than `last_level`**, or
  when `calls_since >= COMPACTOR_NUDGE_EVERY` at the same level. Level 3 is repeated on every
  eligible call.
- **Usage unknown:** nudges fall back to a hold-age reminder every `COMPACTOR_NUDGE_EVERY`
  calls.

### 5.3 Stop: `should_block_stop(state, usage, stop_hook_active)`

The Stop hook blocks the stop, with the reason as the message, only if all of these hold:

- a hold is set
- `used >= T` (or usage is unknown and the hold is older than 30 minutes)
- `stop_hook_active` is false
- `stop_blocked_this_turn` is false

The message is: *"You're ending your turn with compaction held for: <reason>. Context %
line. Either `compactor release` (optionally with `--note`), or re-run `compactor hold` with
a current reason if the work is still fragile."*

This blocks at most once per turn and never loops.

### 5.4 Breakpoints: `is_breakpoint(command, exit_code, patterns)`

PostToolUse on Bash only, when a hold is set and `used >= min(T, C)`:

- **Commits:** a successful `git commit` (and not `--dry-run`).
- **Tests:** a successful command matching the built-in test patterns: `pytest`,
  `python -m pytest`, `npm test`, `npm run test`, `pnpm test`, `yarn test`, `go test`,
  `cargo test`, `make test`, `mvn test`, `gradle test`, `rspec`, `jest`, `vitest`.
- **User patterns:** anything matching `COMPACTOR_BREAKPOINT_PATTERNS`, a list of regexes
  separated by `;`.

A breakpoint produces a one-time suggestion (`breakpoint_suggested` is reset whenever a
hold is set):

> "That looks like a natural breakpoint. Consider writing a note and running
> `compactor release`."

If PostToolUse input doesn't expose the exit code, a breakpoint is detected only from a
tool response that isn't an error (to verify, §9).

### 5.5 SessionStart

| Source | Injected context |
|---|---|
| `startup`, `clear` | A one-line CLI reminder: `compactor hold "<why>"`, `compactor release [--note]`, `compactor note`, `compactor status` |
| `resume`, `fork` | The CLI reminder, plus the note if one is set |
| `compact` | The note, framed as "Handoff note you left before compaction"; a notice if a `ceiling_override` happened (then cleared); the CLI reminder |

SessionStart(`compact`) also fires after a subagent's compaction, with the parent's
`session_id` and no `agent_id`. When the compaction was a subagent's, nothing is injected
and `ceiling_override` stays pending for the main agent's next compaction. It counts as a
subagent's when every running subagent is foreground (the same rule as the gate, §5.1: the
main agent is waiting on them, so it can't be compacting). The compacting agent's
`compact_boundary` can't decide it: Claude Code stamps it ~0.2 s before SessionStart fires but
writes it to the transcript only after every SessionStart hook has returned. While a
background subagent runs, the compaction may be either agent's, so it is treated as the main
agent's and the full main context is injected: note, notices and CLI reminder, led by one
line telling a subagent that this is the main agent's note and hold, to ignore them and not
to run compactor. It may then land in a background subagent's context, and acting on it
there changes the main agent's hold or note (they share the session). This is never a hard-limit risk: the ceiling applies
to every agent regardless. The
check runs before anything else, so a subagent's compaction also never writes
the window cache; nor does any compaction while a subagent runs, since the payload's `model`
names the compacting agent, which may be that subagent. The window-cache save in SessionStart is best-effort: an unwritable state
directory doesn't suppress its output, and neither does a failed save after the ceiling-override notice (the notice may then repeat).

**Subagent liveness, in detail** (§3 has the marker mechanics). A subagent counts as running
from SubagentStart until SubagentStop, or until it has made no tool call for 15 minutes (a
subagent's own PostToolUse refreshes its marker; one that ran a single step longer than that
gets its marker back, demoted, at its next tool call). A foreground subagent whose Agent call
is answered in the main transcript with an error is gone (an API error ends it without a
SubagentStop). One answered without an error may have been moved to the background by the
user (Claude Code answers the call at once and the subagent keeps running), so from then on it
counts as a background subagent. A main-agent tool call or prompt demotes every running
subagent to background, since the main agent is evidently not waiting. Edge case: a
foreground subagent is also demoted by a main-agent tool call made in parallel with its Agent
call (the main agent issued several tools in one turn, and that other tool's PostToolUse fires
while the subagent runs). The effect is bounded (it lasts only for that subagent's run) and safe for the
ceiling: the subagent is then treated as a background one, so its compactions and the main
agent's are gated together, it still gets its own ceiling, and after its compaction the main
agent's note is injected (led by the line telling a subagent to ignore it).

### 5.6 SessionEnd

Clears `hold` and keeps `note` so that `resume` works. A session that ends with a hold set
never leaves a stale hold behind.

## 6. Configuration

All configuration is through environment variables, typically set in
`~/.claude/settings.json` under `env`.

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | unset → plugin inactive | Claude Code's threshold, and the plugin's on/off switch |
| `COMPACTOR_CEILING_PCT` | `90` | Percentage of the window at which a hold is overridden (clamped between 50 and 98) |
| `COMPACTOR_CONTEXT_WINDOW` | inferred | Window size in tokens. Inferred in this order: an explicit setting here; then the size last reported by `status --line` (the statusline input carries `context_window_size`); then 1,000,000 if SessionStart saw a `model` ending in `[1m]`; then 1,000,000 if the transcript's most recent model-identity attachment (written before the first reply and again after each compaction; found by scanning back from the end, then the first 64 KB) has a `modelId` ending in `[1m]`; then, only if none of those resolved it, 1,000,000 if usage is above 200,000 else 200,000 (not from the threshold: a 200k model may carry a threshold set for a 1M one, and a 1M guess would put the ceiling past its real limit) — this last case is a guess, so `Usage.window_known` is `False` and the context lines flag the percentage as assumed |
| `COMPACTOR_NUDGE_EVERY` | `10` | Tool calls and prompts between repeat nudges at the same level |
| `COMPACTOR_BREAKPOINT_PATTERNS` | built-in list | Extra breakpoint regexes, separated by `;` |
| `COMPACTOR_MAX_HOLD_MIN` | `60` | Hold-age override, used only when usage is unknown |
| `COMPACTOR_SUBAGENTS` | `hold` | `hold`: hold a running subagent's auto-compactions until its task ends or it reaches its own ceiling (180k on a 200k window, 900k on 1M). While a background subagent runs, the main agent's compactions are also held until the main ceiling, even without a main hold; foreground subagents don't affect the main agent. `allow`: let subagents compact (a main hold still blocks when the compaction may be the main agent's). Anything else falls back to `hold` with a warning |
| `COMPACTOR_DISABLE` | unset | Any non-empty value turns every hook into a no-op |

## 7. Error handling

- **Hooks fail open.** Every entry point wraps its whole body. Any exception exits 0 with no
  output, and the traceback is appended to `claude-compactor/errors.log` (kept to its last
  200 lines). `status` reports the most recent error.
- **The CLI fails loudly,** with a nonzero exit and a corrective usage message (§4).
- **Unknown usage degrades:** the gate uses hold age, nudges use hold age, and the ceiling
  becomes `COMPACTOR_MAX_HOLD_MIN`.
- **Invalid config** falls back to its default, and a warning goes to the error log.

## 8. Agent guidance (`skills/compactor/SKILL.md`)

The skill tells the agent to:

- **Hold** before fragile, multi-step work: debugging chains, multi-file refactors, long
  autonomous runs, and before dispatching subagents whose results it must integrate. Always
  give a specific reason.
- **Release** at natural breakpoints: a task done, tests green, a commit made, a change of
  topic. Release before ending a turn unless the next turn continues the same fragile work.
- **Write a note** before releasing whenever anything important isn't yet on disk, such as
  the current hypothesis, the next steps, or the file:line references in play. Prefer
  `release --note`.
- Remember that **release doesn't mean compact now**. Compaction happens at the next
  threshold check.
- **Never** end an autonomous run holding, **never** hold without a reason, and **don't**
  release and re-hold in the same turn just to silence nudges.
- **Trust the context lines** when choosing when to release.
- **Subagents:** hold (`compactor hold "waiting on <task> report"`) before dispatching a
  subagent whose report it must act on, and keep holding until it has analysed the report and
  done the work it calls for; tell long subagent tasks to write findings to a file as they go
  and report the path.

## 9. To verify during planning

The plan must answer each of these first. If an answer contradicts the design, the design
gets amended.

**Results so far (2026-09-29, from the docs and a live transcript):**

1. `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is an absolute token count (valid range 100,000 to
   1,000,000). `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` can only lower the trigger point within it.
2. Retry after a block is **not documented**. It gets checked in the manual smoke test.
3. Usage is `message.usage` on `type: "assistant"` entries (`input_tokens`,
   `cache_read_input_tokens`, `cache_creation_input_tokens`). The model id is
   `claude-opus-5-5` even in 1M mode, so window inference changed (§6). The docs call the
   transcript format internal, so `usage.py` must stay defensive and fail open. The
   transcript also carries a `{"type": "attachment", "attachment": {"identity":
   {"modelId": ...}}}` entry with the model identity — including the `[1m]` suffix in 1M
   mode — written before the agent's first reply, in headless runs too (Task 13).
4. `additionalContext` from PostToolUse and UserPromptSubmit reaches the model. The Bash
   response shape and exit code are **unclear in the docs**, so plan Task 1 captures real
   payloads.
5. The statusline input includes `context_window.context_window_size` and `current_usage`.
   `status --line` uses these directly and caches the window size in state.
6. The transcript path is `<config dir>/projects/<sanitized cwd>/<session-id>.jsonl`, where
   the config dir is `CLAUDE_CONFIG_DIR` or `~/.claude`. `CLAUDE_CODE_SESSION_ID` is exported to
   the Bash tool and to hooks.

Also confirmed: SessionStart `source` includes `fork`, and the plugin `bin/` is on the Bash PATH.

Subagents, the real trigger point and retry cadence were verified on 2026-09-30; see
`docs/superpowers/specs/2026-09-29-verification.md`, "Subagents, trigger point and retries",
and §5.1/§5.5 for what changed as a result.

1. **Threshold variable:** the units and semantics of `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and
   how it relates to `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`.
2. **Retry after a block:** does a blocked auto-compact retry on every turn? If not, release
   needs another trigger, and §1's scope changes.
3. **Transcript usage fields:** the field names and location of usage in the transcript, and
   whether the model id there reveals 1M-context mode.
4. **Hook output:** whether `additionalContext` from PostToolUse and UserPromptSubmit reaches
   the model, and whether PostToolUse input includes the Bash exit code.
5. **Statusline input:** its fields, and whether it already exposes context usage, which
   would be cheaper than reading the transcript.
6. **Transcript location:** that `~/.claude/projects/*/<session-id>.jsonl` is stable enough
   for the CLI.

## 10. Testing

- **Unit tests (stdlib `unittest`, so running the tests needs nothing beyond Python):**
  - `policy.py`: exhaustive tables for the gate, nudge levels and rate limiting, the Stop
    decision, and breakpoints
  - `usage.py`: fixture transcripts, including truncated, empty, and no-usage cases
  - `config.py`: defaults, clamping, invalid values
  - `state.py`: atomic writes, corrupt reads, pruning, sanitisation
- **Hook contract tests:** run each hook as a subprocess with fixture stdin, and assert the
  exit code, stdout JSON, and state changes. This includes forced exceptions to prove the
  hooks fail open.
- **CLI tests:** run through subprocess, covering every command, every error message, and
  the exit codes.
- **CI:** GitHub Actions, `ubuntu-latest` and `macos-latest`, Python 3.9 to 3.13.
- **Manual end-to-end smoke test** (documented in the README): set
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW` low, hold, cross the threshold, see the block and the
  nudges, release, see compaction happen and the note come back.

## 11. Distribution

- **Install:** `.claude-plugin/marketplace.json` lets users run
  `/plugin marketplace add rhwendt/compactor` and then `/plugin install compactor`.
- **README:** what the plugin does and the limit on control (§1), quickstart (install, set
  the env var), the configuration table, the agent workflow, the status-line snippet,
  troubleshooting (`compactor status`, the error log), and the requirement for Python 3.9+
  on PATH.
- **Also in the repo:** MIT `LICENSE`, and `CHANGELOG.md` starting at `0.1.0`.

## 12. Implementation order (for the plan)

1. Resolve the §9 verification items.
2. Core: `config`, `state`, `policy.decide_gate`, `cli` (hold, release, note, status), the
   PreCompact, SessionStart and SessionEnd hooks, and SKILL.md. The plugin is usable from
   this point.
3. Usage: `usage.py`, the context lines, the ceiling in the gate, and `status --line`.
4. Nudges: UserPromptSubmit, PostToolUse nudges, and Stop.
5. Breakpoints.
6. Packaging: manifest, marketplace, README, CI, CHANGELOG.
