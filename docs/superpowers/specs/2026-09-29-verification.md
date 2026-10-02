# Payload verification (2026-09-29)

Captured with Claude Code 2.1.284.

## Findings
- PreCompact trigger field: `trigger` = `"manual"` (invoked via `claude -p "/compact" --continue`).
  `compaction_trigger` does not appear anywhere in the payload.
- PostToolUse (Bash) response field: `tool_response`; shape: object with keys `stdout`, `stderr`,
  `interrupted`, `isImage`, `noOutputExpected`. No `exit_code`/`exitCode`/`return_code`/
  `returnCode`/`is_error`/`isError` key is ever present, even on failure (see below) — `tool_output`
  never appears as a key name.
- Failing Bash (`false`) fires PostToolUse: **no**. Confirmed three separate ways with
  `claude -p`: (1) `true` then `false` in one session — only `true` produced a PostToolUse file;
  (2) `false` alone in a session — zero PostToolUse files; (3) `false` then `true` (failure run
  *first*, not last, to rule out an end-of-process race) — again only the `true` call produced a
  PostToolUse file. So this isn't a "last call before the process exits" timing artifact; a
  nonzero-exit Bash call simply does not trigger the PostToolUse hook headlessly on this version.
  Failure is indicated by: n/a — there is no payload to read a failure signal from, because the
  hook never runs. (This was only verified headlessly; interactive-TUI behavior was not tested —
  driving an interactive session isn't possible in this environment.)
- Stop payload has `stop_hook_active`: yes (present, observed value `false` in both headless runs;
  a `true` value was not exercised — that requires an actual blocked-stop loop).
- SessionStart `source` for a headless run: `"startup"`. Also observed: `"resume"` after
  `claude -p ... --continue`, and `"compact"` immediately after a `/compact` completes (the latter
  also carries a `model` field, e.g. `"claude-opus-5-5[1m]"`).
- `agent_id` present in main-session payloads: no. Not present on any captured SessionStart,
  UserPromptSubmit, PostToolUse, Stop, SessionEnd, or PreCompact payload.

## Plan adjustments

1. **PreCompact trigger lookup — no change needed.** The real payload only ever has `trigger`
   (never `compaction_trigger`). `payload.get("compaction_trigger", payload.get("trigger"))`
   already resolves correctly in this case, since the default expression is evaluated and
   returns `payload.get("trigger")` = `"manual"` when `compaction_trigger` is absent. Task 9 can
   keep the lookup as specified; the `compaction_trigger` branch is just unreachable defensive
   code with real payloads, which is fine to leave in case a future Claude Code version renames
   the field back.

2. **`bash_succeeded` — use `tool_response`, and treat "no error keys present" as success.**
   The plan's dict-shape check must key off `tool_response` (confirmed field name; `tool_output`
   never appears). The real success shape is
   `{"stdout": "", "stderr": "", "interrupted": false, "isImage": false, "noOutputExpected": false}`
   — none of `exit_code`/`exitCode`/`return_code`/`returnCode`/`is_error`/`isError` is present.
   Task 10 must implement the dict branch so that a dict with none of those keys present, and
   `interrupted` falsy or absent, counts as **success** (`bash_succeeded` returns `True`) — it must
   not require one of the exit-code/is_error keys to exist. Keep the documented keys
   (`exit_code`/`exitCode`/`return_code`/`returnCode`, `is_error`/`isError`) and the
   `"Exit code N"`/`"Error: Exit code N"` string-prefix branches as defensive fallbacks for other
   Claude Code versions/shapes, but they are not exercised by any payload captured here.

3. **`post_tool_use_bash_fail.json` is missing — the payload cannot be captured.** A failing
   (`false`, exit 1) Bash command never fires the PostToolUse(Bash) hook headlessly in this
   version, confirmed three ways above (see Findings). Since there is no real payload to sanitize,
   per Ruling R2, Task 10's test that would load this fixture must `skipTest` with a reason string
   naming this file and pointing at this section, and instead exercise the dict/string failure
   branches from Item 2 with synthetic (hand-written) payloads — those shapes remain
   spec/documentation-derived, not empirically verified.

4. **Second-order consequence for §5.4 breakpoint detection (informational, not a required code
   change):** because PostToolUse(Bash) never fires headlessly for a failing command, any logic
   gated on that hook (e.g. `is_breakpoint`) will in practice never see a failed Bash invocation
   in headless sessions — there's no payload for it to misclassify. This is safe for breakpoint
   detection (which only wants to confirm success), but it means a "last command failed" signal
   cannot be derived from PostToolUse alone; worth a one-line comment in Task 10's code so a future
   maintainer doesn't assume the failure branch runs against real headless hook traffic. If this
   matters for interactive sessions, it needs a manual interactive check outside this spike.

## Smoke test (2026-09-30, Task 12)

