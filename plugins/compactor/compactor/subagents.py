"""Which real subagents are running in a session, and how full their contexts are. Never raises.

PreCompact and SessionStart can't tell a subagent's compaction from the main agent's: both carry
the parent's session_id and transcript_path and no agent_id. SubagentStart/SubagentStop do carry
agent_id and agent_type, so those hooks leave one marker file per running subagent here. Claude
Code also fires SubagentStop (never SubagentStart) for the throwaway agent that writes each
compaction summary, with an empty agent_type; those are ignored. See the verification notes,
"Subagents, trigger point and retries".

Markers are separate files rather than a field in the state file so that parallel SubagentStart
hooks, and a `compactor hold` running at the same moment, can't overwrite each other's writes.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Collection, Dict, List, Mapping, Optional, Sequence

from .model import ActiveSubagent, Usage
from .state import sanitize_session_id, state_dir
from .usage import (
    BLOCK_BYTES, MAX_SCAN_BYTES, STANDARD_WINDOW, _lines_reversed, read_context, read_model_id,
    window_for_model,
)

# A marker whose file hasn't been touched for this long is stale: its SubagentStop was missed.
# Every tool call the subagent makes (its PostToolUse) refreshes the marker's mtime.
IDLE_MIN = 15
_AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_TMP_PREFIX = ".tmp-"


def valid_agent_id(agent_id: Any) -> bool:
    return isinstance(agent_id, str) and bool(_AGENT_ID.fullmatch(agent_id))


def _session_dir_name(name: str) -> bool:
    """Whether name is one sanitize_session_id could have produced."""
    return bool(_AGENT_ID.fullmatch(name)) and bool(name.strip("_"))


def _marker_file_name(name: str) -> bool:
    return valid_agent_id(name) or name.startswith(_TMP_PREFIX)


def _plain_dir(path: Path) -> bool:
    """A real directory, not a symlink to one: nothing outside the state dir is ever touched."""
    return not path.is_symlink() and path.is_dir()


def markers_root(env: Optional[Mapping[str, str]] = None) -> Path:
    return state_dir(env) / "subagents"


def markers_dir(session_id: str, env: Optional[Mapping[str, str]] = None) -> Path:
    return markers_root(env) / sanitize_session_id(session_id)


def _write_marker(path: Path, demoted: bool, mtime: float) -> None:
    """Atomic: tempfile in the same directory, then os.replace, so a reader never sees half a
    marker. The mtime is set explicitly because it is the marker's liveness clock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _plain_dir(path.parent):
        raise OSError(f"not a plain directory: {path.parent}")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=_TMP_PREFIX)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"demoted": demoted}, f)
        os.utime(tmp, (mtime, mtime))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_marker(path: Path) -> Optional[bool]:
    """The marker's `demoted` flag, or None when it can't be read."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("demoted"), bool):
        return None
    return data["demoted"]


def mark_started(session_id: str, agent_id: str, now: datetime,
                 env: Optional[Mapping[str, str]] = None) -> None:
    if not valid_agent_id(agent_id):
        return
    try:
        _write_marker(markers_dir(session_id, env) / agent_id, False, now.timestamp())
    except OSError:
        pass


def touch(session_id: str, agent_id: str, now: datetime, create: bool,
          env: Optional[Mapping[str, str]] = None) -> None:
    """The subagent just made a tool call: refresh its marker, or (create=True) recreate one that
    expired while it ran a single long step. A recreated marker is demoted: the main agent can't
    still be waiting on a subagent whose marker expired mid-run."""
    if not valid_agent_id(agent_id):
        return
    try:
        path = markers_dir(session_id, env) / agent_id
        if path.is_symlink() or path.parent.is_symlink():
            return
        if path.exists():
            os.utime(str(path), (now.timestamp(), now.timestamp()))
        elif create:
            _write_marker(path, True, now.timestamp())
    except OSError:
        pass


def demote_all(session_id: str, env: Optional[Mapping[str, str]] = None) -> None:
    """The main agent just acted (a tool call or a prompt), so it isn't waiting on any running
    subagent: none of them can be treated as foreground any more. Keeps each marker's mtime."""
    try:
        directory = markers_dir(session_id, env)
        if not _plain_dir(directory):
            return
        markers = [m for m in directory.iterdir() if valid_agent_id(m.name) and not m.is_symlink()]
    except (OSError, ValueError):
        return
    for marker in markers:
        _demote(marker)


