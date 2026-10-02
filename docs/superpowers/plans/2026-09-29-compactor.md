# compactor v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A public Claude Code plugin that lets the agent hold auto-compaction during fragile
work and release it at safe breakpoints. It adds handoff notes that survive compaction,
usage-aware nudges, breakpoint suggestions, a safety ceiling, and status-line output.

**Architecture:** A stdlib-only Python package, `compactor/`, sits at the repo root.

- **`policy.py`:** pure decision functions.
- **`config.py`, `state.py`, `usage.py`:** thin I/O modules for env vars, the per-session
  state file, and the transcript.
- **`messages.py`:** all agent-facing text.
- **Agent interface:** `bin/compactor`, which is on the Bash tool's PATH.
- **Claude Code interface:** six hooks, launched by `hooks/run.py`. The hooks always fail
  open.

**Tech Stack:** Python 3.9+ (stdlib only), `unittest`, and GitHub Actions. It's a Claude Code
plugin (hooks, a skill, `bin/`, and a marketplace manifest).

**Spec:** `docs/superpowers/specs/2026-09-29-compactor-design.md`. Read it before starting
any task. Section numbers (§) below refer to it.

## Global Constraints

- Python **3.9+** compatibility. Every module starts with `from __future__ import annotations`.
  Don't use `match`, `X | Y` outside annotations, `dataclass(slots=...)`, or
  `kw_only`.
- **Standard library only.** No third-party packages, including in tests. Tests use
  `unittest`.
- Run the suite with `python3 -m unittest discover -s tests -t . -v`.
- **Hooks fail open.** Any exception means exit 0 with no output, and the traceback is appended
  to `errors.log`.
- **The CLI fails loudly.** It exits nonzero with a message that shows the correct invocation.
- **Only `cli.py` writes `hold` and `note`.** Hooks write only the bookkeeping fields
  (`nudge`, `stop_blocked_this_turn`, `ceiling_override`), and `status --line` writes only
  `window`. Hooks clear `hold` only on a ceiling override or at SessionEnd.
- **State path:** `${XDG_STATE_HOME:-~/.local/state}/claude-compactor/<sanitized-session-id>.json`,
  written atomically, 7-day prune.
- **Plugin switches:** the plugin is inactive when `CLAUDE_CODE_AUTO_COMPACT_WINDOW` is unset,
  and every hook is a no-op when `COMPACTOR_DISABLE` is non-empty or the payload has
  `agent_id`.
- **Defaults:** `COMPACTOR_CEILING_PCT=90` (clamped between 50 and 98),
  `COMPACTOR_NUDGE_EVERY=10`, `COMPACTOR_MAX_HOLD_MIN=60`. The note limit is 4000 chars.
- **Tests:** tests must never touch the real `~/.claude` or `~/.local/state`. Always pass an
  `env` built by `tests/helpers.TempEnvTestCase`.
- **Commits:** use Conventional Commit messages, and end every commit message with this line:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`

## Review Focus

The spec implies these five input classes or failure modes, and they're the ones most likely
to hurt a user. Each has a test in the task named.

1. **Half-written last transcript line.** A hook runs while Claude Code is still appending to
   the transcript. Usage comes from the previous complete entry, and nothing crashes. (Task 4:
   `test_ignores_half_written_last_line`)
2. **Hostile or odd session ids** (`../../etc/passwd`, `///`). The state file stays inside
   the state dir, or the id is rejected. (Task 3:
   `test_hostile_session_id_stays_inside_state_dir`, `test_empty_session_id_is_rejected`)
3. **Reasons and notes containing quotes, `$`, backticks, braces or newlines.** They're stored
   verbatim and rendered safely in hook JSON and message templates. (Task 8:
   `test_reason_with_shell_characters_is_kept_verbatim`; Task 10:
   `test_nudge_renders_reason_with_braces`)
4. **Statusline stdin that's empty, garbage, or missing fields.** `status --line` still prints
   one line, exits 0, and never shows a traceback. (Task 8:
   `test_line_survives_garbage_stdin`)
5. **A state file that's hand-edited or from a future version, and garbage hook stdin.** It
   reads as the default, and the hooks allow compaction. (Task 3:
   `test_corrupt_or_foreign_files_read_as_default`; Task 9:
   `test_garbage_stdin_fails_open_and_logs`)

---

## File map

| Path | Responsibility | Task |
|---|---|---|
| `.gitignore` | Ignores `spike/` and Python caches | 1 |
| `tests/fixtures/payloads/*.json` | Real hook payloads captured from Claude Code | 1 |
| `docs/superpowers/specs/2026-09-29-verification.md` | Payload-capture findings | 1 |
| `compactor/__init__.py` | Package marker, `__version__` | 2 |
| `compactor/config.py` | Env vars → `Settings` | 2 |
| `tests/__init__.py`, `tests/helpers.py` | Test fixtures and a temp-env base class | 2 |
| `compactor/state.py` | State file, time helpers, error log | 3 |
| `compactor/usage.py` | Transcript and statusline usage | 4 |
| `compactor/policy.py` | Pure decisions (gate in Task 5; nudges, Stop and breakpoints in Task 6) | 5, 6 |
| `compactor/messages.py` | All agent- and user-facing text | 7 |
| `compactor/cli.py`, `bin/compactor` | The agent CLI | 8 |
| `hooks/run.py`, `hooks/hooks.json` | Hook launcher and registrations | 9, 10 |
| `compactor/hooks/{__init__,_common,precompact,session_start,session_end}.py` | Core hooks | 9 |
| `compactor/hooks/{user_prompt_submit,post_tool_use,stop}.py` | Nudge hooks | 10 |
| `skills/compactor/SKILL.md`, `.claude-plugin/{plugin,marketplace}.json` | Plugin surface | 11 |
| `README.md`, `CHANGELOG.md`, `LICENSE`, `.github/workflows/ci.yml` | Distribution | 12 |

---

### Task 1: Capture real hook payloads (verification spike)

This spike resolves spec §9 item 4 (the Bash response shape and exit code) and the PreCompact
trigger field name, and produces real fixtures for later tests. The spike plugin itself is
throwaway and never committed.

**Files:**
- Create: `.gitignore`
- Create (throwaway, ignored): `spike/capture/.claude-plugin/plugin.json`, `spike/capture/hooks/hooks.json`
- Create: `tests/fixtures/payloads/pre_compact.json`, `post_tool_use_bash_ok.json`, `post_tool_use_bash_fail.json` (only if a failing Bash fires PostToolUse), `stop.json`, `user_prompt_submit.json`, `session_start.json`, `session_end.json`
- Create: `docs/superpowers/specs/2026-09-29-verification.md`

**Interfaces:**
- Produces: fixture files that Tasks 9 and 10 load by those exact names. The verification doc
  has a "Plan adjustments" section, and the implementers of Tasks 9 and 10 must read it.

- [ ] **Step 1: Create `.gitignore`**

```gitignore
spike/
__pycache__/
*.pyc
```

- [ ] **Step 2: Create the capture plugin**

`spike/capture/.claude-plugin/plugin.json`:

```json
{ "name": "compactor-capture", "version": "0.0.0" }
```

`spike/capture/hooks/hooks.json`. Each hook writes its stdin to a timestamped file:

```json
{
  "hooks": {
    "PreCompact": [{ "hooks": [{ "type": "command", "command": "cat > \"$COMPACTOR_CAPTURE_DIR/$(date +%s%N)-PreCompact.json\"" }] }],
    "PostToolUse": [{ "matcher": "Bash", "hooks": [{ "type": "command", "command": "cat > \"$COMPACTOR_CAPTURE_DIR/$(date +%s%N)-PostToolUse.json\"" }] }],
    "Stop": [{ "hooks": [{ "type": "command", "command": "cat > \"$COMPACTOR_CAPTURE_DIR/$(date +%s%N)-Stop.json\"" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "cat > \"$COMPACTOR_CAPTURE_DIR/$(date +%s%N)-UserPromptSubmit.json\"" }] }],
    "SessionStart": [{ "hooks": [{ "type": "command", "command": "cat > \"$COMPACTOR_CAPTURE_DIR/$(date +%s%N)-SessionStart.json\"" }] }],
    "SessionEnd": [{ "hooks": [{ "type": "command", "command": "cat > \"$COMPACTOR_CAPTURE_DIR/$(date +%s%N)-SessionEnd.json\"" }] }]
  }
}
```

- [ ] **Step 3: Run a headless session that runs a passing and a failing Bash command**

```bash
mkdir -p spike/out
export COMPACTOR_CAPTURE_DIR="$PWD/spike/out"
claude -p --plugin-dir spike/capture --allowedTools "Bash" \
  "Run the bash command 'true'. Then, as a separate tool call, run the bash command 'false'. Then reply with the word done."
ls spike/out
```

Expected: files for SessionStart, UserPromptSubmit, PostToolUse (one or two), Stop, and
SessionEnd.

- [ ] **Step 4: Capture a PreCompact payload**

```bash
claude -p --plugin-dir spike/capture --continue "/compact"
ls spike/out | grep PreCompact
```

If no PreCompact file appears, capture one interactively instead. Run
`COMPACTOR_CAPTURE_DIR="$PWD/spike/out" claude --plugin-dir spike/capture`, type `hi`, then
`/compact`, then `/exit`.

- [ ] **Step 5: Inspect every payload**

```bash
for f in spike/out/*.json; do echo "== $f"; python3 -m json.tool "$f" | head -60; done
```

- [ ] **Step 6: Copy sanitized fixtures**

Copy one payload per event into `tests/fixtures/payloads/` using the file names listed under
**Files**. In each one:

- Replace `session_id` with `"sess-123"`.
- Replace `transcript_path` with `"/tmp/compactor-fixture/transcript.jsonl"`.
- Replace `cwd` with `"/tmp/compactor-fixture"`.
- Replace any home-directory path with `/tmp/compactor-fixture`.

Keep every other field exactly as captured. `post_tool_use_bash_ok.json` is the `true`
command. `post_tool_use_bash_fail.json` is the `false` command, and only exists if a
PostToolUse payload was captured for it.

- [ ] **Step 7: Write the verification notes**

Create `docs/superpowers/specs/2026-09-29-verification.md` with this structure, filled in
from what you observed:

```markdown
# Payload verification (2026-09-29)

Captured with Claude Code <version from `claude --version`>.

## Findings
- PreCompact trigger field: `<compaction_trigger | trigger>` = `<value>`
- PostToolUse (Bash) response field: `<tool_response | tool_output>`; shape: `<string | object with keys ...>`
- Failing Bash (`false`) fires PostToolUse: `<yes | no>`; failure is indicated by: `<field/value or "n/a">`
- Stop payload has `stop_hook_active`: `<yes | no>`
- SessionStart `source` for a headless run: `<value>`
- `agent_id` present in main-session payloads: `<yes | no>`

## Plan adjustments
<Either "None", or the exact changes Task 9 (PreCompact trigger lookup) and Task 10
(`bash_succeeded`) must make to match the payloads above.>
```

- [ ] **Step 8: Commit**

```bash
git add .gitignore tests/fixtures/payloads docs/superpowers/specs/2026-09-29-verification.md
git commit -m "test: capture real Claude Code hook payloads as fixtures

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Package skeleton, test helpers, and `config.py`

**Files:**
- Create: `compactor/__init__.py`, `compactor/config.py`
- Create: `tests/__init__.py` (empty), `tests/helpers.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.THRESHOLD_VAR`
  - `config.Settings(threshold: Optional[int], ceiling_pct: float, context_window: Optional[int], nudge_every: int, max_hold_min: int, breakpoint_patterns: Tuple[str, ...], disabled: bool, warnings: Tuple[str, ...])`
    - `Settings.active -> bool`
    - `Settings.ceiling_tokens(window: int) -> int`
  - `config.load_settings(env: Optional[Mapping[str, str]] = None) -> Settings`
  - `tests.helpers`: `NOW`, `SESSION`, `REPO_ROOT`, `FIXTURES`, `iso_minutes_ago`,
    `assistant_entry`, `user_entry`, `write_transcript`, and `TempEnvTestCase` (with `.env`,
    `.tmp`, `.transcript_path()` and `.write_usage(used)`)

- [ ] **Step 1: Create the package marker and test helpers**

`compactor/__init__.py`:

```python
"""compactor: agent-controlled auto-compaction for Claude Code."""
__version__ = "0.1.0"
```

`tests/__init__.py`: an empty file.

`tests/helpers.py`:

```python
"""Shared fixtures for the compactor test suite."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

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

    def transcript_path(self, session: str = SESSION) -> Path:
        return self.tmp / "claude" / "projects" / "-home-user-proj" / f"{session}.jsonl"

    def write_usage(self, used: int, session: str = SESSION) -> Path:
        return write_transcript(self.transcript_path(session),
                                [user_entry(), assistant_entry(input_tokens=used)])
```

- [ ] **Step 2: Write the failing tests**

`tests/test_config.py`:

```python
from __future__ import annotations

import unittest

from compactor.config import Settings, load_settings


class LoadSettingsTest(unittest.TestCase):
    def test_defaults_with_empty_env(self):
        s = load_settings({})
        self.assertIsNone(s.threshold)
        self.assertFalse(s.active)
        self.assertEqual(s.ceiling_pct, 90.0)
        self.assertIsNone(s.context_window)
        self.assertEqual(s.nudge_every, 10)
        self.assertEqual(s.max_hold_min, 60)
        self.assertEqual(s.breakpoint_patterns, ())
        self.assertFalse(s.disabled)
        self.assertEqual(s.warnings, ())

    def test_threshold_enables_plugin(self):
        s = load_settings({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350000"})
        self.assertEqual(s.threshold, 350000)
        self.assertTrue(s.active)

    def test_threshold_with_suffix_is_rejected_with_warning(self):
        s = load_settings({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350k"})
        self.assertIsNone(s.threshold)
        self.assertFalse(s.active)
        self.assertIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", s.warnings[0])

    def test_ceiling_non_numeric_falls_back_with_warning(self):
        for raw in ("abc", "nan"):
            with self.subTest(raw=raw):
                s = load_settings({"COMPACTOR_CEILING_PCT": raw})
                self.assertEqual(s.ceiling_pct, 90.0)
                self.assertEqual(len(s.warnings), 1)

    def test_ceiling_is_clamped(self):
        self.assertEqual(load_settings({"COMPACTOR_CEILING_PCT": "200"}).ceiling_pct, 98.0)
        self.assertEqual(load_settings({"COMPACTOR_CEILING_PCT": "10"}).ceiling_pct, 50.0)
        ok = load_settings({"COMPACTOR_CEILING_PCT": "85"})
        self.assertEqual(ok.ceiling_pct, 85.0)
        self.assertEqual(ok.warnings, ())

    def test_positive_ints_reject_zero_negative_and_junk(self):
        s = load_settings({"COMPACTOR_NUDGE_EVERY": "0", "COMPACTOR_MAX_HOLD_MIN": "-5",
                           "COMPACTOR_CONTEXT_WINDOW": "x"})
        self.assertEqual((s.nudge_every, s.max_hold_min, s.context_window), (10, 60, None))
        self.assertEqual(len(s.warnings), 3)

    def test_context_window_override(self):
        s = load_settings({"COMPACTOR_CONTEXT_WINDOW": "1000000"})
        self.assertEqual(s.context_window, 1000000)

    def test_breakpoint_patterns_skip_invalid_regex(self):
        s = load_settings({"COMPACTOR_BREAKPOINT_PATTERNS": r"deploy\s+ok; ([ ;  "})
        self.assertEqual(s.breakpoint_patterns, (r"deploy\s+ok",))
        self.assertEqual(len(s.warnings), 1)

    def test_disable_turns_plugin_off(self):
        s = load_settings({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350000", "COMPACTOR_DISABLE": "1"})
        self.assertTrue(s.disabled)
        self.assertFalse(s.active)


class CeilingTokensTest(unittest.TestCase):
    def test_percent_of_window(self):
        self.assertEqual(Settings(threshold=350_000).ceiling_tokens(1_000_000), 900_000)

    def test_always_above_threshold(self):
        self.assertEqual(Settings(threshold=950_000).ceiling_tokens(1_000_000), 950_001)

    def test_without_threshold(self):
        self.assertEqual(Settings().ceiling_tokens(200_000), 180_000)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'compactor.config'`

- [ ] **Step 4: Implement `compactor/config.py`**

```python
"""Environment-variable configuration. Invalid values fall back to defaults and never raise."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple

