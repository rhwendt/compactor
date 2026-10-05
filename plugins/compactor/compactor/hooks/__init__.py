"""Hook entry points. dispatch() never raises, and never blocks because of its own failure."""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from typing import IO, Callable, Dict, Mapping, Optional

from ..config import load_settings, process_env
from ..state import append_error, utcnow
from . import (
    post_tool_use, pre_tool_use, precompact, session_end, session_start, stop, subagent_activity,
    subagent_start, subagent_stop, user_prompt_submit,
)
from ._common import HookContext, HookResult

HANDLERS: Dict[str, Callable[[HookContext], HookResult]] = {
    "precompact": precompact.handle,
    "session_start": session_start.handle,
    "session_end": session_end.handle,
    "user_prompt_submit": user_prompt_submit.handle,
    "pre_tool_use": pre_tool_use.handle,
    "post_tool_use": post_tool_use.handle,
    "stop": stop.handle,
    "subagent_start": subagent_start.handle,
    "subagent_stop": subagent_stop.handle,
}

# What runs for a payload that carries an agent_id (a subagent's event). Everything else skips
# such payloads: only subagent tracking acts on them, never nudges, gates or state writes.
AGENT_HANDLERS: Dict[str, Callable[[HookContext], HookResult]] = {
    "subagent_start": subagent_start.handle,
    "subagent_stop": subagent_stop.handle,
    "pre_tool_use": pre_tool_use.handle_agent,
    "post_tool_use": subagent_activity.handle,
}

# Still runs while the threshold var is unset, to tell the agent the plugin is off.
RUNS_WHEN_INACTIVE = {"session_start"}


def dispatch(event: str, stdin: Optional[IO[str]] = None, out: Optional[IO[str]] = None,
             err: Optional[IO[str]] = None, env: Optional[Mapping[str, str]] = None,
             now: Optional[datetime] = None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    env = process_env() if env is None else env
    try:
        if event not in HANDLERS:
            return 0
        payload = json.loads(stdin.read() or "{}")
        if not isinstance(payload, dict) or not payload.get("session_id"):
            return 0
        handler = AGENT_HANDLERS.get(event) if payload.get("agent_id") else HANDLERS[event]
        if handler is None:
            return 0
        settings = load_settings(env)
        if settings.disabled:
            return 0
        if settings.threshold is None and event not in RUNS_WHEN_INACTIVE:
            return 0
        result = handler(HookContext(payload=payload, settings=settings, env=env, now=now or utcnow()))
        if result.stdout:
            out.write(result.stdout)
        if result.stderr:
            err.write(result.stderr)
        return result.exit_code
    except Exception:
        append_error(f"{event}: {traceback.format_exc()}", env)
        return 0
