# Changelog

## [0.2.1](https://github.com/rhwendt/compactor/compare/v0.2.0...v0.2.1) (2026-10-05)


### Bug Fixes

* ship only the plugin's runtime files, and add a listing icon ([2b5af29](https://github.com/rhwendt/compactor/commit/2b5af2976acba570ef0ac70f657fe00b7a6ea534))

## 0.2.0 (2026-10-03)

- Fix: subagents and agent-team teammates can no longer take over the main agent's hold or
  handoff note. They share its session (same session ID and environment), so the CLI can't tell
  them apart; a new PreToolUse hook on Bash refuses `compactor hold/release/note` when the call
  carries an `agent_id`. `compactor status` stays allowed.
- Background Bash tasks survive compaction: compactor records each background command's task
  ID when it starts and forgets it on TaskStop or when its completion notice appears. Running
  tasks are listed after compaction and in `compactor status` (text and `--json`).
- `compactor note --file <path>` / `release --file <path>`: re-inject the last 40 lines (up
  to 4 KB) of a file, such as a plan ledger, with the note after compaction.

## 0.1.2 (2026-10-02)

- `compactor status` lists running Agent-tool subagents (foreground/background), as text and in
  `--json`, so the agent can check before releasing. Background shell commands and monitors
  aren't visible to compactor, and the line says so.
- Skill guidance: one hold can span many tasks, so there's no need to re-hold per task; and a
  handoff note should end with the first thing to do after compaction.

## 0.1.1 (2026-10-02)

- Fix: the context window is read from the transcript's most recent model-identity entry.
  Previously only the first 64 KB was searched, so long or resumed sessions fell back to a
  guess. On a 1M model that could mean an assumed 200k window and a 180k safety ceiling.

## 0.1.0 (2026-10-01)

Initial release.

- `compactor hold` / `release` / `note` / `status`: the agent holds auto-compaction during
  fragile work and releases it at a safe breakpoint.
- Handoff notes, re-injected after every compaction and on resume.
- A safety ceiling (a percentage of the context window, 90% by default) that overrides any
  hold, plus a hold-age fallback when context usage can't be read. Usage includes tool
  output not yet reported by the model, and the ceiling allows compaction a turn early when
  the last turn's growth would reach it.
- Usage-aware nudges on tool calls and prompts, with context lines that name every number.
- Breakpoint suggestions after Bash commits and passing test runs, and a reminder when the
  agent ends its turn while holding.
- Context window detection from the status line, the session's model, or the transcript's
  model identity; a guessed window is flagged as assumed.
- `compactor status --line` for the status line.
- Subagent-aware gating: a running subagent's compactions are held until its task ends or it
  reaches its own ceiling (`COMPACTOR_SUBAGENTS=hold`, the default; `allow` opts out).
- Holds are cleared when the session ends. Hooks fail open.
- Agent guidance (skill), including holding while waiting on a subagent's report.
