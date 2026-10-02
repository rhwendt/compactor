"""Context usage from the session transcript or the statusline input. Fails soft: None."""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Tuple

from .config import Settings
from .model import Usage  # defined in the I/O-free model module

USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
STANDARD_WINDOW = 200_000
LARGE_WINDOW = 1_000_000
BLOCK_BYTES = 64 * 1024
MAX_SCAN_BYTES = 8 * 1024 * 1024
# Conservative (high) token estimate for content not yet reported in a reply's usage: real
# tool output runs ~3.3 chars per token, so this errs toward counting more.
CHARS_PER_TOKEN = 3


def _sum_usage(usage: Any) -> Optional[int]:
    if not isinstance(usage, dict):
        return None
    total = 0
    for key in USAGE_KEYS:
        value = usage.get(key) or 0
        if not isinstance(value, int):
            return None
        total += value
    return total


def _lines_reversed(path: Any, block: int, max_bytes: int) -> Iterator[bytes]:
    """Yield complete lines from the end of the file backwards, scanning at most max_bytes."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        scanned = 0
        carry = b""
        while pos > 0 and scanned < max_bytes:
            step = min(block, pos)
            pos -= step
            scanned += step
            f.seek(pos)
            parts = (f.read(step) + carry).split(b"\n")
            carry = parts[0]
            for line in reversed(parts[1:]):
                yield line
        if pos == 0:
            yield carry


def read_context(path: Any, block: int = BLOCK_BYTES, max_bytes: int = MAX_SCAN_BYTES,
                 include_sidechain: bool = False) -> Optional[Tuple[int, int, int]]:
    """(reported, pending, last_turn), or None when no assistant usage is found:
    - reported: tokens in context as of the last main-thread assistant message;
    - pending: an estimate of the user/tool-result content added since, which `reported`
      doesn't include yet;
    - last_turn: how much `reported` grew over the reply before it (0 after a compaction).

    A subagent's own transcript marks every entry as a sidechain, so reading one needs
    include_sidechain=True."""
    pending_chars = 0
    reported: Optional[int] = None
    try:
        for line in _lines_reversed(path, block, max_bytes):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") not in ("assistant", "user"):
                continue
            if entry.get("isSidechain") and not include_sidechain:
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            if entry["type"] == "user":
                if reported is None:
                    pending_chars += len(json.dumps(message.get("content"), ensure_ascii=False))
                continue
            total = _sum_usage(message.get("usage"))
            if total is None:
                continue
            if reported is None:
                reported = total
            elif total != reported:  # the previous reply (one reply spans several entries)
                return reported, _tokens(pending_chars), max(reported - total, 0)
    except OSError:
        return None
    if reported is None:
        return None
    return reported, _tokens(pending_chars), 0


def _tokens(chars: int) -> int:
    return -(-chars // CHARS_PER_TOKEN)


def read_last_usage(path: Any, block: int = BLOCK_BYTES, max_bytes: int = MAX_SCAN_BYTES,
                    include_sidechain: bool = False) -> Optional[int]:
    """Tokens in context as of the last main-thread assistant message, or None."""
    context = read_context(path, block, max_bytes, include_sidechain)
    return context[0] if context is not None else None


def read_model_id(path: Any, max_bytes: int = 65536) -> Optional[str]:
    """The first attachment.identity.modelId within the first max_bytes of the transcript, or None.

    Claude Code writes this model-identity attachment before the agent's first reply, so it is
    available even in headless runs where the SessionStart payload has no `model` field.
    """
    try:
        with open(path, "rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return None
    for line in data.split(b"\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "attachment":
            continue
        attachment = entry.get("attachment")
        if not isinstance(attachment, dict):
            continue
        identity = attachment.get("identity")
        if not isinstance(identity, dict):
            continue
        model_id = identity.get("modelId")
        if isinstance(model_id, str):
            return model_id
    return None


def window_for_model(model_id: Optional[str]) -> Optional[int]:
    """1M when the model id says so; otherwise None — unknown, so callers must not assume 200k."""
    if model_id and model_id.endswith("[1m]"):
        return LARGE_WINDOW
    return None


def guess_window(settings: Settings, used: int) -> int:
    """Last resort when nothing reports the window: 1M only once usage exceeds 200k, which proves
    it. Not from the threshold: a 200k model can carry a threshold set for a 1M one, and guessing
    1M there would put the safety ceiling past the real limit."""
    if used > STANDARD_WINDOW:
        return LARGE_WINDOW
    return STANDARD_WINDOW


def read_usage(transcript_path: Optional[str], settings: Settings,
               cached_window: Optional[int] = None) -> Optional[Usage]:
    """Window precedence: explicit setting, then the cache, then the transcript's model identity,
    then a heuristic that is flagged as assumed (window_known=False) since it can be wrong."""
    if not transcript_path or not isinstance(transcript_path, str):
        return None
    try:
        context = read_context(transcript_path)
    except OSError:
        return None
    if context is None:
        return None
    reported, pending, last_turn = context
    used, growth = reported + pending, max(pending, last_turn)
    if settings.context_window:
        return Usage(used, settings.context_window, True, growth)
    if cached_window:
        return Usage(used, cached_window, True, growth)
    window = window_for_model(read_model_id(transcript_path))
    if window is not None:
        return Usage(used, window, True, growth)
    return Usage(used, guess_window(settings, used), False, growth)


def claude_config_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"))


def find_transcript(session_id: str, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    pattern = str(claude_config_dir(env) / "projects" / "*" / f"{glob.escape(session_id)}.jsonl")
    matches = glob.glob(pattern)
    if not matches:
        return None
    try:
        return max(matches, key=os.path.getmtime)
    except OSError:
        return None


def usage_from_statusline(data: Any, settings: Settings) -> Optional[Usage]:
    if not isinstance(data, dict):
        return None
    info = data.get("context_window")
    if not isinstance(info, dict):
        return None
    size = info.get("context_window_size")
    used = _sum_usage(info.get("current_usage"))
    if not isinstance(size, int) or size <= 0 or used is None:
        return None
    return Usage(used, settings.context_window or size)
