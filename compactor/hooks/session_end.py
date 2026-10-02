"""SessionEnd: a session never leaves a stale hold behind. Spec §5.6."""
from __future__ import annotations

from .. import subagents
from ..state import NudgeState
from ._common import HookContext, HookResult


def handle(ctx: HookContext) -> HookResult:
    subagents.clear(ctx.session_id, ctx.env)
    state = ctx.load_state()
    if state.hold is None and not state.stop_blocked_this_turn:
        return HookResult()
    state.hold = None
    state.nudge = NudgeState()
    state.stop_blocked_this_turn = False
    ctx.save_state(state)
    return HookResult()
