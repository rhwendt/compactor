# Changelog

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