Run headlessly with Claude Code 2.1.284, one `claude -p --plugin-dir .` session (a prior
`--allowedTools "Bash,Read" "<prompt>"` invocation errored out before reaching the model — see
"CLI usage note" below — and does not count as an attempt). Isolated environment:
`XDG_STATE_HOME=$(mktemp -d)`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000`. The agent was told to
run `compactor hold "smoke test"`, read `docs/superpowers/plans/2026-09-29-compactor.md` (and
more files if needed) until `compactor status` showed it past the threshold, run `compactor
status`, run `compactor release --note "smoke note"`, read one more file, run `compactor status`
again, and report what it observed. Session id `03c667c6-6bf9-4d6f-b249-65bd9f09cbf1`; transcript
at `~/.claude/projects/<project>/03c667c6-6bf9-4d6f-b249-65bd9f09cbf1.jsonl`.
State file at `$XDG_STATE_HOME/claude-compactor/03c667c6-6bf9-4d6f-b249-65bd9f09cbf1.json`;
`errors.log` was never created (no hook exceptions).

**CLI usage note:** `claude -p --plugin-dir . --allowedTools "Bash,Read" "<prompt>"` fails with
`Error: Input must be provided either through stdin or as a prompt argument when using --print`,
because `--allowedTools <tools...>` is variadic and swallows the following positional prompt
argument. Piping the prompt on stdin (`cat prompt.txt | claude -p --plugin-dir . --allowedTools
"Bash,Read"`) works. This is a `claude` CLI argument-parsing quirk, not a compactor bug, and
doesn't affect the README (its smoke test targets interactive `claude`, not `claude -p`).

**Timeline (from the transcript, all times UTC):**

| Time | Event |
|---|---|
| 05:31:28 | Session start; compactor's SessionStart hook injects the "you control auto-compaction" context. |
| 05:31:31 | `compactor hold "smoke test"` → context 28k/200k (14%), 72k below the 100k threshold. |
| 05:31:31–39 | Agent reads the 3,787-line plan doc in full (paged, since Read caps at 25k tokens per call). |
| 05:31:39 | `compactor status` → `hold: smoke test (7s)`, context 100k/200k (50%), 365 tokens past threshold. |
| 05:31:44 | `compactor status` again → context 101k/200k (50%), 656 tokens past threshold. |
| 05:31:46 | `compactor release --note "smoke note"` → hold cleared, note saved (`updated_at 05:31:46Z`). |
| 05:32:12 | `compact_boundary` fires, `trigger: "auto"`, `preTokens: 101393`, `postTokens: 5698`. SessionStart(compact) hook re-injects the handoff note: *"compactor — Handoff note you left before compaction (written 2026-09-30T05:31:46Z):\nsmoke note"*. |
| 05:32:16 | `compactor status` → `hold: none`, `note: smoke note`, context 26k/200k (13%). |

**Findings:**

1. **Gate blocks while held, and the block retries after release — spec §9 item 2 answer: yes.**
   Context was past the 100k threshold (100k, then 101k) for roughly 15 seconds while `hold:
   smoke test` was active, and no compaction happened in that window. The only `compact_boundary`
   in the transcript is `trigger: "auto"` at 05:32:12, 26 seconds *after* `compactor release` ran
   at 05:31:46 — i.e. at the next automatic threshold check following the release, not before it.
   This matches the design ("release doesn't mean compact now; compaction happens at the next
   threshold check") and answers §9 item 2: a blocked auto-compact does retry, and it went through
   as soon as the hold was lifted. Caveat: this Claude Code version does not log PreCompact hook
   invocations as visible transcript entries the way it does for SessionStart (no
   `hook_success`/`hook_non_blocking_error`/`hook_additional_context` entries were found anywhere
   in the transcript for PreCompact, PostToolUse, or UserPromptSubmit, only for SessionStart), so
   this conclusion rests on the absence of compaction during the held, over-threshold window plus
   its prompt appearance right after release, not on a directly logged "blocked" message from the
   hook itself.
2. **Handoff note re-injection: confirmed.** The state file's `note.text` is `"smoke note"` with
   `updated_at` matching the release call. The transcript shows the `SessionStart:compact` hook
   firing right after the compaction and injecting exactly that note text as additional context.
   The agent's own final answer confirmed it could see and quote "smoke note" and correctly
   attributed it to that injected message rather than to its own instructions.
3. **Nudges: not exercised by this run — explained, not a defect.** No nudge text appeared
   anywhere in the transcript during the held, over-threshold window. Reading
   `compactor/hooks/post_tool_use.py` explains why: the PostToolUse(Bash) hook returns early via
   `invokes_compactor(command)` whenever the Bash command itself is a `compactor` invocation, and
   every Bash command this smoke test ran while holding was `compactor hold`/`compactor status`
   (which already print their own context/threshold/ceiling lines), while the large files were
   read with the `Read` tool, which never triggers `PostToolUse(Bash)` at all. `UserPromptSubmit`
   only fires once per submitted prompt, and this was a single-prompt `-p` run with the hold set
   after that one prompt, so it never re-ran while holding either. A nudge check would need a
   scenario with an ordinary (non-`compactor`) Bash command run while holding past threshold, or a
   second submitted prompt while still holding — neither happened here. Recorded as "not run /
   inconclusive" for the nudge path specifically.
4. **Stop-hook reminder: not exercised.** The hold was released before the agent's only turn
   ended, so the "reminder if it tries to end its turn while holding" path was never triggered.
   "not run / inconclusive" for this path.
5. **No hook errors.** `errors.log` was never created under the isolated `XDG_STATE_HOME`,
   consistent with hooks failing open with nothing to report.
6. **Window-size aside (not in scope for this task):** the model reports as `claude-opus-5-5[1m]`
   but compactor inferred a 200k window rather than 1,000,000, since no statusline JSON was piped
   in during this `-p` run to teach it the real window size (`COMPACTOR_CONTEXT_WINDOW`/statusline
   inference, spec §6). This only affected the percentage figures shown, not the pass/fail
   threshold math (`CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` in absolute tokens), so it didn't
   affect the findings above.

### Conflicting evidence

In fairness, the tested agent's own final verdict in this session (transcript line ~103,
2026-09-30T05:32:32Z) disagrees with Finding 1 above. Quoted verbatim:

> The smoke test only partly passed. The handoff note came back after compaction, but a
> compaction also happened while the hold was still set, and I never saw a PreCompact block
> message or any nudge.
>
> [...] **A compaction happened after step 5, before the release.** My context was summarized
> while the hold was set. I can't tell whether it was automatic or manual. If it was automatic at
> about 101k, the hold didn't stop it, and that goes against what the plugin is meant to do.

This is the agent's honest read of what it experienced, but the objective transcript record
contradicts it on the ordering:

- **Only one `compact_boundary` exists in the transcript**, at 05:32:12.619Z, `trigger: "auto"`,
  `preTokens: 101393`. The `compactor release --note "smoke note"` tool call is at 05:31:46.152Z
  and its tool result ("Released. Auto-compaction will proceed at the next threshold check...")
  lands at 05:31:46.428Z — both about **26 seconds before** the compact_boundary, not after it.
- **Token usage grew continuously through the hold and the release call, with no drop before
  it.** The per-turn `usage` totals (input + cache tokens) climb monotonically across the hold:
  27,567 → 27,858 → 52,631 → 100,365 → 100,656 → **101,154 at 05:31:46.152Z (the release
  call itself)**. The next assistant turn's usage, at 05:32:14.855Z (after the compact_boundary),
  is 23,094 — that is the only drop anywhere in the transcript, and it falls strictly after both
  the release and the compact_boundary, not between step 5's status check and the release.
- **The likely source of the agent's mistaken claim is Claude Code's own post-compaction
  continuation message**, not a misreading invented by the agent from nothing. That message (the
  standard "This session is being continued from a previous conversation that ran out of
  context... Summary: ..." boilerplate injected as the first turn after compaction) itself lists
  step 6 (`compactor release`) under "Pending Tasks" and asserts "A compaction occurred right
  after [step 5], while the hold was still set." The raw transcript contradicts that ordering:
  the release tool call (entry 59, 05:31:46.152Z) and its successful result (entry 60,
  05:31:46.428Z) come first, then the `compact_boundary` (entry 64, 05:32:12.619Z), and only
  after the boundary is the continuation summary injected. So release had already completed
  about 26 seconds before the compaction the summary describes. Once compaction ran, the agent no longer had its own earlier turns to check
  this against and reasonably took the injected summary at face value, then repeated its framing
  in the final report quoted above.

Taken together, the objective record (a single `auto`-triggered `compact_boundary` timestamped
after the release call, and continuous pre-release token growth with the only drop occurring
after both events) supports Finding 1's conclusion — the hold blocked compaction, and it went
through only after release — over the tested agent's own verdict. As with Finding 1, this
remains an **inference from timing and the raw usage/tool-call log**, not a directly observed
"PreCompact blocked" transcript entry (this Claude Code version doesn't log PreCompact hook
invocations as visible entries at all, whether they block or not). The nudge and Stop-reminder
findings are unaffected by this and remain "not run / inconclusive" as stated above.

**Overall:** the core guarantee — a hold blocks auto-compaction, release lets the next
auto-compact check through, and a handoff note survives compaction and is quoted back — is
verified end to end with direct evidence (state file and transcript timeline), notwithstanding
the tested agent's own contradictory verdict addressed above, which the objective record does not
support. The nudge and stop-reminder paths were not exercised by this particular run and remain
"not run / inconclusive"; they are unit-tested (Task 10/`test_policy_nudge.py`) but this smoke
test didn't happen to trigger them headlessly.

## Subagents, trigger point and retries (2026-09-30)

Captured with Claude Code 2.1.284.

### Method

A throwaway capture-only plugin (`compactor-capture2`, not committed) registered PreCompact,
PreToolUse, PostToolUse, UserPromptSubmit, Stop, SessionStart, SessionEnd, SubagentStart and
SubagentStop hooks, each appending one JSON line (timestamp, event name, full payload, and for
PreCompact a usage figure computed from the payload's `transcript_path`) to a log file, with a
configurable exit-2 block predicate. The compactor plugin itself was **not** loaded in any of
these runs, so results reflect Claude Code alone. The subagent tool name was confirmed as
`Agent` (grepped from an existing project transcript before spending a run on it).

Five `claude -p --plugin-dir <capture>` runs were made (of a 6-run budget), each in a fresh
`git clone` of this repo so there were real files to read:

- **R1** — T=100000, unblocked, main spawns one general-purpose subagent that reads the plan,
  design spec, and every file under `compactor/`/`tests/`, then re-reads the plan.
- **R2** — same as R1, with a block predicate intended to block only subagent compaction
  (`SPIKE_BLOCK=subagent`: exit 2 when `agent_id` is present in the PreCompact payload).
- **R3** — main only (no subagent), T=100000, block predicate intended to block only main
  compaction (`SPIKE_BLOCK=main`: exit 2 when `agent_id` is absent) until usage ≥ 140000, reading
  files one at a time.
- **R4** — main only, T=150000, unblocked, same slow one-file-at-a-time read-through.
- **R5** — same task as R1, T=100000, with an unconditional block predicate
  (`SPIKE_BLOCK=all_auto`: exit 2 on every `trigger:"auto"` PreCompact, added after R2 showed the
  `agent_id`-keyed predicate never fires — see H2/H3).

Evidence comes from the SPIKE_LOG files, the main session transcripts
(`~/.claude/projects/<proj>/<session>.jsonl`), and subagent transcripts, which this spike found
live at `~/.claude/projects/<proj>/<session>/subagents/agent-<agent_id>.jsonl`, each with a
sibling `agent-<agent_id>.meta.json` (`agentType`, `description`, `toolUseId`, `model`, etc.).
Lines inside a subagent transcript carry `sessionId` equal to the **parent's** session id and
their own `agentId`.

### H1–H8 summary

| # | Hypothesis | Verdict | Key evidence |
|---|---|---|---|
| H1 | Subagents auto-compact at the configured window | CONFIRMED | R1 subagent transcript (`agent-a48185e5eb10a223d.jsonl`) has 5 `compact_boundary` entries, `trigger:"auto"`, preTokens 74789/74928/64857/65196/74326 at `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000`. R2's subagent: 65739/73682/73186/73500/73293. |
| H2 | PreCompact fires for subagent compaction and carries `agent_id` | CONFIRMED it fires; REFUTED that it carries `agent_id` | All 10 PreCompact firings across R1+R2 have payload keys exactly `['custom_instructions','cwd','hook_event_name','prompt_id','session_id','transcript_path','trigger']` — no `agent_id`/`agentId`/`subagent_id` key ever appears. `session_id` and `transcript_path` are the **parent's**, byte-identical to the main session's own (e.g. `633a81b7-9065-4949-be3b-972e4084a939.jsonl`), even though the main `.jsonl` itself has **zero** `compact_boundary` entries in R1/R2 (all 5+5 compactions are the subagent's). Timing confirms attribution: each PreCompact hook call in R1 (14:31:16, 14:31:48, 14:32:31, 14:33:20, 14:34:04) precedes its matching subagent `compact_boundary` (14:31:39.985, 14:32:15.502, 14:33:09.606, 14:33:58.891, 14:34:38.558) by roughly that boundary's own `durationMs` (23728ms, etc.). |
| H3 | Exit 2 blocks a subagent's auto-compaction | INCONCLUSIVE as designed; CONFIRMED via modified predicate | R2's `agent_id`-present predicate never matched (`block_decision:"not blocking"` on all 5 of its PreCompact firings) — direct consequence of H2, so R2's subagent compacted on the same schedule as R1's (unblocked). R5, blocking every `trigger:"auto"` PreCompact unconditionally, blocked all 11 attempts (`block_decision:"blocking"` each time); R5's subagent transcript (`agent-af4d5ed9f55a53bab.jsonl`) has **0** `compact_boundary` entries despite reaching 242,123 tokens of usage (vs. the ~65k–75k where R1/R2's unblocked subagents compacted), and only 1 `SubagentStop` fired (its real, final one) instead of the 6 seen in R1/R2. |
| H4 | Subagent Bash sees the parent's `CLAUDE_CODE_SESSION_ID` | CONFIRMED | Main: `MAIN_SID=633a81b7-9065-4949-be3b-972e4084a939`. Subagent: `SID=633a81b7-9065-4949-be3b-972e4084a939` — identical. Full `env \| grep -iE 'claude\|agent'` dumps from the main and subagent Bash calls were identical line-for-line, including `CLAUDE_PID=1409430` (same OS process), `CLAUDE_CODE_BRIDGE_SESSION_ID`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_CODE_MESSAGING_SOCKET`/`_TOKEN`, `CLAUDE_EFFORT`, `CLAUDE_CODE_CHILD_SESSION=1`, `CLAUDE_CODE_SESSION_ATTENDED=0`. No env var distinguished the subagent from the main session. (Caveat: this spike's own orchestrating agent is itself a subagent of another session, so `CLAUDE_CODE_CHILD_SESSION`/`_ATTENDED` are inherited noise from that outer harness — present equally in both dumps, so it doesn't affect the main-vs-subagent comparison.) |
| H5 | PreToolUse carries `agent_id` for subagent calls; something fires after subagent compaction; SubagentStart/Stop payload contents | CONFIRMED, with an added finding | `PreToolUse`/`PostToolUse` for subagent tool calls carry `agent_id` (e.g. `a48185e5eb10a223d`) and `agent_type` (`"general-purpose"`); main-session tool calls have neither key. `SessionStart` fires after a subagent compacts, with `source:"compact"`, timed to the millisecond against each subagent `compact_boundary` (R1: 14:31:40.216, 14:32:15.744, 14:33:09.848, 14:33:59.138, 14:34:38.827) — but, like PreCompact, carrying the parent's `session_id` and no `agent_id`. `SubagentStart` payload keys: `session_id, transcript_path, cwd, prompt_id, agent_id, agent_type, hook_event_name` (fires once, matching the real subagent). `SubagentStop` payload additionally has `permission_mode, effort, stop_hook_active, agent_transcript_path, last_assistant_message`. Unexpectedly, `SubagentStop` fired **6 times** for R1's one subagent, not once (see "surprising" below). |
| H6 | Real trigger point is below the configured threshold; characterize the gap at 100k and 150k | CONFIRMED (below threshold both times); gap looks closer to a roughly fixed token buffer than a fixed percentage | See exact numbers below. |
| H7 | Retry cadence and usage per attempt after a PreCompact block | CONFIRMED: retries happen on (approximately) every subsequent tool call, not on a coarser per-turn or boundary-only cadence | See exact numbers below. |
| H8 | Mid-task subagent auto-compaction loses information and degrades the final report | CONFIRMED | See detail below. |