THRESHOLD_VAR = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
DEFAULT_CEILING_PCT = 90.0
MIN_CEILING_PCT = 50.0
MAX_CEILING_PCT = 98.0
DEFAULT_NUDGE_EVERY = 10
DEFAULT_MAX_HOLD_MIN = 60


@dataclass(frozen=True)
class Settings:
    threshold: Optional[int] = None
    ceiling_pct: float = DEFAULT_CEILING_PCT
    context_window: Optional[int] = None
    nudge_every: int = DEFAULT_NUDGE_EVERY
    max_hold_min: int = DEFAULT_MAX_HOLD_MIN
    breakpoint_patterns: Tuple[str, ...] = ()
    disabled: bool = False
    warnings: Tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.threshold is not None and not self.disabled

    def ceiling_tokens(self, window: int) -> int:
        """Token count at which a hold is overridden. Always above the threshold."""
        ceiling = int(window * self.ceiling_pct / 100)
        if self.threshold is not None:
            ceiling = max(ceiling, self.threshold + 1)
        return ceiling


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


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    env = os.environ if env is None else env
    warnings: List[str] = []
    threshold = _positive_int(env, THRESHOLD_VAR, warnings)
    ceiling_pct = _ceiling_pct(env, warnings)
    context_window = _positive_int(env, "COMPACTOR_CONTEXT_WINDOW", warnings)
    nudge_every = _positive_int(env, "COMPACTOR_NUDGE_EVERY", warnings) or DEFAULT_NUDGE_EVERY
    max_hold_min = _positive_int(env, "COMPACTOR_MAX_HOLD_MIN", warnings) or DEFAULT_MAX_HOLD_MIN
    patterns = _patterns(env, warnings)
    disabled = bool((env.get("COMPACTOR_DISABLE") or "").strip())
    return Settings(
        threshold=threshold,
        ceiling_pct=ceiling_pct,
        context_window=context_window,
        nudge_every=nudge_every,
        max_hold_min=max_hold_min,
        breakpoint_patterns=patterns,
        disabled=disabled,
        warnings=tuple(warnings),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_config -v`
Expected: all tests pass (`OK`).

- [ ] **Step 6: Commit**

```bash
git add compactor/__init__.py compactor/config.py tests/__init__.py tests/helpers.py tests/test_config.py
git commit -m "feat: add settings loaded from environment variables

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `state.py`: the session state file, time helpers, and the error log

**Files:**
- Create: `compactor/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. The tests use `tests.helpers`.
- Produces:
  - Constants: `STATE_VERSION`, `PRUNE_AGE_S`, `ERROR_LOG_LINES`
  - Time helpers: `utcnow() -> datetime`, `to_iso(dt) -> str` (e.g.
    `"2026-09-29T12:00:00Z"`), `from_iso(text) -> datetime` (UTC-aware)
  - Dataclasses:
    - `Hold(reason: str, since: str)`
    - `Note(text: str, updated_at: str)`
    - `NudgeState(last_level: int = 0, calls_since: int = 0, breakpoint_suggested: bool = False)`
    - `CeilingOverride(at: str, pct: Optional[float], reason: str)`
    - `State(hold, note, nudge, stop_blocked_this_turn: bool, ceiling_override, window: Optional[int])`,
      with `.to_dict()` and `State.from_dict(d)`
  - File functions:
    - `state_dir(env=None) -> Path`
    - `sanitize_session_id(s) -> str` (raises `ValueError`)
    - `state_path(session_id, env=None) -> Path`
    - `load(session_id, env=None) -> State` (never raises on bad content)
    - `save(session_id, state, env=None, now=None) -> Path`
    - `prune(directory, keep, now=None)`
    - `append_error(text, env=None, now=None)`
    - `last_error(env=None) -> Optional[str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_state.py`:

```python
from __future__ import annotations

import json
import os
import time
import unittest

from compactor.state import (
    ERROR_LOG_LINES, CeilingOverride, Hold, Note, NudgeState, State, append_error, last_error,
    load, sanitize_session_id, save, state_dir, state_path,
)
from tests.helpers import NOW, SESSION, TempEnvTestCase, iso_minutes_ago


class StateFileTest(TempEnvTestCase):
    def test_missing_file_reads_as_default(self):
        self.assertEqual(load(SESSION, self.env), State())

    def test_round_trip(self):
        state = State(
            hold=Hold("refactor", iso_minutes_ago(5)),
            note=Note("next: x", iso_minutes_ago(1)),
            nudge=NudgeState(last_level=2, calls_since=3, breakpoint_suggested=True),
            stop_blocked_this_turn=True,
            ceiling_override=CeilingOverride(at=iso_minutes_ago(0), pct=90.4, reason="refactor"),
            window=1_000_000,
        )
        save(SESSION, state, self.env)
        self.assertEqual(load(SESSION, self.env), state)

    def test_corrupt_or_foreign_files_read_as_default(self):
        path = state_path(SESSION, self.env)
        path.parent.mkdir(parents=True)
        bad_contents = [
            "{not json",
            json.dumps({"version": 99}),
            json.dumps({"version": 1, "hold": {"reason": "x", "since": "yesterday"}}),
            json.dumps({"version": 1, "hold": {"reason": "x", "since": "2026-09-29T12:00:00Z", "extra": 1}}),
            json.dumps([1, 2]),
            json.dumps({"version": 1, "window": "big"}),
        ]
        for content in bad_contents:
            with self.subTest(content=content):
                path.write_text(content)
                self.assertEqual(load(SESSION, self.env), State())

    def test_session_ids_are_isolated(self):
        save("a", State(hold=Hold("x", iso_minutes_ago(0))), self.env)
        self.assertIsNone(load("b", self.env).hold)

    def test_hostile_session_id_stays_inside_state_dir(self):
        path = state_path("../../etc/passwd", self.env)
        self.assertEqual(path.parent, state_dir(self.env))
        self.assertNotIn("/", path.name)

    def test_empty_session_id_is_rejected(self):
        for bad in ("", "///", "..."):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    sanitize_session_id(bad)

    def test_save_leaves_no_temp_files(self):
        save(SESSION, State(), self.env)
        names = sorted(p.name for p in state_dir(self.env).iterdir())
        self.assertEqual(names, [f"{SESSION}.json"])

    def test_save_prunes_files_older_than_seven_days(self):
        directory = state_dir(self.env)
        directory.mkdir(parents=True)
        old = directory / "old.json"
        old.write_text("{}")
        stale = time.time() - 8 * 24 * 3600
        os.utime(old, (stale, stale))
        recent = directory / "recent.json"
        recent.write_text("{}")
        save(SESSION, State(), self.env)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())


class ErrorLogTest(TempEnvTestCase):
    def test_last_error_is_latest_line(self):
        self.assertIsNone(last_error(self.env))
        append_error("precompact: first\nValueError: boom", self.env, now=NOW)
        self.assertEqual(last_error(self.env), "2026-09-29T12:00:00Z ValueError: boom")

    def test_log_is_trimmed(self):
        for i in range(ERROR_LOG_LINES + 50):
            append_error(f"e{i}", self.env, now=NOW)
        lines = (state_dir(self.env) / "errors.log").read_text().splitlines()
        self.assertEqual(len(lines), ERROR_LOG_LINES)
        self.assertTrue(lines[-1].endswith(f"e{ERROR_LOG_LINES + 49}"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_state -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'compactor.state'`

- [ ] **Step 3: Implement `compactor/state.py`**

```python
"""Session-scoped state file: atomic writes, fail-open reads, 7-day prune, and the error log."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

STATE_VERSION = 1
PRUNE_AGE_S = 7 * 24 * 3600
ERROR_LOG_LINES = 200
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _require(ok: bool) -> None:
    if not ok:
        raise ValueError("invalid state field")


@dataclass
class State:
    hold: Optional[Hold] = None
    note: Optional[Note] = None
    nudge: NudgeState = field(default_factory=NudgeState)
    stop_blocked_this_turn: bool = False
    ceiling_override: Optional[CeilingOverride] = None
    window: Optional[int] = None  # context window size last reported by `status --line`

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["version"] = STATE_VERSION
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "State":
        _require(isinstance(data, dict) and data.get("version") == STATE_VERSION)
        hold = Hold(**data["hold"]) if data.get("hold") else None
        if hold is not None:
            _require(isinstance(hold.reason, str))
            from_iso(hold.since)
        note = Note(**data["note"]) if data.get("note") else None
        if note is not None:
            _require(isinstance(note.text, str))
            from_iso(note.updated_at)
        override = CeilingOverride(**data["ceiling_override"]) if data.get("ceiling_override") else None
        window = data.get("window")
        _require(window is None or (isinstance(window, int) and window > 0))
        return cls(
            hold=hold,
            note=note,
            nudge=NudgeState(**(data.get("nudge") or {})),
            stop_blocked_this_turn=bool(data.get("stop_blocked_this_turn", False)),
            ceiling_override=override,
            window=window,
        )


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_state -v`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add compactor/state.py tests/test_state.py
git commit -m "feat: add atomic session state file and error log

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `usage.py`: context usage from the transcript and the statusline

The spec (§9 results) says the transcript format is internal to Claude Code. Every function
here returns `None` rather than raising on bad input.

**Files:**
- Create: `compactor/usage.py`
- Test: `tests/test_usage.py`

**Interfaces:**
- Consumes: `config.Settings`
- Produces:
  - `USAGE_KEYS`, `STANDARD_WINDOW = 200_000`, `LARGE_WINDOW = 1_000_000`
  - `Usage(used: int, window: int)`, a frozen dataclass with `.pct -> float`
  - `read_last_usage(path, block=65536, max_bytes=8 MiB) -> Optional[int]`
  - `resolve_window(settings, used: int, cached_window: Optional[int]) -> int`
  - `read_usage(transcript_path: Optional[str], settings, cached_window: Optional[int] = None) -> Optional[Usage]`
  - `claude_config_dir(env=None) -> Path`
  - `find_transcript(session_id, env=None) -> Optional[str]`
  - `usage_from_statusline(data: Any, settings) -> Optional[Usage]`

- [ ] **Step 1: Write the failing tests**

`tests/test_usage.py`:

```python
from __future__ import annotations

import unittest

from compactor.config import Settings
from compactor.usage import (
    Usage, find_transcript, read_last_usage, read_usage, resolve_window, usage_from_statusline,
)
from tests.helpers import SESSION, TempEnvTestCase, assistant_entry, user_entry, write_transcript

W = 1_000_000


class ReadLastUsageTest(TempEnvTestCase):
    def test_sums_input_and_cache_tokens_of_last_assistant_entry(self):
        path = write_transcript(self.tmp / "t.jsonl", [
            assistant_entry(input_tokens=1), user_entry(),
            assistant_entry(input_tokens=2, cache_read=300, cache_creation=40), user_entry(),
        ])
        self.assertEqual(read_last_usage(path), 342)

    def test_ignores_sidechain_entries(self):
        path = write_transcript(self.tmp / "t.jsonl", [
            assistant_entry(input_tokens=500), assistant_entry(input_tokens=9, sidechain=True),
        ])
        self.assertEqual(read_last_usage(path), 500)

    def test_ignores_half_written_last_line(self):
        path = write_transcript(self.tmp / "t.jsonl", [assistant_entry(input_tokens=500)],
                                trailing='{"type": "assistant", "message": {"usa')
        self.assertEqual(read_last_usage(path), 500)

    def test_skips_assistant_entries_without_usage(self):
        path = write_transcript(self.tmp / "t.jsonl", [
            assistant_entry(input_tokens=500), {"type": "assistant", "message": {"content": "x"}},
        ])
        self.assertEqual(read_last_usage(path), 500)

    def test_finds_entry_across_block_boundaries(self):
        entries = [assistant_entry(input_tokens=777)] + [user_entry("x" * 50) for _ in range(40)]
        path = write_transcript(self.tmp / "t.jsonl", entries)
        self.assertEqual(read_last_usage(path, block=16), 777)

    def test_gives_up_after_max_bytes(self):
        entries = [assistant_entry(input_tokens=777)] + [user_entry("x" * 50) for _ in range(40)]
        path = write_transcript(self.tmp / "t.jsonl", entries)
        self.assertIsNone(read_last_usage(path, block=16, max_bytes=256))

    def test_no_assistant_entries(self):
        path = write_transcript(self.tmp / "t.jsonl", [user_entry()])
        self.assertIsNone(read_last_usage(path))


class ReadUsageTest(TempEnvTestCase):
    def test_missing_or_empty_path_is_none(self):
        s = Settings(threshold=350_000)
        self.assertIsNone(read_usage(None, s))
        self.assertIsNone(read_usage("", s))
        self.assertIsNone(read_usage(str(self.tmp / "nope.jsonl"), s))

    def test_returns_usage_with_resolved_window(self):
        path = self.write_usage(410_000)
        usage = read_usage(str(path), Settings(threshold=350_000))
        self.assertEqual(usage, Usage(410_000, W))
        self.assertAlmostEqual(usage.pct, 41.0)


class ResolveWindowTest(unittest.TestCase):
    def test_resolution_order(self):
        self.assertEqual(resolve_window(Settings(context_window=500_000), 10, 200_000), 500_000)
        self.assertEqual(resolve_window(Settings(), 10, 1_000_000), 1_000_000)
        self.assertEqual(resolve_window(Settings(threshold=350_000), 10, None), 1_000_000)
        self.assertEqual(resolve_window(Settings(threshold=150_000), 250_000, None), 1_000_000)
        self.assertEqual(resolve_window(Settings(threshold=150_000), 10, None), 200_000)


class FindTranscriptTest(TempEnvTestCase):
    def test_finds_by_session_id(self):
        path = self.write_usage(10)
        self.assertEqual(find_transcript(SESSION, self.env), str(path))

    def test_missing(self):
        self.assertIsNone(find_transcript("other", self.env))

    def test_glob_characters_in_session_id_are_literal(self):
        self.write_usage(10)
        self.assertIsNone(find_transcript("*", self.env))


class StatuslineUsageTest(unittest.TestCase):
    DATA = {"context_window": {"context_window_size": 1_000_000, "current_usage": {
        "input_tokens": 10, "cache_read_input_tokens": 400_000,
        "cache_creation_input_tokens": 90, "output_tokens": 5}}}

    def test_reads_current_usage_and_window(self):
        self.assertEqual(usage_from_statusline(self.DATA, Settings()), Usage(400_100, W))

    def test_override_window_wins(self):
        usage = usage_from_statusline(self.DATA, Settings(context_window=500_000))
        self.assertEqual(usage, Usage(400_100, 500_000))

    def test_malformed_input_is_none(self):
        for data in ({}, {"context_window": None},
                     {"context_window": {"context_window_size": "1M", "current_usage": {}}},
                     [], "x"):
            with self.subTest(data=data):
                self.assertIsNone(usage_from_statusline(data, Settings()))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_usage -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'compactor.usage'`

- [ ] **Step 3: Implement `compactor/usage.py`**

```python
"""Context usage from the session transcript or the statusline input. Fails soft: None."""
from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from .config import Settings

USAGE_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
STANDARD_WINDOW = 200_000
LARGE_WINDOW = 1_000_000
BLOCK_BYTES = 64 * 1024
MAX_SCAN_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Usage:
    used: int
    window: int

    @property
    def pct(self) -> float:
        return 100.0 * self.used / self.window


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


def read_last_usage(path: Any, block: int = BLOCK_BYTES, max_bytes: int = MAX_SCAN_BYTES) -> Optional[int]:
    """Tokens in context as of the last main-thread assistant message, or None."""
    for line in _lines_reversed(path, block, max_bytes):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        message = entry.get("message")
        total = _sum_usage(message.get("usage")) if isinstance(message, dict) else None
        if total is not None:
            return total
    return None


def resolve_window(settings: Settings, used: int, cached_window: Optional[int]) -> int:
    """Explicit override, then the size the statusline reported, then a heuristic."""
    if settings.context_window:
        return settings.context_window
    if cached_window:
        return cached_window
    if (settings.threshold or 0) > STANDARD_WINDOW or used > STANDARD_WINDOW:
        return LARGE_WINDOW
    return STANDARD_WINDOW


def read_usage(transcript_path: Optional[str], settings: Settings,
               cached_window: Optional[int] = None) -> Optional[Usage]:
    if not transcript_path or not isinstance(transcript_path, str):
        return None
    try:
        used = read_last_usage(transcript_path)
    except OSError:
        return None
    if used is None:
        return None
    return Usage(used, resolve_window(settings, used, cached_window))


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_usage -v`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add compactor/usage.py tests/test_usage.py
git commit -m "feat: read context usage from transcript and statusline input

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `policy.py`: the PreCompact gate and the safety ceiling

**Files:**
- Create: `compactor/policy.py`
- Test: `tests/test_policy_gate.py`

**Interfaces:**
- Consumes:
  - `config.Settings` (and `.ceiling_tokens`)
  - `state.Hold` and `state.from_iso`
  - `usage.Usage`
- Produces:
  - `ALLOW = "allow"`, `BLOCK = "block"`
  - `GateDecision(action: str, override: bool = False)`, a frozen dataclass
  - `hold_age_minutes(hold: Hold, now: datetime) -> float`
  - `decide_gate(hold: Optional[Hold], usage: Optional[Usage], settings: Settings, now: datetime) -> GateDecision`

- [ ] **Step 1: Write the failing tests**

`tests/test_policy_gate.py`:

```python
from __future__ import annotations

import unittest

from compactor.config import Settings
from compactor.policy import ALLOW, BLOCK, GateDecision, decide_gate, hold_age_minutes
from compactor.state import Hold
from compactor.usage import Usage
from tests.helpers import NOW, iso_minutes_ago

S = Settings(threshold=350_000)  # ceiling 900k on a 1M window
W = 1_000_000


class DecideGateTest(unittest.TestCase):
    def test_no_hold_allows(self):
        self.assertEqual(decide_gate(None, Usage(400_000, W), S, NOW), GateDecision(ALLOW))

    def test_hold_below_ceiling_blocks(self):
        hold = Hold("x", iso_minutes_ago(5))
        self.assertEqual(decide_gate(hold, Usage(899_999, W), S, NOW), GateDecision(BLOCK))

    def test_hold_at_ceiling_is_overridden(self):
        hold = Hold("x", iso_minutes_ago(5))
        self.assertEqual(decide_gate(hold, Usage(900_000, W), S, NOW), GateDecision(ALLOW, override=True))

    def test_unknown_usage_young_hold_blocks(self):
        hold = Hold("x", iso_minutes_ago(59))
        self.assertEqual(decide_gate(hold, None, S, NOW), GateDecision(BLOCK))

    def test_unknown_usage_old_hold_is_overridden(self):
        hold = Hold("x", iso_minutes_ago(60))
        self.assertEqual(decide_gate(hold, None, S, NOW), GateDecision(ALLOW, override=True))

    def test_custom_ceiling(self):
        settings = Settings(threshold=350_000, ceiling_pct=60)
        decision = decide_gate(Hold("x", iso_minutes_ago(1)), Usage(600_000, W), settings, NOW)
        self.assertEqual(decision, GateDecision(ALLOW, override=True))


class HoldAgeTest(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(hold_age_minutes(Hold("x", iso_minutes_ago(12.5)), NOW), 12.5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_policy_gate -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'compactor.policy'`

- [ ] **Step 3: Implement `compactor/policy.py`**

```python
"""Pure decision logic. No I/O and no clock reads: every input is a parameter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import Settings
from .state import Hold, from_iso
from .usage import Usage

ALLOW = "allow"
BLOCK = "block"


@dataclass(frozen=True)
class GateDecision:
    action: str
    override: bool = False  # True when an active hold was overridden (ceiling or max hold age)


def hold_age_minutes(hold: Hold, now: datetime) -> float:
    return (now - from_iso(hold.since)).total_seconds() / 60


def decide_gate(hold: Optional[Hold], usage: Optional[Usage], settings: Settings,
                now: datetime) -> GateDecision:
    """Spec §5.1: the first rule that applies decides."""
    if hold is None:
        return GateDecision(ALLOW)
    if usage is not None:
        if usage.used >= settings.ceiling_tokens(usage.window):
            return GateDecision(ALLOW, override=True)
        return GateDecision(BLOCK)
    if hold_age_minutes(hold, now) >= settings.max_hold_min:
        return GateDecision(ALLOW, override=True)
    return GateDecision(BLOCK)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_policy_gate -v`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add compactor/policy.py tests/test_policy_gate.py
git commit -m "feat: add pure compaction gate with safety ceiling

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `policy.py`: nudges, the Stop decision, and breakpoints

**Files:**
- Modify: `compactor/policy.py` (replace the import block and append the code below)
- Test: `tests/test_policy_nudge.py`

**Interfaces:**
- Consumes: everything Task 5 consumes, plus `state.NudgeState`
- Produces:
  - Constants: `STOP_UNKNOWN_USAGE_MIN = 30`, `LEVEL3_MARGIN = 0.05`
  - `nudge_level(hold, usage, settings) -> int` (0 to 3)
  - `NudgeDecision(emit: bool, level: int, state: NudgeState)`, a frozen dataclass. When
    usage is unknown, `level` 0 plus `emit` True means a hold-age reminder.
  - `decide_nudge(nudge: NudgeState, hold, usage, settings) -> NudgeDecision`
  - `should_block_stop(hold, usage, settings, stop_hook_active: bool, already_blocked: bool, now) -> bool`
  - `should_suggest_breakpoint(hold, usage, settings, nudge: NudgeState) -> bool`
  - `is_breakpoint(command: str, succeeded: bool, extra_patterns: Sequence[str]) -> Optional[str]`,
    which returns `"commit"`, `"tests"`, `"custom"`, or `None`
  - `invokes_compactor(command: str) -> bool`

- [ ] **Step 1: Write the failing tests**

`tests/test_policy_nudge.py`:

```python
from __future__ import annotations

import unittest

from compactor.config import Settings
from compactor.policy import (
    NudgeDecision, decide_nudge, invokes_compactor, is_breakpoint, nudge_level,
    should_block_stop, should_suggest_breakpoint,
)
from compactor.state import Hold, NudgeState
from compactor.usage import Usage
from tests.helpers import NOW, iso_minutes_ago

S = Settings(threshold=350_000, nudge_every=3)  # ceiling 900k; level 2 from 625k; level 3 from 850k
W = 1_000_000
HOLD = Hold("refactor", iso_minutes_ago(10))


class NudgeLevelTest(unittest.TestCase):
    def test_levels(self):
        cases = [
            (None, Usage(400_000, W), 0),
            (HOLD, None, 0),
            (HOLD, Usage(349_999, W), 0),
            (HOLD, Usage(350_000, W), 1),
            (HOLD, Usage(624_999, W), 1),
            (HOLD, Usage(625_000, W), 2),
            (HOLD, Usage(849_999, W), 2),
            (HOLD, Usage(850_000, W), 3),
        ]
        for hold, usage, expected in cases:
            with self.subTest(usage=usage, hold=hold):
                self.assertEqual(nudge_level(hold, usage, S), expected)


class DecideNudgeTest(unittest.TestCase):
    def test_no_hold_never_nudges(self):
        state = NudgeState(1, 2)
        self.assertEqual(decide_nudge(state, None, Usage(900_000, W), S), NudgeDecision(False, 0, state))

    def test_first_time_past_threshold_emits(self):
        self.assertEqual(decide_nudge(NudgeState(), HOLD, Usage(400_000, W), S),
                         NudgeDecision(True, 1, NudgeState(last_level=1, calls_since=0)))

    def test_same_level_repeats_every_n_calls(self):
        usage = Usage(400_000, W)
        self.assertEqual(decide_nudge(NudgeState(1, 0), HOLD, usage, S),
                         NudgeDecision(False, 1, NudgeState(1, 1)))
        self.assertEqual(decide_nudge(NudgeState(1, 1), HOLD, usage, S),
                         NudgeDecision(False, 1, NudgeState(1, 2)))
        self.assertEqual(decide_nudge(NudgeState(1, 2), HOLD, usage, S),
                         NudgeDecision(True, 1, NudgeState(1, 0)))

    def test_escalation_emits_immediately(self):
        self.assertEqual(decide_nudge(NudgeState(1, 1), HOLD, Usage(625_000, W), S),
                         NudgeDecision(True, 2, NudgeState(2, 0)))

    def test_level_three_emits_every_call(self):
        self.assertEqual(decide_nudge(NudgeState(3, 0), HOLD, Usage(860_000, W), S),
                         NudgeDecision(True, 3, NudgeState(3, 0)))

    def test_dropping_below_threshold_resets_level(self):
        self.assertEqual(decide_nudge(NudgeState(2, 1), HOLD, Usage(100_000, W), S),
                         NudgeDecision(False, 0, NudgeState(0, 1)))

    def test_unknown_usage_emits_age_reminder_every_n_calls(self):
        self.assertEqual(decide_nudge(NudgeState(0, 0), HOLD, None, S),
                         NudgeDecision(False, 0, NudgeState(0, 1)))
        self.assertEqual(decide_nudge(NudgeState(0, 2), HOLD, None, S),
                         NudgeDecision(True, 0, NudgeState(0, 0)))

    def test_breakpoint_flag_is_preserved(self):
        decision = decide_nudge(NudgeState(1, 0, True), HOLD, Usage(400_000, W), S)
        self.assertTrue(decision.state.breakpoint_suggested)


class ShouldBlockStopTest(unittest.TestCase):
    def test_cases(self):
        past, below = Usage(400_000, W), Usage(100_000, W)
        self.assertFalse(should_block_stop(None, past, S, False, False, NOW))
        self.assertTrue(should_block_stop(HOLD, past, S, False, False, NOW))
        self.assertFalse(should_block_stop(HOLD, below, S, False, False, NOW))
        self.assertFalse(should_block_stop(HOLD, past, S, True, False, NOW))
        self.assertFalse(should_block_stop(HOLD, past, S, False, True, NOW))

    def test_unknown_usage_uses_hold_age(self):
        self.assertTrue(should_block_stop(Hold("x", iso_minutes_ago(30)), None, S, False, False, NOW))
        self.assertFalse(should_block_stop(Hold("x", iso_minutes_ago(29)), None, S, False, False, NOW))


class BreakpointTest(unittest.TestCase):
    def test_commits(self):
        for cmd in ("git commit -m 'x'", "cd repo && git commit -am wip", "git -C sub commit -m x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(is_breakpoint(cmd, True, ()), "commit")

    def test_not_commits(self):
        for cmd in ("git commit --dry-run -m x", "git log --grep commit", "git status"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(is_breakpoint(cmd, True, ()))

    def test_test_runners(self):
        for cmd in ("pytest -q", "python3 -m unittest discover", "python -m pytest tests",
                    "npm test", "npm run test -- --watch=false", "pnpm test", "yarn test",
                    "go test ./...", "cargo test", "make test", "./gradlew test",
                    "npx vitest run", "bundle exec rspec"):
            with self.subTest(cmd=cmd):
                self.assertEqual(is_breakpoint(cmd, True, ()), "tests")

    def test_lookalikes_are_not_test_runs(self):
        for cmd in ("pip install pytest-cov", "cat pytest.ini", "ls tests"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(is_breakpoint(cmd, True, ()))

    def test_commit_takes_precedence(self):
        self.assertEqual(is_breakpoint("pytest && git commit -m x", True, ()), "commit")

    def test_failure_is_never_a_breakpoint(self):
        self.assertIsNone(is_breakpoint("git commit -m x", False, ()))

    def test_custom_patterns(self):
        self.assertEqual(is_breakpoint("./deploy.sh --verify", True, (r"deploy\.sh",)), "custom")


class ShouldSuggestBreakpointTest(unittest.TestCase):
    def test_cases(self):
        past, below = Usage(400_000, W), Usage(100_000, W)
        self.assertTrue(should_suggest_breakpoint(HOLD, past, S, NudgeState()))
        self.assertFalse(should_suggest_breakpoint(HOLD, past, S, NudgeState(breakpoint_suggested=True)))
        self.assertFalse(should_suggest_breakpoint(HOLD, below, S, NudgeState()))
        self.assertFalse(should_suggest_breakpoint(HOLD, None, S, NudgeState()))
        self.assertFalse(should_suggest_breakpoint(None, past, S, NudgeState()))


class InvokesCompactorTest(unittest.TestCase):
    def test_cases(self):
        self.assertTrue(invokes_compactor("compactor release"))
        self.assertTrue(invokes_compactor("cd x && compactor hold 'y'"))
        self.assertFalse(invokes_compactor("cat compactor/cli.py"))
        self.assertFalse(invokes_compactor("echo compactor-ish"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_policy_nudge -v`
Expected: ERROR with `ImportError: cannot import name 'NudgeDecision'`

- [ ] **Step 3: Replace the import block at the top of `compactor/policy.py`**

```python
"""Pure decision logic. No I/O and no clock reads: every input is a parameter."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional, Sequence

from .config import Settings
from .state import Hold, NudgeState, from_iso
from .usage import Usage
```

- [ ] **Step 4: Append the nudge, Stop and breakpoint logic to `compactor/policy.py`**

```python
STOP_UNKNOWN_USAGE_MIN = 30
LEVEL3_MARGIN = 0.05  # fraction of the window below the ceiling where nudges become urgent

_SEP = r"(?:^|[\s;&|(])"
_END = r"(?=$|[\s;&|)])"
_COMMIT = re.compile(_SEP + r"git(?:\s+-[Cc]\s+\S+)*\s+commit" + _END)
_TESTS = re.compile(
    _SEP + r"(?:"
    r"pytest|py\.test|python3?\s+-m\s+(?:pytest|unittest)"
    r"|(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test"
    r"|go\s+test|cargo\s+test|make\s+(?:test|check)|mvn\s+(?:-\S+\s+)*test"
    r"|(?:\./)?gradlew?\s+test|rspec|(?:npx\s+)?(?:jest|vitest)"
    r")" + _END
)
_COMPACTOR = re.compile(_SEP + r"compactor(?=$|\s)")


def nudge_level(hold: Optional[Hold], usage: Optional[Usage], settings: Settings) -> int:
    """Spec §5.2: 0 = silent, 1 = past threshold, 2 = halfway to ceiling, 3 = near ceiling."""
    if hold is None or usage is None or settings.threshold is None:
        return 0
    threshold = settings.threshold
    ceiling = settings.ceiling_tokens(usage.window)
    if usage.used < threshold:
        return 0
    if usage.used >= ceiling - LEVEL3_MARGIN * usage.window:
        return 3
    if usage.used >= threshold + (ceiling - threshold) / 2:
        return 2
    return 1


@dataclass(frozen=True)
class NudgeDecision:
    emit: bool
    level: int  # 0 with emit=True means a hold-age reminder (usage unknown)
    state: NudgeState


def decide_nudge(nudge: NudgeState, hold: Optional[Hold], usage: Optional[Usage],
                 settings: Settings) -> NudgeDecision:
    if hold is None:
        return NudgeDecision(False, 0, nudge)
    if usage is None:
        calls = nudge.calls_since + 1
        if calls >= settings.nudge_every:
            return NudgeDecision(True, 0, replace(nudge, calls_since=0))
        return NudgeDecision(False, 0, replace(nudge, calls_since=calls))
    level = nudge_level(hold, usage, settings)
    if level == 0:
        return NudgeDecision(False, 0, replace(nudge, last_level=0))
    if level > nudge.last_level or level == 3:
        return NudgeDecision(True, level, replace(nudge, last_level=level, calls_since=0))
    calls = nudge.calls_since + 1
    if calls >= settings.nudge_every:
        return NudgeDecision(True, level, replace(nudge, last_level=level, calls_since=0))
    return NudgeDecision(False, level, replace(nudge, last_level=level, calls_since=calls))


def should_block_stop(hold: Optional[Hold], usage: Optional[Usage], settings: Settings,
                      stop_hook_active: bool, already_blocked: bool, now: datetime) -> bool:
    """Spec §5.3: block ending a turn at most once, and only for a hold that matters."""
    if hold is None or stop_hook_active or already_blocked:
        return False
    if usage is not None:
        return settings.threshold is not None and usage.used >= settings.threshold
    return hold_age_minutes(hold, now) >= STOP_UNKNOWN_USAGE_MIN


def should_suggest_breakpoint(hold: Optional[Hold], usage: Optional[Usage], settings: Settings,
                              nudge: NudgeState) -> bool:
    return (
        hold is not None
        and usage is not None
        and settings.threshold is not None
        and usage.used >= settings.threshold
        and not nudge.breakpoint_suggested
    )


def is_breakpoint(command: str, succeeded: bool, extra_patterns: Sequence[str]) -> Optional[str]:
    """Spec §5.4: which kind of natural breakpoint a successful Bash command was, if any."""
    if not succeeded or not command:
        return None
    if _COMMIT.search(command) and "--dry-run" not in command:
        return "commit"
    if _TESTS.search(command):
        return "tests"
    for pattern in extra_patterns:
        if re.search(pattern, command):
            return "custom"
    return None


def invokes_compactor(command: str) -> bool:
    return bool(_COMPACTOR.search(command or ""))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_policy_nudge tests.test_policy_gate -v`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add compactor/policy.py tests/test_policy_nudge.py
git commit -m "feat: add nudge levels, stop decision and breakpoint detection

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `messages.py`: every string the agent sees

**Files:**
- Create: `compactor/messages.py`
- Test: `tests/test_messages.py`

**Interfaces:**
- Consumes:
  - `config.Settings` and `config.THRESHOLD_VAR`
  - `state.Hold`, `state.Note`, `state.State`, `state.CeilingOverride` and `state.from_iso`
  - `usage.Usage`
- Produces, all returning `str`:
  - Constants: `CLI_REMINDER`, `NOTE_CLEARED`
  - Formatting:
    - `fmt_tokens(n)`
    - `fmt_age(since_iso, now)`
    - `inactive_reason(settings)`
    - `context_line(usage, settings, holding=False)` (three lines, see spec §5)
  - Hook messages:
    - `hold_active(hold, now)`
    - `gate_block(hold, usage, settings, now)`
    - `nudge(level, hold, usage, settings, now)`
    - `breakpoint_suggestion(kind)`
    - `stop_block(hold, usage, settings, now)`
    - `handoff(note, after_compaction: bool)`
    - `ceiling_notice(override)`
    - `inactive_notice(settings)`
  - CLI messages:
    - `hold_set(hold, usage, settings, refreshed: bool)`
    - `released(had_hold: bool, noted: bool, usage, settings)`
    - `note_set(chars: int)`
    - `status_text(state, usage, settings, now, last_err: Optional[str])`
    - `status_line(hold, usage, settings, now)`

- [ ] **Step 1: Write the failing tests**

`tests/test_messages.py`:

```python
from __future__ import annotations

import unittest

from compactor import messages as m
from compactor.config import Settings
from compactor.state import CeilingOverride, Hold, Note, State
from compactor.usage import Usage
from tests.helpers import NOW, iso_minutes_ago

S = Settings(threshold=350_000)
W = 1_000_000
HOLD = Hold("mid-refactor of auth", iso_minutes_ago(10))


class FormatTest(unittest.TestCase):
    def test_fmt_tokens(self):
        for n, text in ((999, "999"), (60_000, "60k"), (410_000, "410k"),
                        (1_000_000, "1M"), (1_500_000, "1.5M")):
            with self.subTest(n=n):
                self.assertEqual(m.fmt_tokens(n), text)

    def test_fmt_age(self):
        for minutes, text in ((0.5, "30s"), (12, "12m"), (125, "2h 5m"), (120, "2h"), (60 * 24 * 3, "3d")):
            with self.subTest(minutes=minutes):
                self.assertEqual(m.fmt_age(iso_minutes_ago(minutes), NOW), text)


class ContextLineTest(unittest.TestCase):
    def test_past_threshold_while_holding(self):
        self.assertEqual(m.context_line(Usage(410_000, W), S, holding=True), (
            "Context: 410k of 1M tokens used (41% of the model's window).\n"
            "Auto-compact threshold: 350k (CLAUDE_CODE_AUTO_COMPACT_WINDOW) — you are 60k past it; "
            "your hold is what's stopping compaction.\n"
            "Safety ceiling: 900k (90% of window) — your hold is overridden there, 490k from now."))

    def test_below_threshold_without_hold(self):
        self.assertEqual(m.context_line(Usage(100_000, W), S), (
            "Context: 100k of 1M tokens used (10% of the model's window).\n"
            "Auto-compact threshold: 350k (CLAUDE_CODE_AUTO_COMPACT_WINDOW) — 250k away.\n"
            "Safety ceiling: 900k (90% of window) — a hold is overridden there, 800k from now."))

    def test_ceiling_reached(self):
        self.assertEqual(m.context_line(Usage(900_000, W), S, holding=True).splitlines()[2],
                         "Safety ceiling: 900k (90% of window) — reached; a hold is overridden "
                         "at the next compaction check.")

    def test_threshold_not_set(self):
        self.assertEqual(m.context_line(Usage(100_000, 200_000), Settings()).splitlines()[1],
                         "Auto-compact threshold: not set (CLAUDE_CODE_AUTO_COMPACT_WINDOW).")

    def test_unknown(self):
        self.assertEqual(m.context_line(None, S), (
            "Context: usage unknown (the transcript couldn't be read).\n"
            "Auto-compact threshold: 350k (CLAUDE_CODE_AUTO_COMPACT_WINDOW).\n"
            "Safety ceiling: with usage unknown, a hold is overridden after 60 minutes "
            "(COMPACTOR_MAX_HOLD_MIN)."))


class AgentMessagesTest(unittest.TestCase):
    def test_gate_block(self):
        text = m.gate_block(HOLD, Usage(410_000, W), S, NOW)
        for part in ("mid-refactor of auth", "10m", "410k of 1M tokens used", "your hold is what's stopping", "compactor release"):
            self.assertIn(part, text)

    def test_nudges(self):
        for level in (0, 1, 2):
            with self.subTest(level=level):
                text = m.nudge(level, HOLD, Usage(410_000, W), S, NOW)
                self.assertIn("mid-refactor of auth", text)
                self.assertIn("410k of 1M tokens used", text)
        urgent = m.nudge(3, HOLD, Usage(860_000, W), S, NOW)
        self.assertIn("90%", urgent)
        self.assertIn("release now", urgent)

    def test_stop_block_offers_both_ways_out(self):
        text = m.stop_block(HOLD, Usage(410_000, W), S, NOW)
        self.assertIn("compactor release", text)
        self.assertIn("compactor hold", text)

    def test_handoff(self):
        note = Note("next: fix client.py:88", iso_minutes_ago(3))
        self.assertIn("before compaction", m.handoff(note, after_compaction=True))
        self.assertIn("next: fix client.py:88", m.handoff(note, after_compaction=True))
        self.assertIn("earlier in this session", m.handoff(note, after_compaction=False))

    def test_ceiling_notice(self):
        self.assertIn("90%", m.ceiling_notice(CeilingOverride(iso_minutes_ago(0), 90.4, "x")))
        self.assertIn("COMPACTOR_MAX_HOLD_MIN", m.ceiling_notice(CeilingOverride(iso_minutes_ago(0), None, "x")))

    def test_breakpoint_suggestion(self):
        self.assertIn("commit", m.breakpoint_suggestion("commit"))
        self.assertIn("compactor release", m.breakpoint_suggestion("tests"))


class CliMessagesTest(unittest.TestCase):
    def test_hold_set_while_inactive_explains_why(self):
        text = m.hold_set(HOLD, None, Settings(), refreshed=False)
        self.assertIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW is not set", text)

    def test_status_line(self):
        self.assertEqual(m.status_line(HOLD, Usage(410_000, W), Settings(), NOW), "compactor off")
        self.assertEqual(m.status_line(HOLD, Usage(410_000, W), S, NOW), "⏸ held 10m · 41%")
        self.assertEqual(m.status_line(None, Usage(410_000, W), S, NOW), "▶ 41%")
        self.assertEqual(m.status_line(None, None, S, NOW), "▶ compactor")
        self.assertEqual(m.status_line(HOLD, None, S, NOW), "⏸ held 10m")

    def test_status_text(self):
        settings = Settings(threshold=350_000, warnings=("COMPACTOR_CEILING_PCT='x' is not a number; using 90",))
        text = m.status_text(State(), None, settings, NOW, "2026-09-29T12:00:00Z ValueError: boom")
        for part in ("compactor: active (threshold 350k)", "hold: none", "note: none",
                     "Context: usage unknown", "config warning:", "last hook error:"):
            self.assertIn(part, text)
        held = m.status_text(State(hold=HOLD), None, S, NOW, None)
        self.assertIn("hold: mid-refactor of auth (10m)", held)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_messages -v`
Expected: ERROR with `ImportError: cannot import name 'messages'`

- [ ] **Step 3: Implement `compactor/messages.py`**

```python
"""Every string the agent or user sees, in one place so the tone stays consistent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .config import THRESHOLD_VAR, Settings
from .state import CeilingOverride, Hold, Note, State, from_iso
from .usage import Usage

CLI_REMINDER = (
    "compactor: you control auto-compaction in this session. Before fragile multi-step work run "
    '`compactor hold "<why>"`; at a safe breakpoint run `compactor release --note "<what matters>"`. '
    'Also: `compactor note "<text>"`, `compactor status`.'
)
NOTE_CLEARED = "Handoff note cleared."


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1_000:
        return f"{round(n / 1_000)}k"
    return str(n)


def fmt_age(since: str, now: datetime) -> str:
    seconds = max(0, int((now - from_iso(since)).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{hours // 24}d"


def inactive_reason(settings: Settings) -> str:
    if settings.disabled:
        return "COMPACTOR_DISABLE is set"
    return f"{THRESHOLD_VAR} is not set"


def context_line(usage: Optional[Usage], settings: Settings, holding: bool = False) -> str:
    """Three lines that name every number, so the agent never guesses what a figure measures."""
    whose = "your hold" if holding else "a hold"
    lines = []
    if usage is None:
        lines.append("Context: usage unknown (the transcript couldn't be read).")
    else:
        lines.append(f"Context: {fmt_tokens(usage.used)} of {fmt_tokens(usage.window)} tokens used "
                     f"({usage.pct:.0f}% of the model's window).")
    if settings.threshold is None:
        lines.append(f"Auto-compact threshold: not set ({THRESHOLD_VAR}).")
    else:
        head = f"Auto-compact threshold: {fmt_tokens(settings.threshold)} ({THRESHOLD_VAR})"
        if usage is None:
            lines.append(head + ".")
        elif usage.used >= settings.threshold:
            tail = "; your hold is what's stopping compaction." if holding else "."
            lines.append(f"{head} — you are {fmt_tokens(usage.used - settings.threshold)} past it{tail}")
        else:
            lines.append(f"{head} — {fmt_tokens(settings.threshold - usage.used)} away.")
    if usage is None:
        lines.append(f"Safety ceiling: with usage unknown, a hold is overridden after "
                     f"{settings.max_hold_min} minutes (COMPACTOR_MAX_HOLD_MIN).")
    else:
        ceiling = settings.ceiling_tokens(usage.window)
        head = f"Safety ceiling: {fmt_tokens(ceiling)} ({settings.ceiling_pct:g}% of window)"
        if usage.used >= ceiling:
            lines.append(f"{head} — reached; a hold is overridden at the next compaction check.")
        else:
            lines.append(f"{head} — {whose} is overridden there, {fmt_tokens(ceiling - usage.used)} from now.")
    return "\n".join(lines)


def hold_active(hold: Hold, now: datetime) -> str:
    return f"A compaction hold is active: {hold.reason} (held {fmt_age(hold.since, now)})."


def gate_block(hold: Hold, usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    return (
        f"compactor: auto-compaction blocked — held {fmt_age(hold.since, now)} for: {hold.reason}. "
        "Run `compactor release` (add --note to leave a handoff) when the fragile work reaches a "
        f"safe point.\n{context_line(usage, settings, holding=True)}"
    )


_NUDGES = {
    0: "Compaction has been held {age} for: {reason}, and context usage can't be read. "
       "Release at your next natural breakpoint.",
    1: "Compaction is being held ({age}) for: {reason}. Release at your next natural breakpoint.",
    2: "You are well past the compaction threshold, holding ({age}) for: {reason}. "
       "Finish the current step, write a note, and release.",
    3: "The safety ceiling will override your hold at {ceiling}% context. Write a note and release now: "
       '`compactor release --note "<what matters>"`.',
}


def nudge(level: int, hold: Hold, usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    body = _NUDGES[level].format(age=fmt_age(hold.since, now), reason=hold.reason,
                                 ceiling=f"{settings.ceiling_pct:g}")
    return f"compactor: {body}\n{context_line(usage, settings, holding=True)}"


_BREAKPOINTS = {
    "commit": "A commit just landed",
    "tests": "Tests just passed",
    "custom": "A configured breakpoint command just succeeded",
}


def breakpoint_suggestion(kind: str) -> str:
    return (
        f"compactor: {_BREAKPOINTS[kind]} — that looks like a natural breakpoint. "
        'Consider `compactor release --note "<what matters>"`.'
    )


def stop_block(hold: Hold, usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    return (
        f"compactor: you're ending your turn with compaction held ({fmt_age(hold.since, now)}) "
        f"for: {hold.reason}. Either run `compactor release` (optionally with --note), or re-run "
        '`compactor hold "<current reason>"` if the work is still fragile.\n'
        f"{context_line(usage, settings, holding=True)}"
    )


def handoff(note: Note, after_compaction: bool) -> str:
    header = ("Handoff note you left before compaction" if after_compaction
              else "Handoff note from earlier in this session")
    return (
        f"compactor — {header} (written {note.updated_at}):\n{note.text}\n"
        '(Replace it with `compactor note "<text>"` or clear it with `compactor note --clear`.)'
    )


def ceiling_notice(override: CeilingOverride) -> str:
    if override.pct is not None:
        why = f"at {override.pct:.0f}% context by the safety ceiling"
    else:
        why = "because it outlived COMPACTOR_MAX_HOLD_MIN while context usage was unreadable"
    return (
        f"compactor: your hold ({override.reason}) was overridden {why}, and the conversation "
        'was compacted. Re-hold with `compactor hold "<why>"` if the work is still fragile.'
    )


def inactive_notice(settings: Settings) -> str:
    return (
        f"compactor plugin is installed but inactive ({inactive_reason(settings)}). "
        f"Set {THRESHOLD_VAR} (e.g. 350000) to enable agent-controlled compaction."
    )


def hold_set(hold: Hold, usage: Optional[Usage], settings: Settings, refreshed: bool) -> str:
    verb = "Hold updated" if refreshed else "Hold set"
    text = (
        f"{verb}: auto-compaction stays blocked until you run `compactor release`. "
        f"Reason: {hold.reason}.\n{context_line(usage, settings, holding=True)}"
    )
    if not settings.active:
        text += f"\nNote: {inactive_reason(settings)}, so the gate is inactive and this hold has no effect."
    return text


def released(had_hold: bool, noted: bool, usage: Optional[Usage], settings: Settings) -> str:
    if had_hold:
        text = "Released. Auto-compaction will proceed at the next threshold check."
    else:
        text = "No hold was set; nothing to release."
    if noted:
        text += " Handoff note saved; it will be re-injected after compaction."
    return f"{text}\n{context_line(usage, settings)}"


def note_set(chars: int) -> str:
    return (f"Handoff note saved ({chars} chars). It is re-injected after every compaction "
            "until you replace or clear it.")


def status_text(state: State, usage: Optional[Usage], settings: Settings, now: datetime,
                last_err: Optional[str]) -> str:
    if settings.active:
        mode = f"active (threshold {fmt_tokens(settings.threshold or 0)})"
    elif settings.disabled:
        mode = f"disabled ({inactive_reason(settings)})"
    else:
        mode = f"inactive ({inactive_reason(settings)})"
    lines = [f"compactor: {mode}"]
    if state.hold is not None:
        lines.append(f"hold: {state.hold.reason} ({fmt_age(state.hold.since, now)})")
    else:
        lines.append("hold: none")
    if state.note is not None:
        preview = state.note.text.replace("\n", " ")
        lines.append(f"note: {preview[:77] + '...' if len(preview) > 80 else preview}")
    else:
        lines.append("note: none")
    lines.append(context_line(usage, settings, holding=state.hold is not None))
    lines.extend(f"config warning: {warning}" for warning in settings.warnings)
    if last_err:
        lines.append(f"last hook error: {last_err}")
    return "\n".join(lines)


def status_line(hold: Optional[Hold], usage: Optional[Usage], settings: Settings, now: datetime) -> str:
    if not settings.active:
        return "compactor off"
    if hold is not None:
        text = f"⏸ held {fmt_age(hold.since, now)}"
        return f"{text} · {usage.pct:.0f}%" if usage is not None else text
    return f"▶ {usage.pct:.0f}%" if usage is not None else "▶ compactor"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_messages -v`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add compactor/messages.py tests/test_messages.py
git commit -m "feat: add agent-facing messages with context usage line

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: The `compactor` CLI and `bin/compactor`

**Files:**
- Create: `compactor/cli.py`, `bin/compactor` (executable)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes:
  - `config.load_settings`
  - `state`: `Hold`, `Note`, `NudgeState`, `State`, `load`, `save`, `last_error`,
    `sanitize_session_id`, `state_dir`, `to_iso`, `utcnow`
  - `usage`: `find_transcript`, `read_usage`, `usage_from_statusline`
  - `messages.*`
- Produces:
  - `cli.main(argv=None, env=None, stdin=None, out=None, err=None, now=None) -> int`
    - exit codes: 0 means OK, 2 means a usage error, 1 means a state I/O error
  - `cli.USAGE`, `cli.SESSION_VAR`, `cli.MAX_NOTE_CHARS`, `cli.CliError`

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
from __future__ import annotations

import io
import json
import os
import subprocess
import sys

from compactor.cli import MAX_NOTE_CHARS, main
from compactor.state import Hold, Note, NudgeState, State, load, save
from tests.helpers import NOW, REPO_ROOT, SESSION, TempEnvTestCase, iso_minutes_ago


class CliTestCase(TempEnvTestCase):
    def setUp(self):
        super().setUp()
        self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "350000"

    def run_cli(self, *argv, stdin=None, env=None):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), env=self.env if env is None else env,
                    stdin=stdin if stdin is not None else io.StringIO(""), out=out, err=err, now=NOW)
        return code, out.getvalue(), err.getvalue()


class HoldTest(CliTestCase):
    def test_hold_sets_reason_and_since(self):
        code, out, err = self.run_cli("hold", "mid-refactor of auth")
        self.assertEqual(code, 0, err)
        hold = load(SESSION, self.env).hold
        self.assertEqual(hold, Hold("mid-refactor of auth", "2026-09-29T12:00:00Z"))
        self.assertIn("Hold set", out)

    def test_unquoted_words_are_joined(self):
        self.run_cli("hold", "fixing", "the", "parser")
        self.assertEqual(load(SESSION, self.env).hold.reason, "fixing the parser")

    def test_reason_with_shell_characters_is_kept_verbatim(self):
        reason = 'fix "quoted" $HOME & `ticks` {braces}\nline two'
        code, out, _ = self.run_cli("hold", reason)
        self.assertEqual(code, 0)
        self.assertEqual(load(SESSION, self.env).hold.reason, reason)
        self.assertIn("{braces}", out)

    def test_rehold_keeps_since_and_resets_nudges(self):
        save(SESSION, State(hold=Hold("old", iso_minutes_ago(30)), nudge=NudgeState(2, 5, True)), self.env)
        code, out, _ = self.run_cli("hold", "new reason")
        state = load(SESSION, self.env)
        self.assertEqual(state.hold, Hold("new reason", iso_minutes_ago(30)))
        self.assertEqual(state.nudge, NudgeState())
        self.assertIn("Hold updated", out)

    def test_empty_reason_fails_with_usage(self):
        for argv in (["hold"], ["hold", "   "]):
            with self.subTest(argv=argv):
                code, _, err = self.run_cli(*argv)
                self.assertEqual(code, 2)
                self.assertIn("needs a reason", err)
                self.assertIn("compactor release", err)
                self.assertIsNone(load(SESSION, self.env).hold)

    def test_hold_while_inactive_warns(self):
        env = dict(self.env)
        del env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
        code, out, _ = self.run_cli("hold", "x", env=env)
        self.assertEqual(code, 0)
        self.assertIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW is not set", out)

    def test_missing_session_id_fails(self):
        env = dict(self.env)
        del env["CLAUDE_CODE_SESSION_ID"]
        code, _, err = self.run_cli("hold", "x", env=env)
        self.assertEqual(code, 2)
        self.assertIn("CLAUDE_CODE_SESSION_ID", err)


class ReleaseTest(CliTestCase):
    def setUp(self):
        super().setUp()
        save(SESSION, State(hold=Hold("x", iso_minutes_ago(5)), nudge=NudgeState(2, 1)), self.env)

    def test_release_clears_hold_and_nudges(self):
        code, out, _ = self.run_cli("release")
        self.assertEqual(code, 0)
        state = load(SESSION, self.env)
        self.assertIsNone(state.hold)
        self.assertEqual(state.nudge, NudgeState())
        self.assertIn("Released", out)

    def test_release_with_note(self):
        code, out, _ = self.run_cli("release", "--note", "next: step 3")
        self.assertEqual(code, 0)
        self.assertEqual(load(SESSION, self.env).note, Note("next: step 3", "2026-09-29T12:00:00Z"))
        self.assertIn("Handoff note saved", out)

    def test_release_without_hold_is_fine(self):
        self.run_cli("release")
        code, out, _ = self.run_cli("release")
        self.assertEqual(code, 0)
        self.assertIn("No hold was set", out)

    def test_bad_note_leaves_hold_in_place(self):
        for note in ("", "x" * (MAX_NOTE_CHARS + 1)):
            with self.subTest(length=len(note)):
                code, _, _ = self.run_cli("release", "--note", note)
                self.assertEqual(code, 2)
                self.assertIsNotNone(load(SESSION, self.env).hold)


class NoteTest(CliTestCase):
    def test_set_and_clear(self):
        code, out, _ = self.run_cli("note", "next:", "wire", "retry")
        self.assertEqual(code, 0)
        self.assertEqual(load(SESSION, self.env).note.text, "next: wire retry")
        self.assertIn("saved", out)
        code, out, _ = self.run_cli("note", "--clear")
        self.assertEqual(code, 0)
        self.assertIsNone(load(SESSION, self.env).note)

    def test_errors(self):
        for argv in (["note"], ["note", "x", "--clear"], ["note", "x" * (MAX_NOTE_CHARS + 1)]):
            with self.subTest(argv=argv[:2]):
                code, _, _ = self.run_cli(*argv)
                self.assertEqual(code, 2)


class StatusTest(CliTestCase):
    def setUp(self):
        super().setUp()
        save(SESSION, State(hold=Hold("refactor", iso_minutes_ago(10)), note=Note("n", iso_minutes_ago(1))), self.env)
        self.write_usage(410_000)

    def test_text(self):
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("hold: refactor (10m)", out)
        self.assertIn("Context: 410k of 1M tokens used (41%", out)

    def test_json(self):
        code, out, _ = self.run_cli("status", "--json")
        data = json.loads(out)
        self.assertEqual(data["hold"]["reason"], "refactor")
        self.assertEqual(data["usage"]["pct"], 41.0)
        self.assertTrue(data["active"])

    def test_line_uses_statusline_stdin_and_caches_window(self):
        env = dict(self.env)
        del env["CLAUDE_CODE_SESSION_ID"]
        payload = {"session_id": SESSION, "context_window": {"context_window_size": 1_000_000,
                   "current_usage": {"input_tokens": 500_000}}}
        code, out, _ = self.run_cli("status", "--line", stdin=io.StringIO(json.dumps(payload)), env=env)
        self.assertEqual(code, 0)
        self.assertEqual(out, "⏸ held 10m · 50%\n")
        self.assertEqual(load(SESSION, self.env).window, 1_000_000)

    def test_line_survives_garbage_stdin(self):
        for raw in ("", "not json", "[1,2]", '{"session_id": "///"}'):
            with self.subTest(raw=raw):
                code, out, err = self.run_cli("status", "--line", stdin=io.StringIO(raw))
                self.assertEqual(code, 0)
                self.assertEqual(len(out.splitlines()), 1)
                self.assertEqual(err, "")

    def test_line_when_inactive(self):
        env = dict(self.env)
        del env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
        self.assertEqual(self.run_cli("status", "--line", env=env)[1], "compactor off\n")

    def test_status_shows_config_warnings(self):
        env = dict(self.env, COMPACTOR_CEILING_PCT="lots")
        self.assertIn("config warning:", self.run_cli("status", env=env)[1])


class ErrorsTest(CliTestCase):
    def test_unknown_command(self):
        code, _, err = self.run_cli("compact-now")
        self.assertEqual(code, 2)
        self.assertIn("usage:", err)

    def test_no_args_and_help(self):
        self.assertEqual(self.run_cli()[0], 2)
        code, out, _ = self.run_cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("compactor hold", out)

    def test_unwritable_state_dir(self):
        blocker = self.tmp / "file"
        blocker.write_text("x")
        env = dict(self.env, XDG_STATE_HOME=str(blocker))
        code, _, err = self.run_cli("hold", "x", env=env)
        self.assertEqual(code, 1)
        self.assertIn("could not read or write state", err)


class BinScriptTest(CliTestCase):
    def test_bin_script_runs(self):
        script = REPO_ROOT / "bin" / "compactor"
        self.assertTrue(os.access(script, os.X_OK), "bin/compactor must be executable")
        env = dict(os.environ, **self.env)
        result = subprocess.run([sys.executable, str(script), "hold", "via bin"], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(load(SESSION, self.env).hold.reason, "via bin")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_cli -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'compactor.cli'`

- [ ] **Step 3: Implement `compactor/cli.py`**

```python
"""`compactor` CLI, the agent's interface: hold, release, note, status."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from typing import IO, Any, Dict, List, Mapping, Optional

from . import messages
from .config import Settings, load_settings
from .state import (
    Hold, Note, NudgeState, State, last_error, load, sanitize_session_id, save, state_dir,
    to_iso, utcnow,
)
from .usage import Usage, find_transcript, read_usage, usage_from_statusline

SESSION_VAR = "CLAUDE_CODE_SESSION_ID"
MAX_NOTE_CHARS = 4000
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


def _status(args: argparse.Namespace, session_id: str, settings: Settings,
            env: Mapping[str, str], now: datetime) -> str:
    state = load(session_id, env)
    usage = _usage(session_id, state, settings, env)
    if not args.json:
        return messages.status_text(state, usage, settings, now, last_error(env))
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
        "warnings": list(settings.warnings),
        "last_error": last_error(env),
    }
    return json.dumps(data, indent=2)


def _read_json(stdin: Optional[IO[str]]) -> Dict[str, Any]:
    if stdin is None:
        return {}
    try:
        if stdin.isatty():
            return {}
        raw = stdin.read()
        data = json.loads(raw) if raw.strip() else {}
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
```

- [ ] **Step 4: Create `bin/compactor` and make it executable**

```python
#!/usr/bin/env python3
"""Agent CLI for the compactor plugin. The plugin's bin/ is on the Bash tool's PATH."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from compactor.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
```

Run: `chmod +x bin/compactor`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_cli -v`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add compactor/cli.py bin/compactor tests/test_cli.py
git update-index --chmod=+x bin/compactor
git commit -m "feat: add compactor CLI for hold, release, note and status

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Core hooks: dispatch, PreCompact, SessionStart, and SessionEnd

After this task the plugin works end to end: the gate, handoff notes, and hold cleanup.

**Before starting,** read `docs/superpowers/specs/2026-09-29-verification.md` (from Task 1).
If its "Plan adjustments" section changes the PreCompact trigger field, update the `trigger =`
line in `precompact.py` to match.

**Files:**
- Create: `hooks/run.py`, `hooks/hooks.json`
- Create: `compactor/hooks/__init__.py`, `compactor/hooks/_common.py`,
  `compactor/hooks/precompact.py`, `compactor/hooks/session_start.py`,
  `compactor/hooks/session_end.py`
- Test: `tests/hook_helpers.py`, `tests/test_hooks_core.py`

**Interfaces:**
- Consumes: `config.load_settings`, `state.*`, `usage.read_usage`, `policy.decide_gate`,
  `policy.BLOCK`, and `messages.*`
- Produces:
  - `compactor.hooks.dispatch(event: str, stdin=None, out=None, err=None, env=None, now=None) -> int`
  - `compactor.hooks.HANDLERS: Dict[str, Callable[[HookContext], HookResult]]`
  - `_common.HookContext(payload, settings, env, now)`, with `.session_id`,
    `.load_state()`, `.save_state(state)` and `.usage(state)`
  - `_common.HookResult(exit_code=0, stdout=None, stderr=None)`
  - `_common.with_context(event_name, text) -> HookResult`
  - `tests.hook_helpers.HookTestCase`, with `.run_hook(event, payload=None, env=None, raw=None)`,
    `.context_of(out)` and `.hold(minutes_ago=10, reason="refactor")`

- [ ] **Step 1: Write the hook test helper**

`tests/hook_helpers.py`:

```python
from __future__ import annotations

import io
import json
from typing import Any, Dict, Optional

from compactor.hooks import dispatch
from compactor.state import Hold, State, load, save
from tests.helpers import FIXTURES, NOW, SESSION, TempEnvTestCase, iso_minutes_ago


def fixture_payload(name: str) -> Dict[str, Any]:
    return json.loads((FIXTURES / "payloads" / name).read_text())


class HookTestCase(TempEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "350000"

    def run_hook(self, event: str, payload: Optional[Dict[str, Any]] = None,
                 env: Optional[Dict[str, str]] = None, raw: Optional[str] = None):
        if raw is None:
            body: Dict[str, Any] = {"session_id": SESSION, "transcript_path": str(self.transcript_path())}
            body.update(payload or {})
            raw = json.dumps(body)
        out, err = io.StringIO(), io.StringIO()
        code = dispatch(event, stdin=io.StringIO(raw), out=out, err=err,
                        env=self.env if env is None else env, now=NOW)
        return code, out.getvalue(), err.getvalue()

    @staticmethod
    def context_of(out: str) -> Optional[str]:
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else None

    def hold(self, minutes_ago: float = 10, reason: str = "refactor", **fields: Any) -> None:
        save(SESSION, State(hold=Hold(reason, iso_minutes_ago(minutes_ago)), **fields), self.env)

    def state(self) -> State:
        return load(SESSION, self.env)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_hooks_core.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from compactor.hooks import HANDLERS
from compactor.state import CeilingOverride, Note, State, last_error, save, state_path
from tests.helpers import REPO_ROOT, SESSION, iso_minutes_ago
from tests.hook_helpers import HookTestCase, fixture_payload


class PreCompactTest(HookTestCase):
    def test_no_hold_allows(self):
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("precompact"), (0, "", ""))

    def test_hold_blocks_with_reason_and_usage(self):
        self.hold()
        self.write_usage(410_000)
        code, out, err = self.run_hook("precompact")
        self.assertEqual(code, 2)
        self.assertIn("refactor", err)
        self.assertIn("410k of 1M tokens used", err)

    def test_ceiling_overrides_hold(self):
        self.hold()
        self.write_usage(900_000)
        self.assertEqual(self.run_hook("precompact")[0], 0)
        state = self.state()
        self.assertIsNone(state.hold)
        self.assertEqual(state.ceiling_override.pct, 90.0)
        self.assertEqual(state.ceiling_override.reason, "refactor")

    def test_old_hold_with_unknown_usage_is_overridden(self):
        self.hold(minutes_ago=61)
        self.assertEqual(self.run_hook("precompact")[0], 0)
        self.assertIsNone(self.state().ceiling_override.pct)

    def test_manual_compaction_is_never_gated(self):
        self.hold()
        self.write_usage(410_000)
        for payload in ({"compaction_trigger": "manual"}, {"trigger": "manual"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_hook("precompact", payload)[0], 0)

    def test_skips_subagents_disabled_and_inactive(self):
        self.hold()
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("precompact", {"agent_id": "a1"})[0], 0)
        self.assertEqual(self.run_hook("precompact", env=dict(self.env, COMPACTOR_DISABLE="1"))[0], 0)
        inactive = dict(self.env)
        del inactive["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
        self.assertEqual(self.run_hook("precompact", env=inactive)[0], 0)

    def test_garbage_stdin_fails_open_and_logs(self):
        self.hold()
        self.assertEqual(self.run_hook("precompact", raw="{not json")[0], 0)
        self.assertIsNotNone(last_error(self.env))

    def test_real_payload_blocks(self):
        payload = fixture_payload("pre_compact.json")
        payload.update(session_id=SESSION, transcript_path=str(self.transcript_path()))
        for key in ("compaction_trigger", "trigger"):  # captured via /compact, so it says "manual"
            if key in payload:
                payload[key] = "auto"
        self.hold()
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("precompact", raw=json.dumps(payload))[0], 2)


class SessionStartTest(HookTestCase):
    def test_inactive_notice_only_on_startup(self):
        inactive = dict(self.env)
        del inactive["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
        _, out, _ = self.run_hook("session_start", {"source": "startup"}, env=inactive)
        self.assertIn("inactive", self.context_of(out))
        self.assertEqual(self.run_hook("session_start", {"source": "resume"}, env=inactive)[1], "")

    def test_startup_reminds_agent_of_cli(self):
        _, out, _ = self.run_hook("session_start", {"source": "startup"})
        self.assertIn('compactor hold "<why>"', self.context_of(out))

    def test_compact_reinjects_note(self):
        save(SESSION, State(note=Note("next: fix client.py:88", iso_minutes_ago(3))), self.env)
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        text = self.context_of(out)
        self.assertIn("before compaction", text)
        self.assertIn("next: fix client.py:88", text)

    def test_compact_reports_and_clears_ceiling_override(self):
        save(SESSION, State(ceiling_override=CeilingOverride(iso_minutes_ago(0), 90.4, "refactor")), self.env)
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        self.assertIn("overridden", self.context_of(out))
        self.assertIsNone(self.state().ceiling_override)

    def test_resume_and_fork_reinject_note(self):
        save(SESSION, State(note=Note("carry on", iso_minutes_ago(3))), self.env)
        for source in ("resume", "fork"):
            with self.subTest(source=source):
                _, out, _ = self.run_hook("session_start", {"source": source})
                self.assertIn("earlier in this session", self.context_of(out))

    def test_mentions_active_hold(self):
        self.hold()
        _, out, _ = self.run_hook("session_start", {"source": "startup"})
        self.assertIn("A compaction hold is active", self.context_of(out))


class SessionEndTest(HookTestCase):
    def test_clears_hold_keeps_note(self):
        self.hold(note=Note("keep me", iso_minutes_ago(1)))
        self.assertEqual(self.run_hook("session_end", {"reason": "prompt_input_exit"})[0], 0)
        state = self.state()
        self.assertIsNone(state.hold)
        self.assertEqual(state.note.text, "keep me")

    def test_does_not_create_state_when_nothing_to_clear(self):
        self.run_hook("session_end")
        self.assertFalse(state_path(SESSION, self.env).exists())


class LauncherTest(HookTestCase):
    def launch(self, event: str, stdin: str) -> int:
        env = dict(os.environ, **self.env)
        return subprocess.run([sys.executable, str(REPO_ROOT / "hooks" / "run.py"), event], input=stdin,
                              env=env, capture_output=True, text=True, timeout=30).returncode

    def test_garbage_and_unknown_event_exit_zero(self):
        self.assertEqual(self.launch("precompact", "garbage"), 0)
        self.assertEqual(self.launch("no_such_event", "{}"), 0)

    def test_block_exit_code_passes_through(self):
        self.hold()
        self.write_usage(410_000)
        body = json.dumps({"session_id": SESSION, "transcript_path": str(self.transcript_path())})
        self.assertEqual(self.launch("precompact", body), 2)


class HooksJsonTest(unittest.TestCase):
    def test_commands_match_handlers(self):
        config = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text())
        targets, matchers = set(), {}
        for event_name, groups in config["hooks"].items():
            for group in groups:
                matchers[event_name] = group.get("matcher")
                for hook in group["hooks"]:
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/run.py", hook["command"])
                    targets.add(hook["command"].rsplit(" ", 1)[-1])
        self.assertEqual(targets, set(HANDLERS))
        self.assertEqual(matchers["PreCompact"], "auto")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_hooks_core -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'compactor.hooks'`

- [ ] **Step 4: Implement `compactor/hooks/_common.py`**

```python
"""Shared plumbing for hook handlers."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional

from ..config import Settings
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

    def usage(self, state: State) -> Optional[Usage]:
        path = self.payload.get("transcript_path")
        return read_usage(path if isinstance(path, str) else None, self.settings, state.window)


@dataclass
class HookResult:
    exit_code: int = 0
    stdout: Optional[str] = None
    stderr: Optional[str] = None


def with_context(event_name: str, text: str) -> HookResult:
    body = {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}}
    return HookResult(stdout=json.dumps(body))
```

- [ ] **Step 5: Implement `compactor/hooks/precompact.py`**

```python
"""PreCompact (matcher "auto"): the gate. Spec §5.1."""
from __future__ import annotations

from .. import messages
from ..policy import BLOCK, decide_gate
from ..state import CeilingOverride, NudgeState, to_iso
from ._common import HookContext, HookResult


def handle(ctx: HookContext) -> HookResult:
    trigger = ctx.payload.get("compaction_trigger", ctx.payload.get("trigger"))
    if trigger not in (None, "auto"):
        return HookResult()
    state = ctx.load_state()
    usage = ctx.usage(state)
    decision = decide_gate(state.hold, usage, ctx.settings, ctx.now)
    if decision.action == BLOCK:
        return HookResult(exit_code=2, stderr=messages.gate_block(state.hold, usage, ctx.settings, ctx.now) + "\n")
    if decision.override and state.hold is not None:
        state.ceiling_override = CeilingOverride(
            at=to_iso(ctx.now),
            pct=round(usage.pct, 1) if usage is not None else None,
            reason=state.hold.reason,
        )
        state.hold = None
        state.nudge = NudgeState()
        ctx.save_state(state)
    return HookResult()
```

- [ ] **Step 6: Implement `compactor/hooks/session_start.py`**

```python
"""SessionStart: CLI reminder, handoff note, ceiling-override notice. Spec §5.5."""
from __future__ import annotations

from typing import List

from .. import messages
from ._common import HookContext, HookResult, with_context


def handle(ctx: HookContext) -> HookResult:
    source = ctx.payload.get("source")
    if not ctx.settings.active:
        if source == "startup":
            return with_context("SessionStart", messages.inactive_notice(ctx.settings))
        return HookResult()
    state = ctx.load_state()
    parts: List[str] = []
    if source == "compact":
        if state.ceiling_override is not None:
            parts.append(messages.ceiling_notice(state.ceiling_override))
            state.ceiling_override = None
            ctx.save_state(state)
        if state.note is not None:
            parts.append(messages.handoff(state.note, after_compaction=True))
    elif source in ("resume", "fork") and state.note is not None:
        parts.append(messages.handoff(state.note, after_compaction=False))
    if state.hold is not None:
        parts.append(messages.hold_active(state.hold, ctx.now))
    parts.append(messages.CLI_REMINDER)
    return with_context("SessionStart", "\n\n".join(parts))
```

- [ ] **Step 7: Implement `compactor/hooks/session_end.py`**

```python
"""SessionEnd: a session never leaves a stale hold behind. Spec §5.6."""
from __future__ import annotations

from ..state import NudgeState
from ._common import HookContext, HookResult


def handle(ctx: HookContext) -> HookResult:
    state = ctx.load_state()
    if state.hold is None and not state.stop_blocked_this_turn:
        return HookResult()
    state.hold = None
    state.nudge = NudgeState()
    state.stop_blocked_this_turn = False
    ctx.save_state(state)
    return HookResult()
```

- [ ] **Step 8: Implement `compactor/hooks/__init__.py`**

```python
"""Hook entry points. dispatch() never raises, and never blocks because of its own failure."""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import IO, Callable, Dict, Mapping, Optional

from ..config import load_settings
from ..state import append_error, utcnow
from . import precompact, session_end, session_start
from ._common import HookContext, HookResult

HANDLERS: Dict[str, Callable[[HookContext], HookResult]] = {
    "precompact": precompact.handle,
    "session_start": session_start.handle,
    "session_end": session_end.handle,
}

# Still runs while the threshold var is unset, to tell the agent the plugin is off.
RUNS_WHEN_INACTIVE = {"session_start"}


def dispatch(event: str, stdin: Optional[IO[str]] = None, out: Optional[IO[str]] = None,
             err: Optional[IO[str]] = None, env: Optional[Mapping[str, str]] = None,
             now: Optional[datetime] = None) -> int:
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    env = os.environ if env is None else env
    try:
        handler = HANDLERS.get(event)
        if handler is None:
            return 0
        payload = json.loads(stdin.read() or "{}")
        if not isinstance(payload, dict) or payload.get("agent_id") or not payload.get("session_id"):
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
```

- [ ] **Step 9: Create `hooks/run.py`**

```python
#!/usr/bin/env python3
"""Hook launcher: `python3 run.py <event>`. Fails open: any error, even an import error, exits 0."""
import os
import sys


def _run() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from compactor.hooks import dispatch

    return dispatch(sys.argv[1] if len(sys.argv) > 1 else "")


if __name__ == "__main__":
    try:
        code = _run()
    except Exception:
        code = 0
    sys.exit(code)
```

- [ ] **Step 10: Create `hooks/hooks.json`**

```json
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "auto",
        "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/run.py\" precompact" }]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup|resume|clear|compact|fork",
        "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/run.py\" session_start" }]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/run.py\" session_end" }]
      }
    ]
  }
}
```

- [ ] **Step 11: Run the tests to verify they pass, then run the full suite**

Run: `python3 -m unittest tests.test_hooks_core -v`
Expected: `OK`
Run: `python3 -m unittest discover -s tests -t . -v`
Expected: `OK`

- [ ] **Step 12: Commit**

```bash
git add hooks/ compactor/hooks/ tests/hook_helpers.py tests/test_hooks_core.py
git commit -m "feat: add precompact gate, session start and session end hooks

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Nudge hooks: UserPromptSubmit, PostToolUse, and Stop

**Before starting,** read `docs/superpowers/specs/2026-09-29-verification.md`. If its "Plan
adjustments" section describes a different Bash response shape, extend `bash_succeeded` to
match it, and keep the existing branches as defensive fallbacks.

**Files:**
- Create: `compactor/hooks/user_prompt_submit.py`, `compactor/hooks/post_tool_use.py`,
  `compactor/hooks/stop.py`
- Modify: `compactor/hooks/_common.py` (add `apply_nudge`), `compactor/hooks/__init__.py`
  (register handlers), `hooks/hooks.json` (register events)
- Test: `tests/test_hooks_nudges.py`

**Interfaces:**
- Consumes:
  - `policy`: `decide_nudge`, `should_block_stop`, `should_suggest_breakpoint`,
    `is_breakpoint`, `invokes_compactor`
  - `messages`: `nudge`, `breakpoint_suggestion`, `stop_block`
  - `_common`: `HookContext`, `HookResult`, `with_context`
- Produces:
  - `_common.apply_nudge(ctx, state, usage) -> Optional[str]`, which mutates `state.nudge`
  - `post_tool_use.bash_succeeded(payload: Dict) -> bool`
  - `HANDLERS` gains `"user_prompt_submit"`, `"post_tool_use"` and `"stop"`

- [ ] **Step 1: Write the failing tests**

`tests/test_hooks_nudges.py`:

```python
from __future__ import annotations

import json
import unittest

from compactor.hooks.post_tool_use import bash_succeeded
from compactor.state import NudgeState, State, save, state_path
from tests.helpers import FIXTURES, SESSION
from tests.hook_helpers import HookTestCase, fixture_payload


def bash(command: str, **extra):
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "tool_response": {"stdout": "", "stderr": "", "interrupted": False}}
    payload.update(extra)
    return payload


class UserPromptSubmitTest(HookTestCase):
    def test_clears_stop_flag(self):
        save(SESSION, State(stop_blocked_this_turn=True), self.env)
        self.run_hook("user_prompt_submit")
        self.assertFalse(self.state().stop_blocked_this_turn)

    def test_nudges_once_then_rate_limits(self):
        self.hold()
        self.write_usage(410_000)
        text = self.context_of(self.run_hook("user_prompt_submit")[1])
        self.assertIn("Compaction is being held", text)
        self.assertIn("410k of 1M tokens used", text)
        self.assertEqual(self.run_hook("user_prompt_submit")[1], "")

    def test_nudge_renders_reason_with_braces(self):
        self.hold(reason="fix {placeholder} parsing")
        self.write_usage(410_000)
        self.assertIn("fix {placeholder} parsing", self.context_of(self.run_hook("user_prompt_submit")[1]))

    def test_no_hold_is_silent_and_writes_nothing(self):
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("user_prompt_submit")[1], "")
        self.assertFalse(state_path(SESSION, self.env).exists())


class PostToolUseTest(HookTestCase):
    def test_nudges_during_autonomous_work(self):
        self.hold()
        self.write_usage(410_000)
        self.assertIn("Compaction is being held", self.context_of(self.run_hook("post_tool_use", bash("ls"))[1]))

    def test_commit_suggests_release_once(self):
        self.hold(nudge=NudgeState(last_level=1))
        self.write_usage(410_000)
        text = self.context_of(self.run_hook("post_tool_use", bash("git commit -m x"))[1])
        self.assertIn("natural breakpoint", text)
        self.assertEqual(self.run_hook("post_tool_use", bash("git commit -m y"))[1], "")

    def test_failed_commands_are_not_breakpoints(self):
        self.write_usage(410_000)
        failures = (
            bash("git commit -m x", tool_response={"stdout": "", "exit_code": 1}),
            bash("pytest", tool_response="Error: Exit code 1\nFAILED"),
            bash("pytest", tool_response={"stdout": "", "interrupted": True}),
        )
        for payload in failures:
            with self.subTest(payload=payload["tool_response"]):
                self.hold(nudge=NudgeState(last_level=1))
                self.assertEqual(self.run_hook("post_tool_use", payload)[1], "")

    def test_below_threshold_is_silent(self):
        self.hold()
        self.write_usage(100_000)
        self.assertEqual(self.run_hook("post_tool_use", bash("git commit -m x"))[1], "")

    def test_compactor_commands_are_ignored(self):
        self.hold()
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("post_tool_use", bash("compactor status"))[1], "")
        self.assertEqual(self.state().nudge, NudgeState())


class BashSucceededTest(unittest.TestCase):
    def test_shapes(self):
        self.assertTrue(bash_succeeded({"tool_response": {"stdout": "ok", "stderr": "", "interrupted": False}}))
        self.assertTrue(bash_succeeded({"tool_response": {"exit_code": 0}}))
        self.assertFalse(bash_succeeded({"tool_response": {"exit_code": 2}}))
        self.assertFalse(bash_succeeded({"tool_response": {"is_error": True}}))
        self.assertTrue(bash_succeeded({"tool_output": "done"}))
        self.assertFalse(bash_succeeded({"tool_output": "Exit code 1\nboom"}))

    def test_real_success_payload(self):
        self.assertTrue(bash_succeeded(fixture_payload("post_tool_use_bash_ok.json")))

    def test_real_failure_payload(self):
        path = FIXTURES / "payloads" / "post_tool_use_bash_fail.json"
        if not path.exists():
            self.skipTest("a failing Bash command did not fire PostToolUse (see verification notes)")
        self.assertFalse(bash_succeeded(json.loads(path.read_text())))


class StopTest(HookTestCase):
    def test_blocks_once_per_turn(self):
        self.hold()
        self.write_usage(410_000)
        _, out, _ = self.run_hook("stop", {"stop_hook_active": False})
        decision = json.loads(out)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("compactor release", decision["reason"])
        self.assertTrue(self.state().stop_blocked_this_turn)
        self.assertEqual(self.run_hook("stop", {"stop_hook_active": False})[1], "")

    def test_never_blocks_a_continuation(self):
        self.hold()
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("stop", {"stop_hook_active": True})[1], "")

    def test_silent_below_threshold_or_without_hold(self):
        self.write_usage(100_000)
        self.hold()
        self.assertEqual(self.run_hook("stop")[1], "")
        save(SESSION, State(), self.env)
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("stop")[1], "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_hooks_nudges -v`
Expected: ERROR with `ModuleNotFoundError: No module named 'compactor.hooks.post_tool_use'`

- [ ] **Step 3: Add `apply_nudge` to `compactor/hooks/_common.py`**

Add these imports next to the existing ones:

```python
from .. import messages
from ..policy import decide_nudge
```

Append:

```python
def apply_nudge(ctx: HookContext, state: State, usage: Optional[Usage]) -> Optional[str]:
    """Advance the nudge rate limiter in `state`; return the nudge text if one is due."""
    decision = decide_nudge(state.nudge, state.hold, usage, ctx.settings)
    state.nudge = decision.state
    if not decision.emit or state.hold is None:
        return None
    return messages.nudge(decision.level, state.hold, usage, ctx.settings, ctx.now)
```

- [ ] **Step 4: Implement `compactor/hooks/user_prompt_submit.py`**

```python
"""UserPromptSubmit: start-of-turn nudge, and reset the once-per-turn Stop flag. Spec §5.2."""
from __future__ import annotations

import copy

from ._common import HookContext, HookResult, apply_nudge, with_context


def handle(ctx: HookContext) -> HookResult:
    state = ctx.load_state()
    before = copy.deepcopy(state)
    state.stop_blocked_this_turn = False
    text = apply_nudge(ctx, state, ctx.usage(state)) if state.hold is not None else None
    if state != before:
        ctx.save_state(state)
    return with_context("UserPromptSubmit", text) if text else HookResult()
```

- [ ] **Step 5: Implement `compactor/hooks/post_tool_use.py`**

```python
"""PostToolUse (Bash): nudges during autonomous work, and breakpoint suggestions. Spec §5.2, §5.4."""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List

from .. import messages
from ..policy import invokes_compactor, is_breakpoint, should_suggest_breakpoint
from ._common import HookContext, HookResult, apply_nudge, with_context

_EXIT_CODE_TEXT = re.compile(r"^\s*(?:Error:\s*)?Exit code [1-9]\d*")


def bash_succeeded(payload: Dict[str, Any]) -> bool:
    """Best-effort success check across the Bash response shapes Claude Code has used."""
    response = payload.get("tool_response", payload.get("tool_output"))
    if isinstance(response, dict):
        for key in ("exit_code", "exitCode", "return_code", "returnCode"):
            if isinstance(response.get(key), int):
                return response[key] == 0
        if response.get("is_error") or response.get("isError") or response.get("interrupted"):
            return False
        response = response.get("stdout", "")
    if isinstance(response, str) and _EXIT_CODE_TEXT.match(response):
        return False
    return True


def handle(ctx: HookContext) -> HookResult:
    if ctx.payload.get("tool_name") not in (None, "Bash"):
        return HookResult()
    state = ctx.load_state()
    if state.hold is None:
        return HookResult()
    tool_input = ctx.payload.get("tool_input")
    command = str(tool_input.get("command") or "") if isinstance(tool_input, dict) else ""
    if invokes_compactor(command):
        return HookResult()
    before = copy.deepcopy(state)
    usage = ctx.usage(state)
    parts: List[str] = []
    nudge_text = apply_nudge(ctx, state, usage)
    if nudge_text:
        parts.append(nudge_text)
    if should_suggest_breakpoint(state.hold, usage, ctx.settings, state.nudge):
        kind = is_breakpoint(command, bash_succeeded(ctx.payload), ctx.settings.breakpoint_patterns)
        if kind:
            state.nudge.breakpoint_suggested = True
            parts.append(messages.breakpoint_suggestion(kind))
    if state != before:
        ctx.save_state(state)
    return with_context("PostToolUse", "\n".join(parts)) if parts else HookResult()
```

- [ ] **Step 6: Implement `compactor/hooks/stop.py`**

```python
"""Stop: catch a forgotten hold at the end of a turn, at most once per turn. Spec §5.3."""
from __future__ import annotations

import json

from .. import messages
from ..policy import should_block_stop
from ._common import HookContext, HookResult


def handle(ctx: HookContext) -> HookResult:
    state = ctx.load_state()
    if state.hold is None:
        return HookResult()
    usage = ctx.usage(state)
    if not should_block_stop(state.hold, usage, ctx.settings,
                             bool(ctx.payload.get("stop_hook_active")),
                             state.stop_blocked_this_turn, ctx.now):
        return HookResult()
    state.stop_blocked_this_turn = True
    ctx.save_state(state)
    reason = messages.stop_block(state.hold, usage, ctx.settings, ctx.now)
    return HookResult(stdout=json.dumps({"decision": "block", "reason": reason}))
```

- [ ] **Step 7: Register the handlers in `compactor/hooks/__init__.py`**

Replace the two lines `from . import precompact, session_end, session_start` and
`from ._common import HookContext, HookResult`, and the `HANDLERS` dict, with:

```python
from . import post_tool_use, precompact, session_end, session_start, stop, user_prompt_submit
from ._common import HookContext, HookResult

HANDLERS: Dict[str, Callable[[HookContext], HookResult]] = {
    "precompact": precompact.handle,
    "session_start": session_start.handle,
    "session_end": session_end.handle,
    "user_prompt_submit": user_prompt_submit.handle,
    "post_tool_use": post_tool_use.handle,
    "stop": stop.handle,
}
```

- [ ] **Step 8: Register the events in `hooks/hooks.json`**

Add these three keys inside `"hooks"`, next to the existing ones:

```json
    "UserPromptSubmit": [
      {
        "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/run.py\" user_prompt_submit" }]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/run.py\" post_tool_use" }]
      }
    ],
    "Stop": [
      {
        "hooks": [{ "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/run.py\" stop" }]
      }
    ]
```

- [ ] **Step 9: Run the tests to verify they pass, then run the full suite**

Run: `python3 -m unittest tests.test_hooks_nudges -v`
Expected: `OK` (at most one skip, `test_real_failure_payload`, if Task 1 found that failing
Bash doesn't fire PostToolUse)
Run: `python3 -m unittest discover -s tests -t . -v`
Expected: `OK`. `HooksJsonTest` now checks all six handlers.

- [ ] **Step 10: Commit**

```bash
git add compactor/hooks/ hooks/hooks.json tests/test_hooks_nudges.py
git commit -m "feat: add usage-aware nudges, breakpoint suggestions and stop reminder

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: The plugin surface: skill, manifest, and marketplace

**Files:**
- Create: `skills/compactor/SKILL.md`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
- Test: `tests/test_plugin_files.py`

**Interfaces:**
- Consumes: the CLI command names from Task 8 (`hold`, `release --note`, `note`,
  `note --clear`, `status`)
- Produces: an installable plugin named `compactor`, version `0.1.0`

- [ ] **Step 1: Write the failing tests**

`tests/test_plugin_files.py`:

```python
from __future__ import annotations

import json
import unittest

from compactor import __version__
from tests.helpers import REPO_ROOT


class PluginFilesTest(unittest.TestCase):
    def test_manifest(self):
        manifest = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "compactor")
        self.assertEqual(manifest["version"], __version__)

    def test_marketplace_points_at_repo_root(self):
        market = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
        (plugin,) = market["plugins"]
        self.assertEqual((plugin["name"], plugin["source"], plugin["version"]), ("compactor", "./", __version__))

    def test_skill_frontmatter_and_commands(self):
        text = (REPO_ROOT / "skills" / "compactor" / "SKILL.md").read_text()
        self.assertTrue(text.startswith("---\nname: compactor\ndescription: "))
        for command in ('compactor hold "', "compactor release --note", "compactor note --clear", "compactor status"):
            self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_plugin_files -v`
Expected: ERROR with `FileNotFoundError` for `plugin.json`

- [ ] **Step 3: Create `.claude-plugin/plugin.json`**

```json
{
  "name": "compactor",
  "description": "Agent-controlled auto-compaction: hold compaction during fragile work, release it at safe breakpoints, and leave handoff notes that survive it.",
  "version": "0.1.0",
  "author": { "name": "Ryan Wendt" },
  "homepage": "https://github.com/rhwendt/compactor",
  "repository": "https://github.com/rhwendt/compactor",
  "license": "MIT",
  "keywords": ["compaction", "context", "hooks", "handoff"]
}
```

- [ ] **Step 4: Create `.claude-plugin/marketplace.json`**

```json
{
  "name": "compactor",
  "description": "Marketplace for the compactor Claude Code plugin",
  "owner": { "name": "Ryan Wendt" },
  "plugins": [
    {
      "name": "compactor",
      "description": "Agent-controlled auto-compaction with handoff notes",
      "version": "0.1.0",
      "source": "./",
      "author": { "name": "Ryan Wendt" }
    }
  ]
}
```

- [ ] **Step 5: Create `skills/compactor/SKILL.md`**

~~~markdown
---
name: compactor
description: Use when starting fragile multi-step work (debugging chains, multi-file refactors, long autonomous runs, dispatching subagents) in a session with the compactor plugin, or when a compactor message mentions a hold, nudge, ceiling, breakpoint or handoff note.
---

# compactor: you control auto-compaction

Auto-compaction fires when context passes the configured threshold. With this plugin you can
**hold** it during fragile work and **release** it at a safe point. You cannot start a
compaction yourself. Releasing lets the next automatic check go through.

## Commands (on your Bash PATH)

| Command | Use |
|---|---|
| `compactor hold "<why>"` | Before fragile work. The reason is required, so be specific. |
| `compactor release --note "<what matters>"` | At a natural breakpoint. The note comes back after compaction. |
| `compactor note "<text>"` / `compactor note --clear` | Update or clear the handoff note without releasing. |
| `compactor status` | Show the hold, the note, and context usage. |

## When to hold

- A debugging chain whose evidence so far lives only in this conversation
- A multi-file refactor or migration in progress
- A long autonomous run, or before dispatching subagents whose results you must integrate

## When to release

- A task is done, tests are green, or a commit landed
- You're switching topics
- A nudge says you're well past the threshold, or the ceiling is near
- Before ending your turn, unless the next turn continues the same fragile work

## Handoff notes

Before releasing, write down anything that isn't on disk yet and that you'd need after
compaction: the current hypothesis, the next steps, the file:line references in play, and
decisions made. Prefer `compactor release --note "..."` so the note and the release happen
together. A note persists across compactions until you replace or clear it, so clear stale
notes with `compactor note --clear`.

## Rules

- Never hold without a specific reason.
- Never end an autonomous run with a hold set.
- Don't release and re-hold in the same turn just to silence nudges.
- Release doesn't mean compact now. Compaction happens at the next threshold check.
- The safety ceiling overrides any hold near the context limit, so write your note before
  that happens.
- Use the context lines in nudges (tokens used, your auto-compact threshold, the safety
  ceiling) to decide when to release.
~~~

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_plugin_files -v`
Expected: `OK`

- [ ] **Step 7: Validate the plugin with Claude Code**

Run: `claude plugin validate .`
Expected: no errors. If this Claude Code version has no `validate` subcommand, run
`claude --plugin-dir . -p "Run: compactor status"` instead, and confirm the output starts with
`compactor:`.

- [ ] **Step 8: Commit**

```bash
git add .claude-plugin skills tests/test_plugin_files.py
git commit -m "feat: add plugin manifest, marketplace entry and agent skill

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Distribution: README, CHANGELOG, LICENSE, CI, and the manual smoke test

**Files:**
- Modify: `README.md` (full rewrite)
- Create: `CHANGELOG.md`, `LICENSE`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: the env vars from `config.py`, the CLI from Task 8, and the hooks from Tasks 9
  and 10
- Produces: a publishable repo

- [ ] **Step 1: Rewrite `README.md`**

~~~markdown
# compactor

Agent-controlled auto-compaction for Claude Code. The agent **holds** auto-compaction during
fragile work, such as debugging chains or multi-file refactors, and **releases** it at a safe
breakpoint. It can leave a **handoff note** that comes back after compaction.

## What "control" means

Claude Code can't be told to compact early, by hooks or by the model. So compactor controls
**when compaction is allowed**:

- Below your threshold, nothing compacts.
- At or above it, compaction proceeds unless the agent holds it. Releasing lets it through
  at the next automatic check.
- A **safety ceiling** (90% of the window by default) overrides any hold, so a session never
  runs into the hard limit.

## Requirements

- Claude Code with plugin support
- `python3` (3.9 or newer) on your PATH. It uses the standard library only. On Windows, make
  sure `python3` resolves, for example through the Python launcher or an alias.

## Install

```
/plugin marketplace add rhwendt/compactor
/plugin install compactor@compactor
```

## Enable

compactor is inactive until you set a threshold, which also pulls auto-compaction below the
model's default. Add it to `~/.claude/settings.json`:

```json
{ "env": { "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350000" } }
```

Restart Claude Code. The agent is told about the commands at session start.

## How the agent uses it

| Command | Effect |
|---|---|
| `compactor hold "<why>"` | Blocks auto-compaction until released. A reason is required. |
| `compactor release [--note "<text>"]` | Allows compaction again, optionally saving a handoff note. |
| `compactor note "<text>"` / `--clear` | Sets or clears the handoff note. |
| `compactor status [--json \| --line]` | Shows the hold, the note, and context usage. |

While a hold is set and context is past the threshold, the agent receives nudges that get
firmer as usage grows. Each one ends with three lines like:

```
Context: 410k of 1M tokens used (41% of the model's window).
Auto-compact threshold: 350k (CLAUDE_CODE_AUTO_COMPACT_WINDOW) — you are 60k past it; your hold is what's stopping compaction.
Safety ceiling: 900k (90% of window) — your hold is overridden there, 490k from now.
```

It also receives a
one-time suggestion to release after a commit or a passing test run, and a reminder if it
tries to end its turn while holding. Holds are cleared when the session ends.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | unset (plugin inactive) | Auto-compact threshold in tokens (100000 to 1000000) |
| `COMPACTOR_CEILING_PCT` | `90` | Percentage of the window at which a hold is overridden (50 to 98) |
| `COMPACTOR_CONTEXT_WINDOW` | inferred | Window size in tokens, for the context % |
| `COMPACTOR_NUDGE_EVERY` | `10` | Tool calls between repeat nudges |
| `COMPACTOR_BREAKPOINT_PATTERNS` | none | Extra regexes, separated by `;`, for commands that count as breakpoints |
| `COMPACTOR_MAX_HOLD_MIN` | `60` | Overrides a hold after this many minutes if usage can't be read |
| `COMPACTOR_DISABLE` | unset | Any value turns the plugin off |

## Status line

`compactor status --line` prints `⏸ held 12m · 41%`, `▶ 41%`, or `compactor off`. It reads the
statusline JSON on stdin, which also teaches compactor your exact window size. In
`~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 \"$(ls -td ~/.claude/plugins/cache/*/compactor/*/bin/compactor | head -1)\" status --line"
  }
}
```

If you already have a status line script, pipe its stdin into that command and append the
output.

## Troubleshooting

- Run `compactor status` in a session (or ask the agent to). It shows whether the plugin is
  active, any config warnings, and the last hook error.
- Hook errors are logged to `${XDG_STATE_HOME:-~/.local/state}/claude-compactor/errors.log`.
  Hooks always fail open, so a bug in compactor never blocks compaction.
- State lives in that same directory, one JSON file per session, and is pruned after 7 days.

## Manual smoke test

1. Set `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` and start `claude`.
2. Ask the agent to run `compactor hold "smoke test"`, then to read several large files until
   `compactor status` shows it past the threshold.
3. Confirm that auto-compaction is blocked (a PreCompact message appears) and that the agent
   receives nudges.
4. Ask the agent to run `compactor release --note "smoke note"`, then send another prompt.
   Confirm that compaction runs, and that the agent can quote "smoke note" afterwards.

## Development

```
python3 -m unittest discover -s tests -t . -v
claude --plugin-dir .
```

Design: `docs/superpowers/specs/2026-09-29-compactor-design.md`.

## License

MIT
~~~

- [ ] **Step 2: Create `CHANGELOG.md`**

```markdown
# Changelog

## 0.1.0 (unreleased)

- Initial release: a hold/release gate for auto-compaction, handoff notes re-injected after
  compaction, usage-aware nudges with context lines, breakpoint suggestions, a safety
  ceiling, hold cleanup at session end, and `compactor status --line`.
```

- [ ] **Step 3: Create `LICENSE`**

```text
MIT License

Copyright (c) 2026 Ryan Wendt

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Create `.github/workflows/ci.yml`**

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ["3.9", "3.10", "3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - name: Unit tests
        run: python -m unittest discover -s tests -t . -v
      - name: Manifests parse
        run: python -c "import json, sys; [json.load(open(p)) for p in sys.argv[1:]]" .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json
```

- [ ] **Step 5: Run the full suite and the manifest check**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: `OK` (with at most the one skip described in Task 10)
Run: `python3 -c "import json, sys; [json.load(open(p)) for p in sys.argv[1:]]" .claude-plugin/plugin.json .claude-plugin/marketplace.json hooks/hooks.json && echo manifests-ok`
Expected: `manifests-ok`

- [ ] **Step 6: Run the manual smoke test**

Follow the README's "Manual smoke test" with `claude --plugin-dir .` in place of the installed
plugin. Record the results in `docs/superpowers/specs/2026-09-29-verification.md`, under a new
`## Smoke test` heading. Answer spec §9 item 2: does a blocked auto-compact retry on the next
turn?

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md LICENSE .github/workflows/ci.yml docs/superpowers/specs/2026-09-29-verification.md
git commit -m "docs: add README, changelog, license and CI workflow

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```
