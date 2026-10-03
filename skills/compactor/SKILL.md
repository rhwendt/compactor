---
name: compactor
description: Use when starting fragile multi-step work (debugging chains, multi-file refactors, long autonomous runs, dispatching subagents) in a session with the compactor plugin, or when a compactor message mentions a hold, nudge, ceiling, breakpoint or handoff note.
---

# compactor: you control auto-compaction

Auto-compaction fires when context passes the configured threshold. With this plugin you can
**hold** it during fragile work and **release** it at a safe point. You cannot start a
compaction yourself. Releasing lets the next automatic check go through.

## Commands (on your Bash PATH)

| Command | Use |
|---|---|
| `compactor hold "<why>"` | Before fragile work. The reason is required, so be specific. |
| `compactor release --note "<what matters>"` | At a natural breakpoint. The note comes back after compaction. |
| `compactor note "<text>"` / `compactor note --clear` | Update or clear the handoff note without releasing. |
| `--file <path>` on `note` or `release` | Also re-inject the last 40 lines of that file (such as your ledger) after compaction. |
| `compactor status` | Show the hold, the note, running subagents, background tasks, and context usage. |

## When to hold

- A debugging chain whose evidence so far lives only in this conversation
- A multi-file refactor or migration in progress
- A long autonomous run
- Before dispatching a subagent whose result you must act on (see "Subagents" below)

One hold can span many tasks: give it a reason that covers the stretch, such as
`compactor hold "executing plan 7, tasks 11-15"`. You don't need a new hold per task. Running
`hold` again only updates the reason and keeps the original start time.

## When to release

- A task is done, tests are green, or a commit landed
- You're switching topics
- A nudge says you're well past the threshold, or the ceiling is near
- Before ending your turn, unless the next turn continues the same fragile work

## Handoff notes

Before releasing, write down anything that isn't on disk yet and that you'd need after
compaction: the current hypothesis, the next steps, the file:line references in play, and
decisions made. End the note with the first thing to do after compaction, such as "read the
tail of docs/ledger.md", so you resume from that rather than by exploring. Prefer
`compactor release --note "..."` so the note and the release happen together. A note persists
across compactions until you replace or clear it, so clear stale notes with
`compactor note --clear`.

If your real recovery state lives in a file, such as a plan ledger, point the note at it with
`--file <path>`: the note then keeps a short pointer and the file's tail comes back with it.
Background Bash commands you started are listed after compaction with their IDs, so you can
still stop them with TaskStop; you don't need to copy the IDs into the note.

## Subagents

- Before releasing, check `compactor status`: its `subagents:` line shows running Agent-tool
  subagents and its `background tasks:` lines show background Bash commands you started.
  Monitors aren't tracked, so check your own task list for those.
- Before dispatching a subagent whose report you must act on, hold:
  `compactor hold "waiting on <task> report"`. Keep holding after the report arrives until you
  have analysed it and done the work it calls for (edits, tests, a handoff note), then release.
  Don't release just because a report arrived.
- When you dispatch a long subagent task, tell it to write findings to a file as it goes and to
  report that file's path. Its compactions are held until it reaches its own safety ceiling,
  but they can still happen there, and its report is only as good as what survives.
- Only the main agent runs `compactor hold`, `release` and `note`. Subagents and teammates
  share the main agent's session, so their attempts are refused by a hook. If you are one,
  don't try; ignore any compactor note or hold notice you receive (`compactor status` is fine).
- You don't need to watch the model's hard limit yourself: the safety ceiling shown in the
  context lines overrides the hold near the limit.

## Rules

- Never hold without a specific reason.
- Never end an autonomous run with a hold set.
- Don't release and re-hold in the same turn just to silence nudges.
- Release doesn't mean compact now. Compaction happens at the next threshold check.
- The safety ceiling overrides any hold near the context limit, so write your note before
  that happens.
- Use the context lines in nudges (tokens used, your auto-compact threshold, the safety
  ceiling) to decide when to release.
- If line 1 says the window size is assumed, treat the percentage as approximate; the token
  counts and the threshold are still exact.
