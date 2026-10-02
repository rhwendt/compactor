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
            json.dumps([1, 2]),
            json.dumps({"version": 1, "window": "big"}),
            json.dumps({"version": 1, "window": True}),
            json.dumps({"version": 1, "nudge": {"last_level": "2"}}),
            json.dumps({"version": 1, "nudge": {"last_level": 4}}),
            json.dumps({"version": 1, "nudge": {"last_level": -1}}),
            json.dumps({"version": 1, "nudge": {"last_level": True}}),
            json.dumps({"version": 1, "nudge": {"calls_since": -1}}),
            json.dumps({"version": 1, "nudge": {"calls_since": 1.5}}),
            json.dumps({"version": 1, "nudge": {"calls_since": True}}),
            json.dumps({"version": 1, "nudge": {"breakpoint_suggested": 1}}),
            json.dumps({"version": 1, "nudge": {"breakpoint_suggested": "yes"}}),
            json.dumps({"version": 1, "nudge": [1]}),
            json.dumps({"version": 1, "ceiling_override": {"at": "not-a-date", "pct": 90.0, "reason": "x"}}),
            json.dumps({"version": 1, "ceiling_override": {"at": "2026-09-29T12:00:00Z", "pct": "high", "reason": "x"}}),
            json.dumps({"version": 1, "ceiling_override": {"at": "2026-09-29T12:00:00Z", "pct": None, "reason": 5}}),
        ]
        for content in bad_contents:
            with self.subTest(content=content):
                path.write_text(content)
                self.assertEqual(load(SESSION, self.env), State())

    def test_unknown_keys_from_a_newer_version_are_ignored(self):
        path = state_path(SESSION, self.env)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "version": 1,
            "hold": {"reason": "x", "since": "2026-09-29T12:00:00Z", "extra": 1},
            "note": {"text": "n", "updated_at": "2026-09-29T12:00:00Z", "extra": 1},
            "nudge": {"last_level": 2, "calls_since": 3, "breakpoint_suggested": True, "extra": 1},
            "ceiling_override": {"at": "2026-09-29T12:00:00Z", "pct": 90.0, "reason": "x", "extra": 1},
            "future_field": {"anything": True},
        }))
        self.assertEqual(load(SESSION, self.env), State(
            hold=Hold("x", "2026-09-29T12:00:00Z"),
            note=Note("n", "2026-09-29T12:00:00Z"),
            nudge=NudgeState(2, 3, True),
            ceiling_override=CeilingOverride("2026-09-29T12:00:00Z", 90.0, "x"),
        ))

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
        lines = (state_dir(self.env) / "errors.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), ERROR_LOG_LINES)
        self.assertTrue(lines[-1].endswith(f"e{ERROR_LOG_LINES + 49}"))


if __name__ == "__main__":
    unittest.main()
