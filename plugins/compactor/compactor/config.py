"""Environment-variable configuration. Invalid values fall back to defaults and never raise."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

THRESHOLD_VAR = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
# Every environment variable compactor reads. process_env() copies only these, so the rest of
# the environment (tokens, keys, anything else) never passes through compactor.
ENV_KEYS = (
    "CLAUDE_CODE_SESSION_ID",
    THRESHOLD_VAR,
    "CLAUDE_CONFIG_DIR",
    "XDG_STATE_HOME",
    "COMPACTOR_BREAKPOINT_PATTERNS",
    "COMPACTOR_CEILING_PCT",
    "COMPACTOR_CONTEXT_WINDOW",
    "COMPACTOR_DISABLE",
    "COMPACTOR_MAX_HOLD_MIN",
    "COMPACTOR_NUDGE_EVERY",
    "COMPACTOR_SUBAGENTS",
)
DEFAULT_CEILING_PCT = 90.0
MIN_CEILING_PCT = 50.0
MAX_CEILING_PCT = 98.0
DEFAULT_NUDGE_EVERY = 10
DEFAULT_MAX_HOLD_MIN = 60
SUBAGENTS_HOLD = "hold"
SUBAGENTS_ALLOW = "allow"


def process_env() -> Dict[str, str]:
    """The ENV_KEYS that are set in this process's environment, and nothing else."""
    return {key: os.environ[key] for key in ENV_KEYS if key in os.environ}


@dataclass(frozen=True)
class Settings:
    threshold: Optional[int] = None
    ceiling_pct: float = DEFAULT_CEILING_PCT
    context_window: Optional[int] = None
    nudge_every: int = DEFAULT_NUDGE_EVERY
    max_hold_min: int = DEFAULT_MAX_HOLD_MIN
    breakpoint_patterns: Tuple[str, ...] = ()
    disabled: bool = False
    subagents: str = SUBAGENTS_HOLD  # hold a subagent's compactions until its ceiling, or allow them
    warnings: Tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.threshold is not None and not self.disabled

    def ceiling_tokens(self, window: int) -> int:
        """Token count at which a hold is overridden. Never above the window, even when T is."""
        return int(window * self.ceiling_pct / 100)


def _positive_int(env: Mapping[str, str], name: str, warnings: List[str]) -> Optional[int]:
    raw = (env.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        warnings.append(f"{name}={raw!r} is not an integer; ignored")
        return None
    if value <= 0:
        warnings.append(f"{name}={raw!r} must be positive; ignored")
        return None
    return value


def _ceiling_pct(env: Mapping[str, str], warnings: List[str]) -> float:
    raw = (env.get("COMPACTOR_CEILING_PCT") or "").strip()
    if not raw:
        return DEFAULT_CEILING_PCT
    try:
        value = float(raw)
        if math.isnan(value):
            raise ValueError(raw)
    except ValueError:
        warnings.append(f"COMPACTOR_CEILING_PCT={raw!r} is not a number; using {DEFAULT_CEILING_PCT:g}")
        return DEFAULT_CEILING_PCT
    clamped = min(max(value, MIN_CEILING_PCT), MAX_CEILING_PCT)
    if clamped != value:
        warnings.append(f"COMPACTOR_CEILING_PCT={raw!r} clamped to {clamped:g}")
    return clamped


def _patterns(env: Mapping[str, str], warnings: List[str]) -> Tuple[str, ...]:
    patterns: List[str] = []
    for part in (env.get("COMPACTOR_BREAKPOINT_PATTERNS") or "").split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            re.compile(part)
        except re.error as exc:
            warnings.append(f"COMPACTOR_BREAKPOINT_PATTERNS entry {part!r} is not a valid regex ({exc}); ignored")
            continue
        patterns.append(part)
    return tuple(patterns)


def _subagents(env: Mapping[str, str], warnings: List[str]) -> str:
    raw = (env.get("COMPACTOR_SUBAGENTS") or "").strip()
    if not raw:
        return SUBAGENTS_HOLD
    value = raw.lower()
    if value in (SUBAGENTS_HOLD, SUBAGENTS_ALLOW):
        return value
    warnings.append(f"COMPACTOR_SUBAGENTS={raw!r} must be 'hold' or 'allow'; using {SUBAGENTS_HOLD!r}")
    return SUBAGENTS_HOLD


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = process_env() if env is None else env
    warnings: List[str] = []
    threshold = _positive_int(env, THRESHOLD_VAR, warnings)
    ceiling_pct = _ceiling_pct(env, warnings)
    context_window = _positive_int(env, "COMPACTOR_CONTEXT_WINDOW", warnings)
    nudge_every = _positive_int(env, "COMPACTOR_NUDGE_EVERY", warnings) or DEFAULT_NUDGE_EVERY
    max_hold_min = _positive_int(env, "COMPACTOR_MAX_HOLD_MIN", warnings) or DEFAULT_MAX_HOLD_MIN
    patterns = _patterns(env, warnings)
    disabled = bool((env.get("COMPACTOR_DISABLE") or "").strip())
    subagents = _subagents(env, warnings)
    return Settings(
        threshold=threshold,
        ceiling_pct=ceiling_pct,
        context_window=context_window,
        nudge_every=nudge_every,
        max_hold_min=max_hold_min,
        breakpoint_patterns=patterns,
        disabled=disabled,
        subagents=subagents,
        warnings=tuple(warnings),
    )
