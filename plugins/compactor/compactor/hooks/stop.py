"""Stop: catch a forgotten hold at the end of a turn, at most once per turn. Spec §5.3."""
from __future__ import annotations

import json

from .. import messages
from ..policy import should_block_stop
from ._common import HookContext, HookResult


def handle(ctx: HookContext) -> HookResult:
    state = ctx.load_state()
    if state.hold is None:
        return HookResult()
    usage = ctx.usage(state)
    if not should_block_stop(state.hold, usage, ctx.settings,
                             bool(ctx.payload.get("stop_hook_active")),
                             state.stop_blocked_this_turn, ctx.now):
        return HookResult()
    state.stop_blocked_this_turn = True
    ctx.save_state(state)
    reason = messages.stop_block(state.hold, usage, ctx.settings, ctx.now)
    return HookResult(stdout=json.dumps({"decision": "block", "reason": reason}))