### H6 — exact numbers

- T=100000 (unblocked): first-compaction `preTokens` = **74789** (R1) and **65739** (R2) — 74.8%
  and 65.7% of the configured window; gap below threshold = 25,211 and 34,261 tokens
  respectively. (Consistent with this spike's earlier informal baseline of preTokens≈70160 at
  T=100000.)
- T=150000 (unblocked, main session, R4): first-compaction `preTokens` = **117286** — 78.2% of
  the configured window; gap below threshold = 32,714 tokens. R4's subsequent compactions:
  preTokens 132004, 116965, 115071 (gaps 17,996 / 33,035 / 34,929).
- The absolute gaps (≈25k–35k tokens) are noticeably more consistent across the two thresholds
  than the percentages are (65.7%–78.2%), i.e. the data here fits "roughly fixed token buffer
  reserved below the configured window" better than "fixed percentage of the window" — though the
  gap itself still varies by about 9k tokens (25,211 vs. 34,261 at the same threshold in R1 vs.
  R2), so it is not perfectly fixed either.

### H7 — exact numbers

R3 (main only, T=100000, blocked while usage < 140000): 92 total PreCompact attempts in 3
blocking cycles, and **35 PreToolUse events occurred in the same window as the first 35 PreCompact
attempts (1:1)** — i.e. essentially every tool call re-attempted PreCompact while blocked.

