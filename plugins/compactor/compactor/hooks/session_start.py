"""SessionStart: CLI reminder, handoff note, ceiling-override notice, 1M window hint. Spec §5.5.

SessionStart(compact) also fires after a subagent compacts, with the parent's session_id and no
agent_id; the main agent's note and reminders would then land in the subagent's context."""
from __future__ import annotations

from typing import List

from .. import messages, subagents, tasks
from ..model import Note
from ..state import read_file_tail
from ..usage import LARGE_WINDOW
from ._common import HookContext, HookResult, with_context


def handle(ctx: HookContext) -> HookResult:
    source = ctx.payload.get("source")
    if not ctx.settings.active:
        if source == "startup":
            return with_context("SessionStart", messages.inactive_notice(ctx.settings))
        return HookResult()
    subagent_running = False
    if source == "compact":
        active = ctx.active_subagents()
        if subagents.owns_compaction(active):
            # A subagent's compaction: leave the main agent's note, notices and window cache alone.
            return HookResult()
        subagent_running = bool(active)
    state = ctx.load_state()
    # `model` names the compacting agent, which may be a running background subagent.
    model = None if subagent_running else ctx.payload.get("model")
    if state.window is None and isinstance(model, str) and model.endswith("[1m]"):
        state.window = LARGE_WINDOW  # the status line, when configured, reports the exact size later
        try:
            ctx.save_state(state)
        except OSError:
            pass  # only a cache; don't lose this SessionStart's output over it
    parts: List[str] = [messages.SUBAGENT_IGNORE] if subagent_running else []
    if source == "compact":
        if state.ceiling_override is not None:
            parts.append(messages.ceiling_notice(state.ceiling_override))
            state.ceiling_override = None
            try:
                ctx.save_state(state)
            except OSError:
                pass  # the notice may repeat later; don't lose this output over it
        if state.note is not None:
            parts.append(_handoff(state.note, after_compaction=True))
        if state.tasks and tasks.prune(state, ctx.transcript_path):
            try:
                ctx.save_state(state)
            except OSError:
                pass  # the list is rebuilt from the transcript next time; keep this output
        if state.tasks:
            parts.append(messages.tasks_after_compaction(state.tasks, ctx.now))
    elif source in ("resume", "fork") and state.note is not None:
        parts.append(_handoff(state.note, after_compaction=False))
    if state.hold is not None:
        parts.append(messages.hold_active(state.hold, ctx.now))
    parts.append(messages.CLI_REMINDER)
    return with_context("SessionStart", "\n\n".join(parts))


def _handoff(note: Note, after_compaction: bool) -> str:
    tail = read_file_tail(note.file) if note.file is not None else None
    return messages.handoff(note, after_compaction, tail)

