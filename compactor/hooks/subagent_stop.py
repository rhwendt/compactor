"""SubagentStop: a real subagent finished; stop treating compactions as possibly its own."""
from __future__ import annotations

from .. import subagents
from ._common import HookContext, HookResult
from .subagent_start import is_real_subagent


def handle(ctx: HookContext) -> HookResult:
    if is_real_subagent(ctx):
        subagents.mark_stopped(ctx.session_id, ctx.payload.get("agent_id"), ctx.env)
    return HookResult()