- Cycle 1: 35 attempts, usage climbing attempt-over-attempt 53236 → 80114 → 101061 → 101173 →
  110911 → ... → 140010 (2s–5s apart), unblocked at 140010 (≥140000), real `compact_boundary`
  landed at preTokens=140114 seconds later.
- Cycle 2: 9 attempts, 64215 → 68638 → ... → 148117, unblocked, real `compact_boundary` at
  preTokens=148221.
- Cycle 3: 46 attempts, 66941 → 67053 → ... → 104808, still blocking when the 1200s run ended
  (never reached 140000 again in the time available).

### H8 — detail

**(a) Continuation summary content, instruction survival.** R1's first compaction summary
(subagent transcript, injected 2026-09-30T14:31:39.984Z) quotes the subagent's original task
verbatim under "Primary Request and Intent": *"The user asked for this, verbatim: 'First run
`env \| grep -iE 'claude\|agent'` ... Report all one-line summaries plus the env/SID output.'"* —
the task instructions (env/SID step, file list and order, "summarise each in one line") survived
intact through all 5 compactions. However, each summary's recovery pointer — *"If you need
specific details from before compaction ... read the full transcript at:
.../633a81b7-9065-4949-be3b-972e4084a939.jsonl"* — names the **main** session's transcript, not
the subagent's own pre-compaction transcript (which actually lives at
`.../subagents/agent-a48185e5eb10a223d.jsonl`); following that pointer would read the wrong
conversation.

