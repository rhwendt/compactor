"""Shared plumbing for hook handlers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from .. import messages, subagents
from ..config import Settings
from ..policy import decide_nudge
from ..state import State, load, save
from ..usage import Usage, read_usage


@dataclass
class HookContext:
    payload: Dict[str, Any]
    settings: Settings
    env: Mapping[str, str]
    now: datetime

    @property
    def session_id(self) -> str:
        return str(self.payload["session_id"])

    def load_state(self) -> State:
        return load(self.session_id, self.env)

    def save_state(self, state: State) -> None:
        save(self.session_id, state, self.env)

    @property
    def transcript_path(self) -> Optional[str]:
        path = self.payload.get("transcript_path")
        return path if isinstance(path, str) else None

    def usage(self, state: State) -> Optional[Usage]:
        return read_usage(self.transcript_path, self.settings, state.window)

    def active_subagents(self) -> List[subagents.ActiveSubagent]:
        return subagents.running(self.session_id, self.transcript_path, self.now, self.env)


@dataclass
class HookResult:
    exit_code: int = 0
    stdout: Optional[str] = None
    stderr: Optional[str] = None


def with_context(event_name: str, text: str) -> HookResult:
    body = {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}
    return HookResult(stdout=json.dumps(body))


def apply_nudge(ctx: HookContext, state: State, usage: Optional[Usage]) -> Optional[str]:
    """Advance the nudge rate limiter in `state`; return the nudge text if one is due."""
    decision = decide_nudge(state.nudge, state.hold, usage, ctx.settings)
    state.nudge = decision.state
    if not decision.emit or state.hold is None:
        return None
    return messages.nudge(decision.level, state.hold, usage, ctx.settings, ctx.now)
