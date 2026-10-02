"""Pure decision logic. No I/O and no clock reads: every input is a parameter."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import List, Optional, Sequence

from .config import SUBAGENTS_HOLD, Settings
from .model import ActiveSubagent, Hold, NudgeState, Usage, from_iso

ALLOW = "allow"
BLOCK = "block"


@dataclass(frozen=True)
class GateDecision:
    action: str
    override: bool = False  # True when an active hold was overridden (ceiling or max hold age)
    subagent: bool = False  # True when blocking to hold a subagent's compaction (COMPACTOR_SUBAGENTS=hold)


def hold_age_minutes(hold: Hold, now: datetime) -> float:
    return (now - from_iso(hold.since)).total_seconds() / 60


def decide_gate(hold: Optional[Hold], usage: Optional[Usage], settings: Settings,
                now: datetime) -> GateDecision:
    """Spec §5.1: the first rule that applies decides."""
    if hold is None:
        return GateDecision(ALLOW)
    if usage is not None:
        if _at_ceiling(usage, settings):
            return GateDecision(ALLOW, override=True)
        return GateDecision(BLOCK)
    if hold_age_minutes(hold, now) >= settings.max_hold_min:
        return GateDecision(ALLOW, override=True)
    return GateDecision(BLOCK)


def _at_ceiling(usage: Optional[Usage], settings: Settings) -> bool:
    """At the ceiling, or one more turn like the last would reach it: a hold has no later chance
    to let compaction through once a turn's tool results have pushed context past the limit."""
    return usage is not None and usage.used + usage.growth >= settings.ceiling_tokens(usage.window)


def decide_compaction(hold: Optional[Hold], main_usage: Optional[Usage],
                      active: Sequence[ActiveSubagent], settings: Settings,
                      now: datetime) -> GateDecision:
    """Spec §5.1 with subagents. PreCompact can't say whose compaction it is, so while a real
    subagent runs: never block when any measured context is at its ceiling; otherwise block if
    either the subagent rule or (when the main agent may be the one compacting) the main rule
    says block. The main agent can't be compacting while every running subagent is foreground,
    since it is waiting on them."""
    if not active:
        return decide_gate(hold, main_usage, settings, now)
    all_foreground = all(sub.foreground for sub in active)
    if _at_ceiling(main_usage, settings):
        # With every subagent foreground the compaction is theirs: allow it, but leave the main
        # hold for the main agent's own next attempt to override.
        return GateDecision(ALLOW, override=hold is not None and not all_foreground)
    if any(_at_ceiling(sub.usage, settings) for sub in active):
        return GateDecision(ALLOW)  # a subagent's ceiling; the main hold stays
    # A subagent whose usage can't be read is not held blind: it could be near its hard limit.
    # Nor is the main agent: with its usage unknown and the compaction possibly its own, the main
    # gate's hold-age fallback must stay reachable.
    if (settings.subagents == SUBAGENTS_HOLD and all(sub.usage is not None for sub in active)
            and (main_usage is not None or all_foreground)):
        return GateDecision(BLOCK, subagent=True)
    if all_foreground:
        return GateDecision(ALLOW)
    if any(sub.usage is None for sub in active):
        # The compaction may be that unmeasurable subagent's, so the main hold may block it only
        # as a hold of unknown usage would (until COMPACTOR_MAX_HOLD_MIN); and since it may not
        # be the main agent's, letting it through doesn't override (clear) the main hold.
        return GateDecision(decide_gate(hold, None, settings, now).action)
    return decide_gate(hold, main_usage, settings, now)


STOP_UNKNOWN_USAGE_MIN = 30
LEVEL3_MARGIN = 0.05  # fraction of the window below the ceiling where nudges become urgent

_SEP = r"(?:^|[\s;&|(])"
_END = r"(?=$|[\s;&|)])"
_COMMIT = re.compile(_SEP + r"git(?:\s+-[Cc]\s+\S+)*\s+commit" + _END)
_TESTS = re.compile(
    _SEP + r"(?:"
    r"pytest|py\.test|python3?\s+-m\s+(?:pytest|unittest)"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test(?::[\w-]+)?"
    r"|go\s+test|cargo\s+test|make\s+(?:test|check)|mvn(?:\s+\S+)*?\s+test"
    r"|(?:\./)?gradlew?(?:\s+\S+)*?\s+test|rspec|(?:npx\s+)?(?:jest|vitest)"
    r")" + _END
)
_COMPACTOR = re.compile(r"^\s*(?:\S*/)?compactor(?:\s|$)")  # in command-word position
_SEGMENTS = re.compile(r"&&|\|\||[;|&\n]")