**(b) Final-report quality, with vs. without compaction (same prompt, R1 vs. R5).** R1 (5
compactions) reports `compactor/`'s 15 files individually but groups `tests/`'s 13 files
generically ("`test_cli`, `test_config`, ... : one file per module or feature above") and its 6
fixtures collectively, with no per-file test detail, and surfaces no plan-vs-code discrepancies.
R5 (same task, all auto-compaction blocked) itemizes all 32 files/groups individually with
specific detail (e.g. `tests/test_cli.py`: "a concurrent hold, an idle input pipe, and UTF-8
output on a Windows code page") and additionally reports four concrete plan-vs-code discrepancies
that R1's report never mentions (ceiling calculation, which hooks get nudged, who the block
message addresses, missing window-guess logic). Files read before/around R1's earlier
compactions (plan, spec, `compactor/*`) kept detailed per-file summaries; files read across/after
the later compactions (`tests/*`, fixtures) were compressed or grouped — consistent with
information loss compounding over successive compactions.

**(c) Main agent's awareness.** Grepping the main session transcript for "compact" found 143
matches, but every one traces back to the subagent's own prose about the compactor plugin (its
reading subject matter, not the subagent's own runtime state) — none is a system-injected notice
that the subagent itself compacted. No phrase resembling "context ran out", "previous
conversation" or "compaction happened" appears anywhere in the subagent's final report or the
main agent's relay of it to the user. The main agent's transcript shows no sign it knew its
subagent had compacted 5 times.

### Surprising

