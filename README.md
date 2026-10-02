# compactor

Agent-controlled auto-compaction for Claude Code. The agent **holds** auto-compaction during
fragile work, such as debugging chains or multi-file refactors, and **releases** it at a safe
breakpoint. It can leave a **handoff note** that comes back after compaction.

## What "control" means

Claude Code can't be told to compact early, by hooks or by the model. So compactor controls
**when compaction is allowed**:

- Below roughly your threshold, nothing compacts (Claude Code starts a little early; see
  [Known Claude Code behaviors](#known-claude-code-behaviors)).
- At or above it, compaction proceeds unless the agent holds it. Releasing lets it through
  at the next automatic check. The exception: by default, while a background subagent runs,
  compactions are held until the safety ceiling (see the same section).
- A **safety ceiling** (90% of the window by default) overrides any hold, so a session never
  runs into the hard limit. It counts tool output that arrived since the model's last reply,
  and lets compaction through early when one more turn as large as the last would reach it.

## Requirements

- Claude Code with plugin support
- `python3` (3.9 or newer) on your PATH. It uses the standard library only. On Windows, make
  sure `python3` resolves, for example through the Python launcher or an alias.

The test suite runs in CI on Linux, macOS and Windows (Python 3.9–3.13). Live sessions have
been checked on Linux only, so reports from macOS and Windows are welcome.

## Install

```
/plugin marketplace add rhwendt/compactor
/plugin install compactor@compactor
```

## Enable

compactor is inactive until you set a threshold, which also pulls auto-compaction below the
model's default. Add it to `~/.claude/settings.json`:

```json
{ "env": { "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350000" } }
```

Set the threshold below the model's context window, for example `350000` on 1M-context
models or `150000` on 200k models. The safety ceiling is always a percentage of the window,
so a threshold at or above it leaves the agent no room to hold.

Restart Claude Code. The agent is told about the commands at session start.

### Per project or per session

The plugin and the threshold are set separately, and each can be global, per project, or (for
the threshold) per session.

**Where the plugin is installed.** Pass `--scope` when installing from a shell in the project
folder:

```sh
claude plugin marketplace add rhwendt/compactor --scope local
claude plugin install compactor@compactor --scope local
```

| Scope | Active in | Recorded in |
|---|---|---|
| `user` (default) | all your projects | `~/.claude/settings.json` |
| `project` | this project, for everyone using the repo | `.claude/settings.json` (committed) |
| `local` | this project, just you | `.claude/settings.local.json` (not committed) |

**Where the threshold is set.** Put the `env` block in the matching settings file:

| To enable compactor for | Set `CLAUDE_CODE_AUTO_COMPACT_WINDOW` in |
|---|---|
| every project | `~/.claude/settings.json` |
| one project, everyone | the project's `.claude/settings.json` |
| one project, just you | the project's `.claude/settings.local.json` |
| one session | `claude --settings '{"env":{"CLAUDE_CODE_AUTO_COMPACT_WINDOW":"350000"}}'` |

Project settings override your user settings, and `--settings` overrides both. A value in any
settings file also beats one from your shell, so `CLAUDE_CODE_AUTO_COMPACT_WINDOW=500000 claude`
or an `export` in your shell profile has no effect while a settings file sets the variable.
The `COMPACTOR_*` options below can go in the same `env` blocks.

For example, to use compactor in one project only, install it with `--scope local` and add
this to that project's `.claude/settings.local.json`:

```json
{ "env": { "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350000" } }
```

### Updating

Claude Code caches an installed plugin by version. To pick up a new release, run both
commands, then start a new session:

```sh
claude plugin marketplace update compactor
claude plugin update compactor@compactor
```

## How the agent uses it

| Command | Effect |
|---|---|
| `compactor hold "<why>"` | Blocks auto-compaction until released. A reason is required. |
| `compactor release [--note "<text>"]` | Allows compaction again, optionally saving a handoff note. |
| `compactor note "<text>"` / `--clear` | Sets or clears the handoff note. |
| `compactor status [--json \| --line]` | Shows the hold, the note, running subagents, and context usage. |

While a hold is set and context is past the threshold, the agent receives nudges that get
firmer as usage grows. Each one ends with three lines like:

```
Context: 410k of 1M tokens used (41% of the model's window).
Auto-compact threshold: 350k (CLAUDE_CODE_AUTO_COMPACT_WINDOW) — you are 60k past it; your hold is what's stopping compaction.
Safety ceiling: 900k (90% of window) — your hold is overridden there, 490k from now.
```

It also receives a
one-time suggestion to release after a commit or a passing test run, and a reminder if it
tries to end its turn while holding. Holds are cleared when the session ends.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | unset (plugin inactive) | Auto-compact threshold in tokens (Claude Code accepts 100000 to 1000000) |
| `COMPACTOR_CEILING_PCT` | `90` | Percentage of the window at which a hold is overridden (50 to 98) |
| `COMPACTOR_CONTEXT_WINDOW` | inferred | Window size in tokens, for the context % |
| `COMPACTOR_NUDGE_EVERY` | `10` | Tool calls and prompts between repeat nudges |
| `COMPACTOR_BREAKPOINT_PATTERNS` | none | Extra regexes, separated by `;`, for commands that count as breakpoints |
| `COMPACTOR_MAX_HOLD_MIN` | `60` | Overrides a hold after this many minutes if usage can't be read |
| `COMPACTOR_SUBAGENTS` | `hold` | `hold`: a running subagent's auto-compactions wait until its task ends or it reaches its own safety ceiling (180k on a 200k window). While a background subagent runs, the main agent's compactions are held until its ceiling too, even without a hold. `allow`: subagents compact normally |
| `COMPACTOR_DISABLE` | unset | Any value turns the plugin off |

## Status line

`compactor status --line` prints `⏸ held 12m · 41%`, `▶ 41%`, or `compactor off`. It reads the
statusline JSON on stdin, which also teaches compactor your exact window size. In
`~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 \"$(ls -td ~/.claude/plugins/cache/*/compactor/*/bin/compactor | head -1)\" status --line"
  }
}
```

If you already have a status line script, pipe its stdin into that command and append the
output.

Exact context percentages come from, in order: an explicit `COMPACTOR_CONTEXT_WINDOW`; the
size this status line reported; a `model` ending in `[1m]` seen at session start; the latest
model identity Claude Code writes into the transcript (before the agent's first reply and after
each compaction), which also carries an `[1m]` suffix in 1M mode. Failing all of those, compactor assumes 200k until usage
passes 200k, and flags the guess in the context lines — "window size assumed" — since the
percentage (not the token counts) may then be wrong. The guess errs low on purpose: the safety
ceiling then lands early rather than past a 200k model's real limit.

## Known Claude Code behaviors

- **Auto-compaction starts ~25–35k tokens before your threshold.** Claude Code treats
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW` as the window and keeps its usual reserve below it, the way
  a 1M model compacts around 967k. With `100000` compaction was first attempted at 66k–75k, and
  with `150000` at 117k. If you want compaction to start nearer your number, set the value about
  35k higher. compactor doesn't change this; its context lines show your configured value.
- **Subagents compact too, and Claude Code doesn't say whose compaction it is.** compactor
  tracks running subagents and handles this as follows:
  - In the default `hold` mode, a subagent's compactions are held until its task ends or it
    reaches its own safety ceiling (180k on a 200k window, 900k on a 1M one).
  - While a **background** subagent runs, the main agent's compactions are held until the main
    ceiling too, even without a hold, because they can't be told apart. The cost is a larger
    context (and bill) until the subagent finishes.
  - `COMPACTOR_SUBAGENTS=allow` opts out: subagents compact normally, and a main-agent hold
    still applies.
  - A background subagent may receive the main agent's handoff note and hold notice after it
    compacts; they come with a line telling it to ignore them.

  This guesswork exists because Claude Code's PreCompact and SessionStart(`compact`) hooks
  don't say which agent is compacting. That is tracked upstream in
  [anthropics/claude-code#91910](https://github.com/anthropics/claude-code/issues/91910); once
  those payloads carry `agent_id`, compactor can attribute every compaction exactly.

## Troubleshooting

- Run `compactor status` in a session (or ask the agent to). It shows whether the plugin is
  active, any config warnings, and the last hook error.
- Hook errors are logged to `${XDG_STATE_HOME:-~/.local/state}/claude-compactor/errors.log`.
  Hooks always fail open, so a bug in compactor never blocks compaction.
- State lives in that same directory, one JSON file per session, and is pruned after 7 days.

## Manual smoke test

1. Set `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` and start `claude`.
2. Ask the agent to run `compactor hold "smoke test"`, then to read several large files until
   `compactor status` shows it past the threshold.
3. Confirm that auto-compaction does not run while the hold is set: context keeps growing past
   the threshold (`compactor status` shows how far past), and a "compactor: auto-compaction
   held by the agent" message may appear when Claude Code tries. While held past the
   threshold, the agent receives nudges on its tool calls and on your prompts.
4. Ask the agent to run `compactor release --note "smoke note"`, then send another prompt.
   Confirm that compaction runs at the next automatic check, and that the handoff note is
   re-injected: the agent can quote "smoke note" afterwards.

## Development

```
python3 -m unittest discover -s tests -t . -v
claude --plugin-dir .
```

Design: `docs/superpowers/specs/2026-09-29-compactor-design.md`.

## License

MIT