def _demote(marker: Path) -> None:
    """Mark one marker background, keeping its mtime."""
    try:
        if _read_marker(marker) is False:
            _write_marker(marker, True, marker.stat().st_mtime)
    except (OSError, ValueError):
        pass


def mark_stopped(session_id: str, agent_id: str, env: Optional[Mapping[str, str]] = None) -> None:
    if not valid_agent_id(agent_id):
        return
    try:
        directory = markers_dir(session_id, env)
        if _plain_dir(directory):
            (directory / agent_id).unlink()
    except OSError:
        pass


def clear(session_id: str, env: Optional[Mapping[str, str]] = None) -> None:
    try:
        shutil.rmtree(str(markers_dir(session_id, env)))
    except OSError:
        pass


def _expired(path: Path, now: datetime) -> bool:
    try:
        return now.timestamp() - path.stat().st_mtime > IDLE_MIN * 60
    except OSError:
        return True


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _prune_other_sessions(root: Path, keep: Path, now: datetime) -> None:
    """Expire idle markers of other sessions (e.g. ones that never saw SessionEnd) and remove
    their directories once empty. Only real (non-symlink) directories named like a session id
    are entered, and only real files named like a marker or a marker tempfile are removed."""
    try:
        if not _plain_dir(root):
            return
        directories = [d for d in root.iterdir()
                       if d != keep and _session_dir_name(d.name) and _plain_dir(d)]
    except OSError:
        return
    for directory in directories:
        try:
            for marker in directory.iterdir():
                if _marker_file_name(marker.name) and not marker.is_symlink() and _expired(marker, now):
                    _unlink(marker)
            directory.rmdir()  # fails, harmlessly, while live markers or other files remain
        except OSError:
            pass


def read_markers(session_id: str, now: datetime,
                 env: Optional[Mapping[str, str]] = None) -> Dict[str, bool]:
    """Live markers as {agent_id: demoted}. Idle or unreadable markers are removed, and so are
    empty marker directories."""
    try:
        directory = markers_dir(session_id, env)
    except ValueError:
        return {}
    _prune_other_sessions(directory.parent, directory, now)
    try:
        if not _plain_dir(directory):
            return {}
        candidates = sorted(directory.iterdir())
    except OSError:
        return {}
    markers: Dict[str, bool] = {}
    for marker in candidates:
        if not _marker_file_name(marker.name) or marker.is_symlink():
            continue  # not ours: never read or removed
        if marker.name.startswith(_TMP_PREFIX):
            if _expired(marker, now):
                _unlink(marker)  # left behind by a writer that died
            continue
        demoted = _read_marker(marker)
        if demoted is None or _expired(marker, now):
            _unlink(marker)
            continue
        markers[marker.name] = demoted
    if not markers:
        try:
            directory.rmdir()
        except OSError:
            pass
    return markers


def subagents_dir(transcript_path: Any) -> Optional[Path]:
    """<session>.jsonl -> <session>/subagents, where Claude Code keeps subagent transcripts."""
    if not isinstance(transcript_path, str) or not transcript_path.endswith(".jsonl"):
        return None
    return Path(transcript_path[: -len(".jsonl")]) / "subagents"


