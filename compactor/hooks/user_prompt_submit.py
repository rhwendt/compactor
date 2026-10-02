"""UserPromptSubmit: start-of-turn nudge, and reset the once-per-turn Stop flag. Spec §5.2."""
from __future__ import annotations

import copy

from .. import subagents
from ._common import HookContext, HookResult, apply_nudge, with_context


def handle(ctx: HookContext) -> HookResult:
    subagents.demote_all(ctx.session_id, ctx.env)  # the main agent is acting, so not waiting on one
    state = ctx.load_state()
    before = copy.deepcopy(state)
    state.stop_blocked_this_turn = False
    text = apply_nudge(ctx, state, ctx.usage(state)) if state.hold is not None else None
    if state != before:
        ctx.save_state(state)
    return with_context("UserPromptSubmit", text) if text else HookResult()
