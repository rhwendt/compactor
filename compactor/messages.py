"""Every string the agent or user sees, in one place so the tone stays consistent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from .config import THRESHOLD_VAR, Settings
from .model import ActiveSubagent, CeilingOverride, Hold, Note, State, Usage, from_iso

CLI_REMINDER = (
    "compactor: you control auto-compaction in this session. Before fragile multi-step work run "
    '`compactor hold "<why>"`; at a safe breakpoint run `compactor release --note "<what matters>"`. '
    'Also: `compactor note "<text>"`, `compactor status`.'
)
NOTE_CLEARED = "Handoff note cleared."
# Leads SessionStart's injection while a subagent runs: the compaction may have been a
# background subagent's, and the main agent's note and hold then land in its context.
SUBAGENT_IGNORE = ("If you are a subagent: this is the main agent's note and hold — ignore them "
                   "and don't run compactor.")

LARGE_TURN_TOKENS = 20_000  # a turn this large gets a note that the ceiling may come a turn early


def fmt_tokens(n: int) -> str:
    if round(n / 1_000) >= 1_000:  # 999,600 would otherwise print as "1000k"
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def fmt_age(since: str, now: datetime) -> str:
    seconds = max(0, int((now - from_iso(since)).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{hours // 24}d"


def inactive_reason(settings: Settings) -> str:
    if settings.disabled:
        return "COMPACTOR_DISABLE is set"
    return f"{THRESHOLD_VAR} is not set"


def context_line(usage: Optional[Usage], settings: Settings, holding: bool = False) -> str:
    """Three lines that name every number, so the agent never guesses what a figure measures."""
    whose = "your hold" if holding else "a hold"
    lines = []
    if usage is None:
        lines.append("Context: usage unknown (no reply has reported it yet, or the transcript couldn't be read).")
    elif usage.window_known:
        lines.append(f"Context: {fmt_tokens(usage.used)} of {fmt_tokens(usage.window)} tokens used "
                     f"({usage.pct:.0f}% of the model's window).")
    else:
        lines.append(f"Context: {fmt_tokens(usage.used)} of {fmt_tokens(usage.window)} tokens used "
                     f"({usage.pct:.0f}% of the model's window — window size assumed; set "
                     "COMPACTOR_CONTEXT_WINDOW for exact figures).")
    if settings.threshold is None:
        lines.append(f"Auto-compact threshold: not set ({THRESHOLD_VAR}).")
    else:
        head = f"Auto-compact threshold: {fmt_tokens(settings.threshold)} ({THRESHOLD_VAR})"
        if usage is None:
            lines.append(head + ".")
        elif usage.used >= settings.threshold:
            below_ceiling = usage.used < settings.ceiling_tokens(usage.window)
            tail = "; your hold is what's stopping compaction." if holding and below_ceiling else "."
            lines.append(f"{head} — you are {fmt_tokens(usage.used - settings.threshold)} past it{tail}")
        else:
            lines.append(f"{head} — {fmt_tokens(settings.threshold - usage.used)} away.")
    if usage is None:
        lines.append(f"Safety ceiling: with usage unknown, a hold is overridden after "
                     f"{settings.max_hold_min} minutes (COMPACTOR_MAX_HOLD_MIN).")
    else:
        ceiling = settings.ceiling_tokens(usage.window)
        head = f"Safety ceiling: {fmt_tokens(ceiling)} ({settings.ceiling_pct:g}% of window)"
        away, growth = fmt_tokens(ceiling - usage.used), fmt_tokens(usage.growth)
        if usage.used >= ceiling:
            lines.append(f"{head} — reached; a hold is overridden at the next compaction check.")
        elif usage.used + usage.growth >= ceiling:
            lines.append(f"{head} — {away} away, but the last turn added {growth}, so a hold is "
                         "overridden at the next compaction check.")
        elif usage.growth >= LARGE_TURN_TOKENS:
            lines.append(f"{head} — {whose} is overridden there, {away} from now, or a turn sooner: "
                         f"the last turn added {growth}.")
        else:
            lines.append(f"{head} — {whose} is overridden there, {away} from now.")
    return "\n".join(lines)


def hold_active(hold: Hold, now: datetime) -> str:
    return f"A compaction hold is active: {hold.reason} (held {fmt_age(hold.since, now)})."


def gate_block(hold: Hold, usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    """Goes to stderr, which the human sees, so it describes the agent's hold in the third person."""
    return (
        f"compactor: auto-compaction held by the agent for {fmt_age(hold.since, now)}: {hold.reason}. "
        "It will release at a safe point; the safety ceiling overrides the hold near the context "
        f"limit.\n{context_line(usage, settings)}"
    )


