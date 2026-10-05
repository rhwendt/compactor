"""SubagentStart: remember that a real subagent is running, so the gate can tell. Spec §5.1."""
from __future__ import annotations

from .. import subagents
from ._common import HookContext, HookResult


def is_real_subagent(ctx: HookContext) -> bool:
    """Claude Code's compaction-summary agents report an empty agent_type."""
    agent_type = ctx.payload.get("agent_type")
    return isinstance(agent_type, str) and bool(agent_type)


def handle(ctx: HookContext) -> HookResult:
    if is_real_subagent(ctx):
        subagents.mark_started(ctx.session_id, ctx.payload.get("agent_id"), ctx.now, ctx.env)
    return HookResult()
