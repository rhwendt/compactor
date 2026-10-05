"""PostToolUse from a subagent (payload has agent_id): only keep its running-marker alive.

No nudges and no state writes: the subagent shares the main agent's session, and holds belong
to the main agent. A marker that expired while the subagent ran one long step is recreated."""
from __future__ import annotations

from .. import subagents
from ._common import HookContext, HookResult
from .subagent_start import is_real_subagent


def handle(ctx: HookContext) -> HookResult:
    subagents.touch(ctx.session_id, ctx.payload.get("agent_id"), ctx.now, is_real_subagent(ctx), ctx.env)
    return HookResult()