def subagent_gate_block(active: Sequence[ActiveSubagent], settings: Settings) -> str:
    """Goes to stderr. PreCompact can't say whose compaction this is, only that subagents run."""
    lines = [
        "compactor: an auto-compaction is being held while a subagent runs; it may be the "
        "main agent's or the subagent's (Claude Code doesn't say whose). It waits until the "
        "subagent finishes or the compacting agent reaches its safety ceiling "
        "(COMPACTOR_SUBAGENTS=hold; set it to allow to let subagents compact)."
    ]
    for sub in active:
        if sub.usage is None:
            continue
        assumed = "" if sub.usage.window_known else " (window size assumed)"
        lines.append(
            f"Subagent {sub.agent_id}: {fmt_tokens(sub.usage.used)} of {fmt_tokens(sub.usage.window)} tokens "
            f"used{assumed}; safety ceiling {fmt_tokens(settings.ceiling_tokens(sub.usage.window))} "
            f"({settings.ceiling_pct:g}% of window)."
        )
    return "\n".join(lines)


_NUDGES = {
    0: "Compaction has been held {age} for: {reason}, and context usage can't be read. "
       "Release at your next natural breakpoint.",
    1: "Compaction is being held ({age}) for: {reason}. Release at your next natural breakpoint.",
    2: "You are well past the compaction threshold, holding ({age}) for: {reason}. "
       "Finish the current step, write a note, and release.",
    3: "The safety ceiling will override your hold at {ceiling}% context. Write a note and release now: "
       '`compactor release --note "<what matters>"`.',
}


