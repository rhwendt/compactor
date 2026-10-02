"""Shared fixtures for the compactor test suite."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from compactor.subagents import read_markers

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
SESSION = "sess-123"
REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def iso_minutes_ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def assistant_entry(input_tokens: int = 0, cache_read: int = 0, cache_creation: int = 0,
                    sidechain: bool = False) -> Dict[str, Any]:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "model": "claude-opus-5-5",
            "role": "assistant",
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "output_tokens": 50,
            },
        },
    }


def user_entry(text: str = "hi") -> Dict[str, Any]:
    return {"type": "user", "message": {"role": "user", "content": text}}


def tool_result_entry(text: str, sidechain: bool = False) -> Dict[str, Any]:
    return {"type": "user", "isSidechain": sidechain, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_x", "content": text}]}}


def identity_entry(model_id: str) -> Dict[str, Any]:
    return {"type": "attachment", "attachment": {"identity": {"modelId": model_id}}}


def write_transcript(path: Path, entries: Iterable[Dict[str, Any]], trailing: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
        f.write(trailing)
    return path


class TempEnvTestCase(unittest.TestCase):
    """Isolated state dir and Claude config dir per test, exposed as self.env."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="compactor-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.env: Dict[str, str] = {
            "XDG_STATE_HOME": str(self.tmp / "state"),
            "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
            "HOME": str(self.tmp),
            "CLAUDE_CODE_SESSION_ID": SESSION,
        }

    def subprocess_env(self) -> Dict[str, str]:
        """os.environ without the caller's compactor settings, overlaid with self.env."""
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("COMPACTOR_") and k != "CLAUDE_CODE_AUTO_COMPACT_WINDOW"}
        env.update(self.env)
        return env

    def transcript_path(self, session: str = SESSION) -> Path:
        return self.tmp / "claude" / "projects" / "-home-user-proj" / f"{session}.jsonl"

    def write_usage(self, used: int, session: str = SESSION, model_id: Optional[str] = None) -> Path:
        entries = [user_entry()]
        if model_id is not None:
            entries.append(identity_entry(model_id))
        entries.append(assistant_entry(input_tokens=used))
        return write_transcript(self.transcript_path(session), entries)


def compact_boundary_entry(timestamp: str, sidechain: bool = False) -> Dict[str, Any]:
    return {"type": "system", "subtype": "compact_boundary", "isSidechain": sidechain,
            "timestamp": timestamp, "compactMetadata": {"trigger": "auto", "preTokens": 70_000}}


def write_subagent(main_transcript: Path, agent_id: str, used: Optional[int] = None,
                   model_id: Optional[str] = None, meta: Optional[Dict[str, Any]] = None,
                   extra: Iterable[Dict[str, Any]] = ()) -> Path:
    """Lay out <session>/subagents/agent-<id>.jsonl (+ .meta.json) the way Claude Code does."""
    directory = main_transcript.with_suffix("") / "subagents"
    entries = [user_entry()]
    if model_id is not None:
        entries.append(identity_entry(model_id))
    if used is not None:
        entries.append(assistant_entry(input_tokens=used, sidechain=True))
    entries.extend(extra)
    path = write_transcript(directory / f"agent-{agent_id}.jsonl", entries)
    if meta is not None:
        (directory / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def real_meta(**fields: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {"agentType": "general-purpose", "description": "task",
                            "toolUseId": "toolu_1", "spawnDepth": 1, "requestShape": "foreground"}
    meta.update(fields)
    return meta


def active_ids(session_id: str, now: datetime, env: Optional[Dict[str, str]] = None) -> List[str]:
    """Agent ids with a live subagent marker, sorted."""
    return sorted(read_markers(session_id, now, env))
