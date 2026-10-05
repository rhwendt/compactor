"""Background tasks the main agent started, so their IDs survive compaction.

Claude Code reports a background Bash command's ID in the PostToolUse response
(`backgroundTaskId`), a TaskStop's in `task_id`, and a finished task as a
`<task-notification>` in the transcript with its `<task-id>` and `<status>`."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping, Optional, Set

from .model import MAX_TASKS, BackgroundTask, State, to_iso
from .usage import BLOCK_BYTES, MAX_SCAN_BYTES, _lines_reversed

_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NOTIFICATION = re.compile(rb"<task-id>([A-Za-z0-9_-]{1,64})</task-id>.*?<status>([a-z_]+)</status>", re.S)
# Every status but these ends a task. Unknown statuses count as finished too, since a
# notification is only sent when a task stops.
_STILL_RUNNING = {b"running", b"started", b"progress"}
DESCRIPTION_CHARS = 120


def record(state: State, payload: Mapping[str, Any], now: datetime) -> bool:
    """Track a background Bash start or a TaskStop from a main-agent PostToolUse. True if changed."""
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    response = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
    if tool == "Bash":
        task_id = response.get("backgroundTaskId")
        if not isinstance(task_id, str) or not _TASK_ID.match(task_id):
            return False
        if any(t.task_id == task_id for t in state.tasks):
            return False
        description = str(tool_input.get("description") or tool_input.get("command") or "").strip()
        state.tasks.append(BackgroundTask(task_id, " ".join(description.split())[:DESCRIPTION_CHARS],
                                          to_iso(now)))
        del state.tasks[:-MAX_TASKS]
        return True
    if tool == "TaskStop":
        task_id = response.get("task_id") or tool_input.get("task_id")
        return _forget(state, {task_id} if isinstance(task_id, str) else set())
    return False


def finished_ids(transcript_path: Optional[str], ids: Set[str]) -> Set[str]:
    """Which of `ids` have a completion notification in the transcript's last MAX_SCAN_BYTES."""
    if not transcript_path or not ids:
        return set()
    wanted = {i.encode() for i in ids}
    done: Set[bytes] = set()
    try:
        for line in _lines_reversed(transcript_path, BLOCK_BYTES, MAX_SCAN_BYTES):
            if b"<task-notification>" not in line:
                continue
            for task_id, status in _NOTIFICATION.findall(line):
                if task_id in wanted and status not in _STILL_RUNNING:
                    done.add(task_id)
            if done == wanted:
                break
    except OSError:
        return set()
    return {i.decode() for i in done}


def prune(state: State, transcript_path: Optional[str]) -> bool:
    """Drop tasks the transcript shows as finished. True if changed."""
    return _forget(state, finished_ids(transcript_path, {t.task_id for t in state.tasks}))


def _forget(state: State, ids: Set[str]) -> bool:
    kept = [t for t in state.tasks if t.task_id not in ids]
    changed = len(kept) != len(state.tasks)
    state.tasks = kept
    return changed
