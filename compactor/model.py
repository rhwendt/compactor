"""Plain data types shared by the pure modules (policy, messages) and the I/O modules. No I/O."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Type, TypeVar

_T = TypeVar("_T")

STATE_VERSION = 1
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_ISO_FORMAT)


def from_iso(text: str) -> datetime:
    return datetime.strptime(text, _ISO_FORMAT).replace(tzinfo=timezone.utc)


@dataclass
class Hold:
    reason: str
    since: str


@dataclass
class Note:
    text: str
    updated_at: str


@dataclass
class NudgeState:
    last_level: int = 0
    calls_since: int = 0
    breakpoint_suggested: bool = False


@dataclass
class CeilingOverride:
    at: str
    pct: Optional[float]
    reason: str
    growth_pct: Optional[float] = None  # set when expected next-turn growth triggered it early


def _require(ok: bool) -> None:
    if not ok:
        raise ValueError("invalid state field")


def _build(cls: Type[_T], data: Any) -> _T:
    """Construct from the known field names only, so keys added by a newer version are ignored."""
    _require(isinstance(data, dict))
    return cls(**{f.name: data[f.name] for f in fields(cls) if f.name in data})  # type: ignore[call-arg]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class State:
    hold: Optional[Hold] = None
    note: Optional[Note] = None
    nudge: NudgeState = field(default_factory=NudgeState)
    stop_blocked_this_turn: bool = False
    ceiling_override: Optional[CeilingOverride] = None
    window: Optional[int] = None  # window size from `status --line`, or 1M from a `[1m]` SessionStart model

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["version"] = STATE_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "State":
        _require(isinstance(data, dict) and data.get("version") == STATE_VERSION)
        hold = _build(Hold, data["hold"]) if data.get("hold") else None
        if hold is not None:
            _require(isinstance(hold.reason, str))
            from_iso(hold.since)
        note = _build(Note, data["note"]) if data.get("note") else None
        if note is not None:
            _require(isinstance(note.text, str))
            from_iso(note.updated_at)
        override = _build(CeilingOverride, data["ceiling_override"]) if data.get("ceiling_override") else None
        if override is not None:
            _require(isinstance(override.reason, str))
            for pct in (override.pct, override.growth_pct):
                _require(pct is None or _is_int(pct) or isinstance(pct, float))
            from_iso(override.at)
        nudge = _build(NudgeState, data.get("nudge") or {})
        _require(_is_int(nudge.last_level) and 0 <= nudge.last_level <= 3)
        _require(_is_int(nudge.calls_since) and nudge.calls_since >= 0)
        _require(isinstance(nudge.breakpoint_suggested, bool))
        window = data.get("window")
        _require(window is None or (_is_int(window) and window > 0))
        return cls(
            hold=hold,
            note=note,
            nudge=nudge,
            stop_blocked_this_turn=bool(data.get("stop_blocked_this_turn", False)),
            ceiling_override=override,
            window=window,
        )


@dataclass(frozen=True)
class Usage:
    used: int
    window: int
    window_known: bool = True
    growth: int = 0  # expected growth over the next turn: the larger of the last turn's and pending

    @property
    def pct(self) -> float:
        return 100.0 * self.used / self.window


@dataclass(frozen=True)
class ActiveSubagent:
    agent_id: str
    usage: Optional[Usage]  # None when its transcript can't be read
    foreground: bool = False  # meta.json says requestShape "foreground": the main agent is waiting on it
