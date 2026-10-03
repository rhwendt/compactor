"""PostToolUse (all tools): nudges during autonomous work; Bash breakpoint suggestions; background
task tracking. Spec §5.2, §5.4."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

from .. import messages, subagents, tasks
from ..policy import invokes_compactor, is_breakpoint, should_suggest_breakpoint
from ._common import HookContext, HookResult, apply_nudge, with_context

_EXIT_CODE_TEXT = re.compile(r"^\s*(?:Error:\s*)?Exit code [1-9]\d*")


def bash_succeeded(payload: Dict[str, Any]) -> bool:
    """Best-effort success check across the Bash response shapes Claude Code has used."""
    response = payload.get("tool_response", payload.get("tool_output"))
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "return_code", "returnCode"):
            if isinstance(response.get(key), int):
                return response[key] == 0
        if response.get("is_error") or response.get("isError") or response.get("interrupted"):
            return False
        response = response.get("stdout", "")
    if isinstance(response, str) and _EXIT_CODE_TEXT.match(response):
        return False
    return True


def handle(ctx: HookContext) -> HookResult:
    subagents.demote_all(ctx.session_id, ctx.env)  # the main agent is acting, so not waiting on one
    state = ctx.load_state()
    if tasks.record(state, ctx.payload, ctx.now):
        ctx.save_state(state)
    if state.hold is None:
        return HookResult()
    is_bash = ctx.payload.get("tool_name") == "Bash"
    tool_input = ctx.payload.get("tool_input")
    command = str(tool_input.get("command") or "") if is_bash and isinstance(tool_input, dict) else ""
    if invokes_compactor(command):
        return HookResult()
    before = copy.deepcopy(state)
    usage = ctx.usage(state)
    parts: List[str] = []
    nudge_text = apply_nudge(ctx, state, usage)
    if nudge_text:
        parts.append(nudge_text)
    if is_bash and should_suggest_breakpoint(state.hold, usage, ctx.settings, state.nudge):
        kind = is_breakpoint(command, bash_succeeded(ctx.payload), ctx.settings.breakpoint_patterns)
        if kind:
            state.nudge.breakpoint_suggested = True
            parts.append(messages.breakpoint_suggestion(kind))
    if state != before:
        ctx.save_state(state)
    return with_context("PostToolUse", "\n".join(parts)) if parts else HookResult()