def read_meta(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _usage(transcript: Path, meta: Optional[Dict[str, Any]]) -> Optional[Usage]:
    context = read_context(transcript, include_sidechain=True)
    if context is None:
        return None
    reported, pending, last_turn = context
    used, growth = reported + pending, max(pending, last_turn)
    model = meta.get("model") if meta else None
    window = window_for_model(read_model_id(transcript)) or window_for_model(model if isinstance(model, str) else None)
    if window is not None:
        return Usage(used, window, True, growth)
    return Usage(used, STANDARD_WINDOW, False, growth)  # smaller window = lower, safer ceiling


def measure(agent_ids: Sequence[str], transcript_path: Any,
            demoted: Collection[str] = ()) -> List[ActiveSubagent]:
    """The real subagents among agent_ids, with their usage. An agent whose meta.json says
    agentType "" is a compaction-summary agent and is dropped; a missing or corrupt meta.json
    still counts (the SubagentStart payload said it was real). A subagent is foreground only if
    its meta.json says so and the main agent hasn't acted since it started (`demoted`)."""
    directory = subagents_dir(transcript_path)
    found: List[ActiveSubagent] = []
    for agent_id in agent_ids:
        if directory is None or not valid_agent_id(agent_id):
            found.append(ActiveSubagent(str(agent_id), None))
            continue
        meta = read_meta(directory / f"agent-{agent_id}.meta.json")
        if meta is not None and meta.get("agentType") == "":
            continue
        foreground = (meta is not None and meta.get("requestShape") == "foreground"
                      and agent_id not in demoted)
        found.append(ActiveSubagent(agent_id, _usage(directory / f"agent-{agent_id}.jsonl", meta), foreground))
    return found


def _answered(transcript_path: Any, tool_use_ids: Mapping[str, str],
              block: int = BLOCK_BYTES, max_bytes: int = MAX_SCAN_BYTES) -> Dict[str, bool]:
    """{agent_id: is_error} for the agents (values: their Agent call's tool_use_id) whose call
    already has a tool_result in the main transcript. Only the last max_bytes are searched."""
    wanted = {tool_id.encode("utf-8"): agent_id for agent_id, tool_id in tool_use_ids.items()}
    found: Dict[str, bool] = {}
    if not wanted or not isinstance(transcript_path, str):
        return found
    try:
        for line in _lines_reversed(transcript_path, block, max_bytes):
            if b"tool_result" not in line or not any(tool_id in line for tool_id in wanted):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") if isinstance(entry, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            for item in content if isinstance(content, list) else ():
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    agent_id = wanted.get(str(item.get("tool_use_id")).encode("utf-8"))
                    if agent_id is not None and agent_id not in found:
                        found[agent_id] = item.get("is_error") is True
            if len(found) == len(wanted):
                break
    except OSError:
        pass
    return found


def _returned(agent_ids: Sequence[str], transcript_path: Any) -> Dict[str, bool]:
    """{agent_id: is_error} for foreground subagents whose Agent call has already been answered
    in the main transcript. An error answer ends the call (live: an API error, with no
    SubagentStop). Any other answer may mean the user moved the call to the background: Claude
    Code answers it at once (async_launched) and the subagent keeps running, while meta.json
    still says "foreground". A background call is answered at launch, so it can't tell."""
    directory = subagents_dir(transcript_path)
    if directory is None:
        return {}
    calls: Dict[str, str] = {}
    for agent_id in agent_ids:
        meta = read_meta(directory / f"agent-{agent_id}.meta.json")
        tool_id = meta.get("toolUseId") if meta else None
        if meta and meta.get("requestShape") == "foreground" and isinstance(tool_id, str) and tool_id:
            calls[agent_id] = tool_id
    return _answered(transcript_path, calls)


def running(session_id: str, transcript_path: Any, now: datetime,
            env: Optional[Mapping[str, str]] = None) -> List[ActiveSubagent]:
    """Real subagents running in this session (SubagentStart seen, no SubagentStop, not idle).
    A foreground one whose Agent call was answered with an error is gone: its marker is removed.
    One answered without an error runs on in the background: its marker is demoted, and it is
    re-demoted on every call, so a marker that touch() recreated can't turn foreground again."""
    markers = read_markers(session_id, now, env)
    if not markers:
        return []
    returned = _returned(sorted(markers), transcript_path)
    demoted = {k for k, v in markers.items() if v}
    for agent_id, is_error in returned.items():
        if is_error:
            mark_stopped(session_id, agent_id, env)
        elif agent_id not in demoted:
            _demote(markers_dir(session_id, env) / agent_id)
            demoted.add(agent_id)
    live = sorted(k for k in markers if not returned.get(k))
    return measure(live, transcript_path, demoted) if live else []


def owns_compaction(active: Sequence[ActiveSubagent]) -> bool:
    """Whether the compaction SessionStart(compact) reports was a subagent's: yes when every
    running subagent is foreground, because the main agent is then waiting on them and can't be
    compacting. Nothing on disk can say more at that moment: Claude Code writes the compacting
    agent's compact_boundary only after every SessionStart hook has returned. With a background
    subagent running the compaction may be either agent's, so it counts as the main agent's."""
    return bool(active) and all(sub.foreground for sub in active)