def _segments(command: str) -> List[str]:
    return [s for s in _SEGMENTS.split(command) if s.strip()]


def _effective_threshold(settings: Settings, usage: Usage) -> Optional[int]:
    """Where "past the threshold" starts: T, or the ceiling when T is set at or above it."""
    if settings.threshold is None:
        return None
    return min(settings.threshold, settings.ceiling_tokens(usage.window))


def _past_threshold(settings: Settings, usage: Usage) -> bool:
    threshold = _effective_threshold(settings, usage)
    return threshold is not None and usage.used >= threshold


def nudge_level(hold: Optional[Hold], usage: Optional[Usage], settings: Settings) -> int:
    """Spec §5.2: 0 = silent, 1 = past threshold, 2 = halfway to ceiling, 3 = near ceiling."""
    if hold is None or usage is None:
        return 0
    threshold = _effective_threshold(settings, usage)
    if threshold is None or usage.used < threshold:
        return 0
    ceiling = settings.ceiling_tokens(usage.window)
    if ceiling <= threshold or usage.used >= ceiling - LEVEL3_MARGIN * usage.window:
        return 3
    if usage.used >= threshold + (ceiling - threshold) / 2:
        return 2
    return 1


@dataclass(frozen=True)
class NudgeDecision:
    emit: bool
    level: int  # 0 with emit=True means a hold-age reminder (usage unknown)
    state: NudgeState


def decide_nudge(nudge: NudgeState, hold: Optional[Hold], usage: Optional[Usage],
                 settings: Settings) -> NudgeDecision:
    if hold is None:
        return NudgeDecision(False, 0, nudge)
    if usage is None:
        calls = nudge.calls_since + 1
        if calls >= settings.nudge_every:
            return NudgeDecision(True, 0, replace(nudge, calls_since=0))
        return NudgeDecision(False, 0, replace(nudge, calls_since=calls))
    level = nudge_level(hold, usage, settings)
    if level == 0:
        return NudgeDecision(False, 0, replace(nudge, last_level=0))
    if level > nudge.last_level or level == 3:
        return NudgeDecision(True, level, replace(nudge, last_level=level, calls_since=0))
    calls = nudge.calls_since + 1
    if calls >= settings.nudge_every:
        return NudgeDecision(True, level, replace(nudge, last_level=level, calls_since=0))
    return NudgeDecision(False, level, replace(nudge, last_level=level, calls_since=calls))


def should_block_stop(hold: Optional[Hold], usage: Optional[Usage], settings: Settings,
                      stop_hook_active: bool, already_blocked: bool, now: datetime) -> bool:
    """Spec §5.3: block ending a turn at most once, and only for a hold that matters."""
    if hold is None or stop_hook_active or already_blocked:
        return False
    if usage is not None:
        return _past_threshold(settings, usage)
    return hold_age_minutes(hold, now) >= STOP_UNKNOWN_USAGE_MIN


def should_suggest_breakpoint(hold: Optional[Hold], usage: Optional[Usage], settings: Settings,
                              nudge: NudgeState) -> bool:
    return (
        hold is not None
        and usage is not None
        and _past_threshold(settings, usage)
        and not nudge.breakpoint_suggested
    )


def is_breakpoint(command: str, succeeded: bool, extra_patterns: Sequence[str]) -> Optional[str]:
    """Spec §5.4: which kind of natural breakpoint a successful Bash command was, if any."""
    if not succeeded or not command:
        return None
    # Evaluate per segment: commit if any segment matches commit (without --dry-run in that segment)
    for segment in _segments(command):
        if _COMMIT.search(segment) and "--dry-run" not in segment:
            return "commit"
    # Then check for tests across all segments
    for segment in _segments(command):
        if _TESTS.search(segment):
            return "tests"
    # Custom patterns are searched in the whole command
    for pattern in extra_patterns:
        if re.search(pattern, command):
            return "custom"
    return None


def invokes_compactor(command: str) -> bool:
    """True when some segment of the command runs `compactor` itself, not just names a path."""
    return any(_COMPACTOR.match(segment) for segment in _segments(command or ""))
