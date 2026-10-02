"""PreCompact (matcher "auto"): the gate, for the main agent and running subagents. Spec §5.1."""
from __future__ import annotations

from .. import messages
from ..policy import BLOCK, decide_compaction
from ..state import CeilingOverride, NudgeState, to_iso
from ._common import HookContext, HookResult


def handle(ctx: HookContext) -> HookResult:
    trigger = ctx.payload.get("compaction_trigger", ctx.payload.get("trigger"))
    if trigger not in (None, "auto"):
        return HookResult()
    state = ctx.load_state()
    usage = ctx.usage(state)
    active = ctx.active_subagents()
    decision = decide_compaction(state.hold, usage, active, ctx.settings, ctx.now)
    if decision.action == BLOCK and decision.subagent:
        return HookResult(exit_code=2, stderr=messages.subagent_gate_block(active, ctx.settings) + "\n")
    if decision.action == BLOCK and state.hold is not None:
        return HookResult(exit_code=2, stderr=messages.gate_block(state.hold, usage, ctx.settings, ctx.now) + "\n")
    if decision.override and state.hold is not None:
        early = usage is not None and usage.used < ctx.settings.ceiling_tokens(usage.window)
        state.ceiling_override = CeilingOverride(
            at=to_iso(ctx.now),
            pct=round(usage.pct, 1) if usage is not None else None,
            reason=state.hold.reason,
            growth_pct=round(100 * usage.growth / usage.window, 1) if early else None,
        )
        state.hold = None
        state.nudge = NudgeState()
        ctx.save_state(state)
    return HookResult()