def nudge(level: int, hold: Hold, usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    body = _NUDGES[level].format(age=fmt_age(hold.since, now), reason=hold.reason,
                                 ceiling=f"{settings.ceiling_pct:g}")
    return f"compactor: {body}\n{context_line(usage, settings, holding=True)}"


_BREAKPOINTS = {
    "commit": "A commit just landed",
    "tests": "Tests just passed",
    "custom": "A configured breakpoint command just succeeded",
}


def breakpoint_suggestion(kind: str) -> str:
    return (
        f"compactor: {_BREAKPOINTS[kind]} — that looks like a natural breakpoint. "
        'Consider `compactor release --note "<what matters>"`.'
    )


def stop_block(hold: Hold, usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    return (
        f"compactor: you're ending your turn with compaction held ({fmt_age(hold.since, now)}) "
        f"for: {hold.reason}. Either run `compactor release` (optionally with --note), or re-run "
        '`compactor hold "<current reason>"` if the work is still fragile.\n'
        f"{context_line(usage, settings, holding=True)}"
    )


def handoff(note: Note, after_compaction: bool) -> str:
    header = ("Handoff note you left before compaction" if after_compaction
              else "Handoff note from earlier in this session")
    return (
        f"compactor — {header} (written {note.updated_at}):\n{note.text}\n"
        '(Replace it with `compactor note "<text>"` or clear it with `compactor note --clear`.)'
    )


def ceiling_notice(override: CeilingOverride) -> str:
    if override.pct is not None and override.growth_pct is not None:
        why = (f"at {override.pct:.0f}% context by the safety ceiling, early: the last turn added "
               f"{override.growth_pct:.0f}% of the window, and another turn like it would have "
               "passed the ceiling before the next chance to compact")
    elif override.pct is not None:
        why = f"at {override.pct:.0f}% context by the safety ceiling"
    else:
        why = "because it outlived COMPACTOR_MAX_HOLD_MIN while context usage was unreadable"
    return (
        f"compactor: your hold ({override.reason}) was overridden {why}, and the conversation "
        'was compacted. Re-hold with `compactor hold "<why>"` if the work is still fragile.'
    )


def inactive_notice(settings: Settings) -> str:
    return (
        f"compactor plugin is installed but inactive ({inactive_reason(settings)}). "
        f"Set {THRESHOLD_VAR} (e.g. 350000) to enable agent-controlled compaction."
    )


def hold_set(hold: Hold, usage: Optional[Usage], settings: Settings, refreshed: bool) -> str:
    verb = "Hold updated" if refreshed else "Hold set"
    text = (
        f"{verb}: auto-compaction stays blocked until you run `compactor release`. "
        f"Reason: {hold.reason}.\n{context_line(usage, settings, holding=True)}"
    )
    if not settings.active:
        text += f"\nNote: {inactive_reason(settings)}, so the gate is inactive and this hold has no effect."
    return text


def released(had_hold: bool, noted: bool, usage: Optional[Usage], settings: Settings) -> str:
    if had_hold:
        text = "Released. Auto-compaction will proceed at the next threshold check."
    else:
        text = "No hold was set; nothing to release."
    if noted:
        text += " Handoff note saved; it will be re-injected after compaction."
    return f"{text}\n{context_line(usage, settings)}"


def note_set(chars: int) -> str:
    return (f"Handoff note saved ({chars} chars). It is re-injected after every compaction "
            "until you replace or clear it.")


def subagents_line(active: Sequence[ActiveSubagent]) -> str:
    scope = "(compactor sees Agent-tool subagents only, not background shell commands or monitors)"
    if not active:
        return f"subagents: none running {scope}"
    foreground = sum(1 for sub in active if sub.foreground)
    return (f"subagents: {len(active)} running ({foreground} foreground, "
            f"{len(active) - foreground} background) {scope}")


def status_text(state: State, usage: Optional[Usage], settings: Settings, now: datetime,
                last_err: Optional[str],
                subagents: Optional[Sequence[ActiveSubagent]] = None) -> str:
    """`subagents` None means unknown, and the line is left out rather than guessed."""
    if settings.active:
        mode = f"active (threshold {fmt_tokens(settings.threshold or 0)})"
    elif settings.disabled:
        mode = f"disabled ({inactive_reason(settings)})"
    else:
        mode = f"inactive ({inactive_reason(settings)})"
    lines = [f"compactor: {mode}"]
    if state.hold is not None:
        lines.append(f"hold: {state.hold.reason} ({fmt_age(state.hold.since, now)})")
    else:
        lines.append("hold: none")
    if state.note is not None:
        preview = state.note.text.replace("\n", " ")
        lines.append(f"note: {preview[:77] + '...' if len(preview) > 80 else preview}")
    else:
        lines.append("note: none")
    if subagents is not None:
        lines.append(subagents_line(subagents))
    lines.append(context_line(usage, settings, holding=state.hold is not None))
    lines.extend(f"config warning: {warning}" for warning in settings.warnings)
    if last_err:
        lines.append(f"last hook error: {last_err}")
    return "\n".join(lines)


def _pct_str(usage: Usage) -> str:
    """A "~" prefix when the window is assumed, since the percentage may be off (the tokens aren't)."""
    return f"{'' if usage.window_known else '~'}{usage.pct:.0f}%"


def status_line(hold: Optional[Hold], usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    if not settings.active:
        return "compactor off"
    if hold is not None:
        text = f"⏸ held {fmt_age(hold.since, now)}"
        return f"{text} · {_pct_str(usage)}" if usage is not None else text
    return f"▶ {_pct_str(usage)}" if usage is not None else "▶ compactor"
