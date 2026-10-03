"""Session-scoped state file: atomic writes, fail-open reads, 7-day prune, and the error log."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

# The state types live in model.py (no I/O) and are re-exported here.
from .model import (  # noqa: F401
    STATE_VERSION, CeilingOverride, Hold, Note, NudgeState, State, from_iso, to_iso,
)

PRUNE_AGE_S = 7 * 24 * 3600
ERROR_LOG_LINES = 200
NOTE_FILE_LINES = 40  # how much of a note's file comes back after compaction
NOTE_FILE_CHARS = 4000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def state_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    base = env.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return Path(base) / "claude-compactor"


def sanitize_session_id(session_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "")[:128]
    if not cleaned.strip("_"):
        raise ValueError(f"unusable session id: {session_id!r}")
    return cleaned


def state_path(session_id: str, env: Optional[Mapping[str, str]] = None) -> Path:
    return state_dir(env) / f"{sanitize_session_id(session_id)}.json"


def load(session_id: str, env: Optional[Mapping[str, str]] = None) -> State:
    """Read the session's state. Missing, corrupt or foreign files read as the default."""
    try:
        raw = json.loads(state_path(session_id, env).read_text(encoding="utf-8"))
        return State.from_dict(raw)
    except Exception:
        return State()


def save(session_id: str, state: State, env: Optional[Mapping[str, str]] = None,
         now: Optional[float] = None) -> Path:
    """Write atomically (tempfile + os.replace), then prune stale session files."""
    path = state_path(session_id, env)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    prune(path.parent, keep=path, now=now)
    return path


def prune(directory: Path, keep: Path, now: Optional[float] = None) -> None:
    now = time.time() if now is None else now
    for candidate in directory.glob("*.json"):
        if candidate == keep:
            continue
        try:
            if now - candidate.stat().st_mtime > PRUNE_AGE_S:
                candidate.unlink()
        except OSError:
            pass


def append_error(text: str, env: Optional[Mapping[str, str]] = None,
                 now: Optional[datetime] = None) -> None:
    """Append a (possibly multi-line) error, each line stamped. Never raises."""
    try:
        directory = state_dir(env)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "errors.log"
        stamp = to_iso(now or utcnow())
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.exists() else []
        lines.extend(f"{stamp} {line}" for line in (text.rstrip().splitlines() or [""]))
        path.write_text("\n".join(lines[-ERROR_LOG_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def last_error(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    try:
        lines = (state_dir(env) / "errors.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return lines[-1] if lines else None


def read_file_tail(path: str, max_lines: int = NOTE_FILE_LINES, max_chars: int = NOTE_FILE_CHARS) -> Optional[str]:
    """The last max_lines lines of a text file, at most max_chars, or None if it can't be read."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_chars * 4))  # enough bytes for max_chars of UTF-8
            data = f.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
    except OSError:
        return None
    lines = data.rstrip("\n").split("\n")[-max_lines:]
    return "\n".join(lines)[-max_chars:]

