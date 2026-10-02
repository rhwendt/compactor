"""`compactor` CLI, the agent's interface: hold, release, note, status."""
from __future__ import annotations

import argparse
import json
import os
import select
import sys
import threading
import time
from dataclasses import asdict
from datetime import datetime
from typing import IO, Any, Dict, List, Mapping, Optional

from . import messages, subagents
from .config import Settings, load_settings
from .state import (
    Hold, Note, NudgeState, State, last_error, load, sanitize_session_id, save, state_dir,
    to_iso, utcnow,
)
from .model import ActiveSubagent
from .usage import Usage, find_transcript, read_usage, usage_from_statusline

SESSION_VAR = "CLAUDE_CODE_SESSION_ID"
ERROR_BROKEN_PIPE = 109
MAX_NOTE_CHARS = 4000
STDIN_WAIT_S = 1.0
USAGE = """usage:
  compactor hold "<reason>"            hold auto-compaction during fragile work
  compactor release [--note "<text>"]  release the hold, optionally leaving a handoff note
  compactor note "<text>"              set the handoff note (re-injected after compaction)
  compactor note --clear               clear the handoff note
  compactor status [--json | --line]   show the hold, the note, and context usage"""


class CliError(Exception):
    """A usage mistake: printed with the usage text, exit code 2."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise CliError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="compactor", add_help=False)
    sub = parser.add_subparsers(dest="command")
    hold = sub.add_parser("hold", add_help=False)
    hold.add_argument("reason", nargs="*")
    release = sub.add_parser("release", add_help=False)
    release.add_argument("--note")
    note = sub.add_parser("note", add_help=False)
    note.add_argument("text", nargs="*")
    note.add_argument("--clear", action="store_true")
    status = sub.add_parser("status", add_help=False)
    fmt = status.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--line", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None, env: Optional[Mapping[str, str]] = None,
         stdin: Optional[IO[str]] = None, out: Optional[IO[str]] = None,
         err: Optional[IO[str]] = None, now: Optional[datetime] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    now = utcnow() if now is None else now
    if not argv:
        err.write(USAGE + "\n")
        return 2
    if argv[0] in ("-h", "--help", "help"):
        out.write(USAGE + "\n")
        return 0
    try:
        args = _build_parser().parse_args(argv)
        if args.command is None:
            raise CliError("missing command")
        settings = load_settings(env)
        if args.command == "status" and args.line:
            out.write(_status_line(settings, env, stdin, now) + "\n")
            return 0
        session_id = _session_id(env)
        handler = {"hold": _hold, "release": _release, "note": _note, "status": _status}[args.command]
        out.write(handler(args, session_id, settings, env, now) + "\n")
        return 0
    except CliError as exc:
        err.write(f"compactor: {exc}\n\n{USAGE}\n")
        return 2
    except OSError as exc:
        err.write(f"compactor: could not read or write state under {state_dir(env)}: {exc}\n")
        return 1


def _session_id(env: Mapping[str, str]) -> str:
    session_id = (env.get(SESSION_VAR) or "").strip()
    if not session_id:
        raise CliError(f"{SESSION_VAR} is not set. Run compactor from Claude Code's Bash tool.")
    try:
        sanitize_session_id(session_id)
    except ValueError as exc:
        raise CliError(str(exc))
    return session_id


def _usage(session_id: str, state: State, settings: Settings, env: Mapping[str, str]) -> Optional[Usage]:
    path = find_transcript(session_id, env)
    return read_usage(path, settings, state.window) if path else None


def _check_note(text: str) -> str:
    text = text.strip()
    if not text:
        raise CliError('a note needs text, e.g. compactor note "next: wire retry into client.py:88"')
    if len(text) > MAX_NOTE_CHARS:
        raise CliError(f"the note is {len(text)} chars; the limit is {MAX_NOTE_CHARS}. "
                       "Keep only what you need after compaction.")
    return text


def _hold(args: argparse.Namespace, session_id: str, settings: Settings,
          env: Mapping[str, str], now: datetime) -> str:
    reason = " ".join(args.reason).strip()
    if not reason:
        raise CliError('hold needs a reason, e.g. compactor hold "mid-refactor of the auth module"')
    state = load(session_id, env)
    refreshed = state.hold is not None
    since = state.hold.since if state.hold is not None else to_iso(now)
    state.hold = Hold(reason=reason, since=since)
    state.nudge = NudgeState()
    save(session_id, state, env)
    return messages.hold_set(state.hold, _usage(session_id, state, settings, env), settings, refreshed)


def _release(args: argparse.Namespace, session_id: str, settings: Settings,
             env: Mapping[str, str], now: datetime) -> str:
    note_text = _check_note(args.note) if args.note is not None else None
    state = load(session_id, env)
    had_hold = state.hold is not None
    state.hold = None
    state.nudge = NudgeState()
    if note_text is not None:
        state.note = Note(text=note_text, updated_at=to_iso(now))
    save(session_id, state, env)
    usage = _usage(session_id, state, settings, env)
    return messages.released(had_hold, note_text is not None, usage, settings)


def _note(args: argparse.Namespace, session_id: str, settings: Settings,
          env: Mapping[str, str], now: datetime) -> str:
    text = " ".join(args.text)
    if args.clear and text.strip():
        raise CliError("use either note text or --clear, not both")
    if not args.clear:
        text = _check_note(text)
    state = load(session_id, env)
    if args.clear:
        state.note = None
        save(session_id, state, env)
        return messages.NOTE_CLEARED
    state.note = Note(text=text, updated_at=to_iso(now))
    save(session_id, state, env)
    return messages.note_set(len(text))


def _running_subagents(session_id: str, settings: Settings, env: Mapping[str, str],
                       now: datetime) -> Optional[List[ActiveSubagent]]:
    """Subagents the hooks are tracking, or None when inactive or unreadable (status only shows it)."""
    if not settings.active:
        return None
    try:
        return subagents.running(session_id, find_transcript(session_id, env), now, env)
    except Exception:  # informational: never let it break status
        return None


def _status(args: argparse.Namespace, session_id: str, settings: Settings,
            env: Mapping[str, str], now: datetime) -> str:
    state = load(session_id, env)
    usage = _usage(session_id, state, settings, env)
    active = _running_subagents(session_id, settings, env, now)
    if not args.json:
        return messages.status_text(state, usage, settings, now, last_error(env), active)
    data: Dict[str, Any] = {
        "session_id": session_id,
        "active": settings.active,
        "disabled": settings.disabled,
        "threshold": settings.threshold,
        "hold": asdict(state.hold) if state.hold else None,
        "note": asdict(state.note) if state.note else None,
        "usage": None if usage is None else {
            "used": usage.used,
            "window": usage.window,
            "pct": round(usage.pct, 1),
            "ceiling": settings.ceiling_tokens(usage.window),
        },
        "subagents": None if active is None else [
            {"agent_id": sub.agent_id, "foreground": sub.foreground,
             "used": sub.usage.used if sub.usage is not None else None}
            for sub in active],
        "warnings": list(settings.warnings),
        "last_error": last_error(env),
    }
    return json.dumps(data, indent=2)


def _read_with_timeout(stdin: IO[str], timeout: float) -> Optional[str]:
    """Read all of stdin, giving up (None) if it hasn't reached EOF within `timeout`."""
    try:
        fileno = stdin.fileno()
    except (AttributeError, OSError, ValueError):
        return stdin.read()  # in-memory streams (tests) never block
    try:
        ready, _, _ = select.select([fileno], [], [], timeout)
    except (OSError, ValueError):
        # select can't poll this stream (on Windows it only takes sockets).
        if os.name == "nt":
            return _read_windows_pipe(fileno, timeout)
        return _read_fd_on_thread(fileno, timeout)
    return stdin.read() if ready else None