`SubagentStop` does not fire once per subagent — it fires once per **compaction cycle**, for
compactions of the main session too, not just subagents. R1's one real subagent produced 6
`SubagentStop` events: 5 with a distinct, throwaway `agent_id` (different from the real
subagent's `a48185e5eb10a223d` each time) and `agent_type:""`, each timestamped to the
millisecond against a `compact_boundary`, each with an `agent_transcript_path` pointing at its
own disposable `subagents/agent-<throwaway-id>.jsonl` file, and `last_assistant_message`
containing the full `<analysis>/<summary>` continuation text (i.e. it *is* the compaction
summary) — plus one final, real `SubagentStop` (`agent_id:"a48185e5eb10a223d"`,
`agent_type:"general-purpose"`) at actual task completion. R4 (main session only — `Agent` was
not even in `--allowedTools`) shows the identical pattern for its own 4 compactions: 4
`SubagentStop` events with throwaway `agent_id`s, empty `agent_type`, and their own
`subagents/agent-*.jsonl` transcripts. This indicates Claude Code implements the
compaction-summary generation step — for the main session and for subagents alike — as an
internal, ephemeral "subagent" call that fires (SubagentStart-less) `SubagentStop` hooks with a
disposable agent id.

### Implications for compactor (facts only)

- A PreCompact hook running for a subagent's compaction sees a payload with no `agent_id` and a
  `session_id`/`transcript_path` equal to the parent's — it cannot tell, from the PreCompact
  payload alone, whether it is compacting the main session or a subagent.
- Anything keyed by `session_id` from PreCompact or from a post-compact SessionStart
  (`source:"compact"`) would collide between the main session and any of its subagents, since both
  report the same `session_id` in those payloads.
- `PreToolUse`/`PostToolUse` payloads do carry `agent_id`, so state keyed off tool-use events (not
  PreCompact/SessionStart) can distinguish subagent activity from main activity.
- Exiting 2 from PreCompact reliably blocks the compaction it was called for — including a
  subagent's — when the hook isn't conditioned on `agent_id` (since `agent_id` isn't available at
  PreCompact time).
- Every compaction (main or subagent) also fires an ephemeral `SubagentStop`-shaped hook event
  with a throwaway `agent_id`, empty `agent_type`, and its own `subagents/agent-*.jsonl`
  transcript. A hook listening on `SubagentStop` that assumes it only fires for genuine
  Agent/Task-tool subagents will also see these compaction pseudo-events.
- The real auto-compact trigger point observed here was consistently below the configured
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (65.7%–78.2% of it across three thresholds/runs), with an
  absolute gap of roughly 25k–35k tokens rather than a clean percentage.
- Once PreCompact keeps returning non-zero, Claude Code retries at (approximately) every
  subsequent tool call — not on a coarser per-turn or boundary-only cadence — so a hook that
  blocks compaction should expect very frequent re-invocation while the hold is active.
- A subagent whose context auto-compacts mid-task can still recite its original instructions
  verbatim afterward (they're preserved in the continuation summary), but per-file/per-item detail
  for work done further from the point of compaction was observably compressed relative to an
  otherwise-identical run with compaction blocked, and the main agent that spawned the subagent
  has no visible signal that any of this happened.

## Live subagent gating check (2026-09-30)

Captured with Claude Code 2.1.284, on the pre-release development branch (after the subagent
gate, before the note-leak fix).

### Method

Two runs were made against real headless `claude -p` sessions (of a 3-run budget; the third,
optional control run was skipped — see "Runs executed" below). Each run used a fresh
`git clone` of that development version, its own `XDG_STATE_HOME`, and
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` / `COMPACTOR_CEILING_PCT=50` (a ceiling of 50% of
whatever window compactor infers for each agent — 100k for the subagent's assumed 200k window).
Both the real `compactor` plugin and a throwaway, never-committed
observability plugin (a scratch directory outside the repo)
were loaded together with two `--plugin-dir` flags:

```
claude -p --plugin-dir <compactor checkout> --plugin-dir <capture> \
  --allowedTools "Bash,Read,Grep,Glob,Agent,Task"
```

**Multiple `--plugin-dir` flags do work together**: both plugins' hooks fired for every event in
both runs — the capture plugin logged 45 JSON lines per run (`SessionStart`, `PostToolUse`,
`SubagentStart`/`Stop`, `PreCompact`) to `$SPIKE_LOG`, always exiting 0, while compactor's own
hooks independently produced the hold/status/release text seen in the transcripts and gated
compaction. No conflict or missing-hook behavior was observed; this needed no workaround.

The capture plugin (`hooks/capture.py`) reads each hook's JSON stdin, adds a wall-clock
timestamp, and appends it as one line to `$SPIKE_LOG`; it never inspects or alters behavior.
Evidence below is drawn from `$SPIKE_LOG`, the main session transcript
(`~/.claude/projects/<proj>/<session>.jsonl`), the subagent transcript
(`<session>/subagents/agent-<agent_id>.jsonl`, with its sibling `.meta.json`), compactor's
state file, and its (empty, both runs) `errors.log`. Per-event `usage` at each `PreCompact` was
computed from the subagent transcript's own `message.usage` fields (input + both cache fields +
output) at the nearest preceding timestamp, not guessed.

Each main-agent prompt: run `compactor hold "waiting on subagent review"` then
`compactor note "main note: R<n> marker"`; spawn exactly one `general-purpose` subagent,
model `sonnet`, foreground (no other main-agent tool calls while it ran), with the task "read
the plan, then the design spec, then every file under `compactor/` alphabetically, then the plan
again, one summary line per file, in order"; wait for it to return; then run `compactor status`
and `compactor release`.

**Model/window actually inferred**: the subagent's `meta.json` reports `"model": "sonnet"` with
no `[1m]` suffix, and its compaction ceiling behaved exactly as the 200k-assumed default predicts
(gated below ~100,000 tokens, allowed at/over it — see R1 below), confirming compactor inferred
the 200k default for it, as intended. The **main** agent's own context, unexpectedly, was
reported throughout as "X k of **1M** tokens" (e.g. `29k of 1M tokens used (3% of the model's
window)` immediately after the first `compactor hold`, before any compaction). This `claude -p`
environment's top-level model apparently carries a `[1m]`-suffixed identity somewhere Claude Code
writes (per the README's fallback chain), even though the `SessionStart` payload's own `model`
field read back as `"claude-sonnet-5-5"` with no visible suffix in the captured payload
*(superseded — see "Root cause and fix": that `model` is the compacting subagent's, not the main agent's)*. This did
not affect the test: the main agent's own ceiling (500k) was never remotely approached (usage
stayed at 29k-31k throughout both runs), so it never competed with the subagent-ceiling path
being tested.

### Runs executed

- **R1** — `COMPACTOR_SUBAGENTS` unset (default `hold`). Completed, two subagent compaction
  cycles observed.
- **R2** — `COMPACTOR_SUBAGENTS=allow`. Completed, three subagent compaction cycles observed
  (subagent then hit Claude Code's own "autocompact is thrashing" circuit breaker and exited
  early with an API error — see "Surprising" below).
- **R3** (control, no main hold) — **skipped**. The task's own instructions made R3 conditional
  on R1/R2 coming back clean; R1/R2 instead surfaced a reproducible note-leak bug (below), so the
  third run was not worth spending out of the 3-run budget.

### R1 (hold mode, default) — expectations vs. observed

Main session `ab402eb5-3095-4f22-b6e2-20715429d813`; subagent `agent-a87f2b19e0f2b111a`
(general-purpose, sonnet, `requestShape: "foreground"`).

| # | Expectation | Verdict | Evidence |
|---|---|---|---|
| 1 | Subagent's compactions below its ~100k ceiling are BLOCKED (exit 2, no `compact_boundary`) | **PASS** | `PreCompact` fired at usage ≈59,370 (16:37:24) and ≈86,191 (16:37:27) — both below the 100k ceiling — with no matching `compact_boundary` in the subagent transcript at that usage; the next `PreCompact` at 16:37:47, usage ≈109,738 (over ceiling), was followed 21s later (matching the compaction's own `durationMs`) by the real `compact_boundary` (`preTokens: 130454`). Same pattern repeated for the second cycle: blocked at ≈55,652 (16:38:12), allowed at ≈101,773 (16:38:15) → `compact_boundary` `preTokens: 116771` at 16:38:42. |
| 2 | Allowed at/over ceiling (~100k), `compact_boundary` preTokens ≥ ~100k | **PASS** | `preTokens: 130454` and `preTokens: 116771`, both ≥ 100,000 (see above). |
| 3 | Main hold survives (no `ceiling_override` in state; `compactor status` still shows the hold) | **PASS** | Final `compactor status` (run after the subagent returned): `"hold: waiting on subagent review (1m)"`. State file's `ceiling_override: null` throughout; it only became `null`+`hold: null` at the end because the main agent itself ran `compactor release`. |
| 4 | SessionStart(compact) after the subagent's compaction injects NO main note | **FAIL** | Both post-compaction `SessionStart` hook outputs (16:38:08.318Z and 16:38:42.318Z), attached directly into the **subagent's own transcript** (`hookName: "SessionStart:compact"`), contain the main agent's handoff note verbatim: `"compactor — Handoff note you left before compaction (written 2026-09-30T16:37:13Z):\nmain note: R1 marker\n..."` plus the hold-active notice. This happened on **both** of R1's two compactions. |
| 5 | Markers created at SubagentStart, removed at SubagentStop | **PASS** | `SubagentStart` (16:37:16, `agent_id: a87f2b19e0f2b111a`, `agent_type: general-purpose`) creates the marker; it survives two intervening pseudo `SubagentStop` events for the compaction-summary agents (`agent_type: ""`, ids `af4b6adc567dff064` and `af19ae1a9e55794ea` — correctly ignored, since `is_real_subagent` requires non-empty `agent_type`); the real final `SubagentStop` (16:38:50, same `agent_id`, `agent_type: general-purpose`) removes it. The post-run markers directory is empty. |
| 6 | No `errors.log` | **PASS** | `errors.log` absent/empty. |

### R2 (allow mode) — expectations vs. observed

Main session `cac97646-ad58-46fc-97fd-25236990ccb4`; subagent `agent-ac71188909c62d26a`
(general-purpose, sonnet, foreground).

| # | Expectation | Verdict | Evidence |
|---|---|---|---|
| 1 | Subagent compacts at Claude Code's normal trigger (~65-75k) despite the main hold | **PASS** | Three compactions, exactly one `PreCompact` immediately before each (no blocked retries, unlike R1): `preTokens` 74,320 (16:46:21), 67,781 (16:46:43), 66,830 (16:47:16) — all in the 65-75k range documented for T=100000, nowhere near the 100k ceiling that governed R1. |
| 2 | Main hold still intact | **PASS** | Final `compactor status`: `"hold: waiting on subagent review (1m)"`, unchanged by any of the three subagent compactions. |
| 3 | No main note leaked | **PARTIAL FAIL** | Leaked on 2 of 3 compactions (16:46:21 and 16:47:16: `hookSpecificOutput.additionalContext` containing `"main note: R2 marker"`, verbatim in the subagent's own transcript), but **correctly suppressed** on the middle one (16:46:43/44: the `SessionStart:compact` hook's `stdout` was empty, i.e. compactor's own `if active and owns_compaction(...): return HookResult()` path fired as designed that time). This confirms the suppression logic is not simply absent — it's timing-sensitive and intermittent. |

### Surprising

- **The handoff-note suppression after a subagent's own compaction is unreliable, not absent.**
  Across both runs, 4 of 5 subagent compactions leaked the main agent's note (and hold-active
  notice) into the subagent's own context; 1 of 5 (R2's second) correctly suppressed it. Isolated
  unit-level testing of `subagents.owns_compaction()` against a byte-accurate reconstruction of
  the subagent transcript *as it stood at the exact hook-invocation timestamp* (truncated to just
  before the `SessionStart` fired, `now` set to the real captured time, delta ≈0.2-0.3s) returns
  `True` as designed — so the pure decision function is correct given accurate live-marker input.
  The leak therefore most likely comes from `ctx.active_subagents()` (i.e. the running-subagent
  marker lookup) intermittently returning empty *(superseded — disproved, see "Root cause and
  fix": the boundary isn't on disk yet)* at the moment `session_start.py` runs, even
  though the marker should still be well within its 15-minute idle window — this could not be
  confirmed directly because the marker files no longer exist post-run to inspect, and
  `errors.log` was empty both times (this is a silent logic gap, not a crash). This is worth a
  maintainer follow-up: the note/hold-notice leak into a subagent's post-compaction context is a
  real, reproducible behavior, not a one-off fluke, but it isn't deterministic either.
- **R2's subagent hit Claude Code's own circuit breaker.** After its third compaction the
  subagent was terminated early by Claude Code itself: `"Agent terminated early due to an API
  error: Autocompact is thrashing: the context refilled to the limit within 3 turns of the
  previous compact, 3 times in a row..."`. This is a Claude Code safety net, unrelated to
  compactor, but it means R2 never got a report back from the subagent (the main agent still
  completed `compactor status`/`release` cleanly) — and it's a real illustration of the exact
  failure mode `COMPACTOR_SUBAGENTS=hold` exists to prevent.
- Neither run's main agent ever came close to its own (500k, since its window was read back as
  1M) ceiling or threshold; both main transcripts show zero `compact_boundary` entries of their
  own, confirming every observed compaction in both runs was the subagent's.
- R1's own final report ("it used about 48k tokens and 29 tool calls") looked, at first glance,
  too cheap to have ever reached the 100k ceiling — that figure is just the subagent's *final*
  usage after two compactions reset it, not its peak. The transcript's own `message.usage` and
  `compact_boundary.preTokens` fields (not the agent's self-report) are what confirm it actually
  crossed the ceiling twice; self-reported token counts from a compacted agent should not be
  trusted for this kind of check.


### Root cause and fix (2026-09-30)

- **Root cause.** Claude Code stamps the compacting agent's `compact_boundary` ~0.2 s before
  `SessionStart(compact)` fires, but writes it to the transcript only after every
  SessionStart hook has returned (the boundary, the summary and the hook attachments land in one
  batch; entries stamped after the hook finished sit before the hook's own attachments). An
  instrumented repro confirmed it: while compactor's SessionStart hook ran, the subagent
  transcript held 0 boundaries, its marker was live, and `owns_compaction` returned `False`;
  the boundary appeared on disk ~0.12 s after the hook exited. The old rule ("a boundary
  within 30 s") therefore only ever saw the *previous* compaction's boundary, which predicts
  all five R1/R2 outcomes exactly: leaked when there was none or it was 33–34 s old,
  suppressed only in R2's second compaction, whose predecessor was 22.9 s old. The earlier
  "markers intermittently empty" guess was wrong, and so was the replay that included the
  boundary on the strength of its timestamp.
- **Fix.** A `SessionStart(compact)` is the subagent's when every running subagent
  is foreground (the gate's own invariant: the main agent is waiting on them). While a
  background subagent runs it counts as the main agent's and the note is injected, which is a
  documented limitation. This also stops a main compaction within 30 s of a background
  subagent's from losing its note. Follow-up fix: the payload's `model` is the
  *compacting* agent's (`claude-sonnet-5-5` from the sonnet subagent in all three instrumented
  and live compactions, while the main agent ran `claude-opus-5-5`), so the window cache is no
  longer written from a compaction while any subagent runs. This also corrects the note above
  that the main agent's payload read back as sonnet.
- **Live confirmation** (R1 method, with both fixes): the subagent compacted once
  (`preTokens` 129906). Its `SessionStart:compact` got no compactor output, and "FIX marker"
  appears 0 times in the subagent transcript. The main agent's hold survived, and
  `status`/`release` ran normally. The main agent never compacted, so its note path was covered
  by unit tests only.
- **Fix round 1.** R2's foreground subagent ended with an API error: an `is_error` tool_result
  for its Agent call (main transcript, 16:47:22.319) and no SubagentStop, PostToolUse or Stop.
  Its marker stayed foreground, so a main compaction right after it would have been
  attributed to the subagent. A foreground marker now counts only while its Agent call's
  `toolUseId` has no tool_result in the main transcript. The timing holds: in the
  instrumented run, all 4 PreCompacts found the triggering tool_result already on disk
  0.11–0.13 s earlier, so PostToolUseFailure wasn't needed. The failed-call path was not
  re-verified live; the repro budget was spent, and no cheap way to make a foreground Agent
  call fail with an API error was found.

## Background subagent window check (2026-10-01)

Captured with Claude Code 2.1.284. One `claude -p --model sonnet` run in a fresh clone with
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000`. The main agent launched one general-purpose subagent
with `run_in_background: true`. The subagent's `.meta.json` records
`"requestShape":"background"`, and the result's agent stats show `requested.background: 1`.
The subagent read the plan, the spec and every `.py` file, then re-read the plan.

The subagent's transcript has three `trigger:"auto"` compact boundaries. preTokens were 75941,
122352 and 152453.

- **First compaction (75941):** the request before it was at 60587, then one Read added about
  63k characters. That is the same ~25–35k below the 100k window as foreground subagents (H1)
  and the main agent.
- **Second and third compactions (122352, 152453):** these overshoot because of batching, not
  because the window differs.
  - Before the second, one assistant turn issued 37 parallel Reads starting from 63410.
  - Before the third, a turn issued 3 large Reads starting from 111212.
  - Claude Code checks the threshold only between model requests, so a single turn's tool
    results all land before the check.

**Verdict:** background subagents inherit `CLAUDE_CODE_AUTO_COMPACT_WINDOW` exactly as
foreground ones do. A turn that issues many parallel reads can push usage past the window
before compaction runs. That applies to any agent, the main one included.

## Overflow under a hold (2026-10-01)

Captured with Claude Code 2.1.284, headless `claude -p --model haiku` (Haiku 4.5, a 200k window
whose transcript identity has no `[1m]` suffix). The compactor plugin and the throwaway capture
plugin were both loaded. The agent ran `compactor hold`, then read large files in rounds of
parallel Read/Bash calls.

**Bug 1: a 200k model with a threshold above 200k.** With `CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000`,
Claude Code limited the threshold to the model's window and compacted at preTokens 188711.
compactor guessed a 1M window from the threshold, which put the ceiling at 900k, so a hold would
have blocked there. Fixed: compactor guesses 1M only once usage passes 200k.

**Bug 2: the gate's usage figure lagged by a turn.** compactor read usage from the last
assistant reply. PreCompact fires as a reply arrives, while the reply's tool calls are still
running, so the large results in flight weren't counted.

- *First run* (threshold 150k, ceiling 98%): usage went 27k → 104.5k → 182.8k. Both PreCompacts
  were blocked: the gate saw 104.5k and then 182.8k against a 196k ceiling. The next request
  failed with "Prompt is too long", which ended the session.
- *A/B run* (threshold 150k, default 90% ceiling, 7 new ~28k-character files per round):
  - `main` died with "Prompt is too long".
  - The first fix, which counted only output already written to disk, survived narrowly. At the
    first PreCompact only one result was on disk (113k seen), so it blocked. It allowed the
    second at 188k, and compaction then ran with preTokens 232102. Claude Code compacted a
    context larger than the window, but no margin was left.
  - The final fix projects growth from the last turn as well. It allowed the first
    PreCompact, compaction ran at preTokens 157163 and then 156083, and the run finished. The
    agent was told its hold was overridden.

Usage now counts content added since the last reply (characters ÷ 3). The gate allows
compaction when `used + growth` reaches the ceiling, where growth is the larger of that pending
content and the last turn's increase.
