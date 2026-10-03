"""PreToolUse (Bash): only the main agent may change the hold or the handoff note.

Subagents and in-process teammates share the main agent's session (the same
CLAUDE_CODE_SESSION_ID and environment), so the CLI can't tell who called it. Their tool hooks
do carry agent_id, so a teammate's `compactor hold/release/note` is denied here."""
from __future__ import annotations

import json

from .. import messages
from ..policy import compactor_state_command
from ._common import HookContext, HookResult


def handle(ctx: HookContext) -> HookResult:
    return HookResult()  # the main agent: nothing to check


def handle_agent(ctx: HookContext) -> HookResult:
    tool_input = ctx.payload.get("tool_input")
    if ctx.payload.get("tool_name") != "Bash" or not isinstance(tool_input, dict):
        return HookResult()
    command = compactor_state_command(str(tool_input.get("command") or ""))
    if command is None:
        return HookResult()
    return HookResult(stdout=json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": messages.TEAMMATE_DENIED.format(command=command),
    }}))