def _read_windows_pipe(fileno: int, timeout: float) -> Optional[str]:
    """Poll a Windows pipe with PeekNamedPipe and read only what is available, so nothing ever
    blocks: a thread blocked in a read would also block closing the pipe."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    peek = kernel32.PeekNamedPipe
    peek.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    peek.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(fileno)  # type: ignore[attr-defined]
    chunks: List[bytes] = []
    deadline = time.monotonic() + timeout
    while True:
        available = wintypes.DWORD(0)
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            if ctypes.get_last_error() == ERROR_BROKEN_PIPE:  # writer closed: EOF
                break
            return os.read(fileno, 1 << 20).decode("utf-8", errors="replace")  # not a pipe: a file
        if available.value:
            chunks.append(os.read(fileno, available.value))
        elif time.monotonic() >= deadline:
            return None
        else:
            time.sleep(0.02)
    return b"".join(chunks).decode("utf-8", errors="replace")


def _read_fd_on_thread(fileno: int, timeout: float) -> Optional[str]:
    """Read the raw descriptor to EOF on a daemon thread. Raw os.read takes no lock of the text
    stream's, so a reader still blocked when we give up can't deadlock closing it or exiting."""
    chunks: List[bytes] = []
    done = threading.Event()

    def read() -> None:
        try:
            while True:
                chunk = os.read(fileno, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            done.set()
        except OSError:
            pass

    threading.Thread(target=read, daemon=True).start()
    if not done.wait(timeout):
        return None
    return b"".join(chunks).decode("utf-8", errors="replace")


def _read_json(stdin: Optional[IO[str]]) -> Dict[str, Any]:
    if stdin is None:
        return {}
    try:
        if stdin.isatty():
            return {}
        raw = _read_with_timeout(stdin, STDIN_WAIT_S)
        data = json.loads(raw) if raw and raw.strip() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _status_line(settings: Settings, env: Mapping[str, str], stdin: Optional[IO[str]],
                 now: datetime) -> str:
    """Never raises: a status line must always print something."""
    try:
        if not settings.active:
            return messages.status_line(None, None, settings, now)
        data = _read_json(stdin)
        session_id = str(data.get("session_id") or env.get(SESSION_VAR) or "").strip()
        usage = usage_from_statusline(data, settings)
        if not session_id:
            return messages.status_line(None, usage, settings, now)
        state = load(session_id, env)
        if usage is not None and settings.context_window is None and state.window != usage.window:
            state = load(session_id, env)  # re-read so a concurrent hold/release isn't overwritten
            state.window = usage.window
            try:
                save(session_id, state, env)
            except OSError:
                pass
        if usage is None:
            path = data.get("transcript_path") or find_transcript(session_id, env)
            usage = read_usage(path, settings, state.window) if path else None
        return messages.status_line(state.hold, usage, settings, now)
    except Exception:
        return "compactor ?"
