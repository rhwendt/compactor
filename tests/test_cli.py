from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from unittest import mock

from compactor import cli
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

    def test_line_window_cache_keeps_a_hold_written_concurrently(self):
        save(SESSION, State(), self.env)
        real_load = load
        calls = []

        def load_then_concurrent_hold(session_id, env=None):
            state = real_load(session_id, env)
            if not calls:  # a `compactor hold` lands right after the status line's first read
                save(SESSION, State(hold=Hold("concurrent", iso_minutes_ago(0))), self.env)
            calls.append(session_id)
            return state

        payload = {"session_id": SESSION, "context_window": {"context_window_size": 1_000_000,
                   "current_usage": {"input_tokens": 500_000}}}
        with mock.patch("compactor.cli.load", side_effect=load_then_concurrent_hold):
            code, _, _ = self.run_cli("status", "--line", stdin=io.StringIO(json.dumps(payload)))
        self.assertEqual(code, 0)
        state = load(SESSION, self.env)
        self.assertEqual(state.window, 1_000_000)
        self.assertEqual(state.hold.reason, "concurrent")

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

    def test_line_does_not_hang_on_idle_pipe_when_select_cannot_poll(self):
        # Windows: select() rejects pipes, so the read happens on a thread with a timeout.
        r, w = os.pipe()
        self.addCleanup(os.close, w)
        with os.fdopen(r) as idle, mock.patch("compactor.cli.select.select", side_effect=OSError):
            start = time.monotonic()
            code, out, _ = self.run_cli("status", "--line", stdin=idle)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertLess(time.monotonic() - start, 5)

    def test_line_reads_a_pipe_when_select_cannot_poll(self):
        r, w = os.pipe()
        os.write(w, json.dumps({"session_id": "abc"}).encode())
        os.close(w)
        with os.fdopen(r) as piped, mock.patch("compactor.cli.select.select", side_effect=OSError):
            self.assertEqual(cli._read_json(piped), {"session_id": "abc"})

    def test_line_does_not_hang_on_idle_pipe(self):
        r, w = os.pipe()
        self.addCleanup(os.close, w)
        with os.fdopen(r) as idle:
            start = time.monotonic()
            code, out, _ = self.run_cli("status", "--line", stdin=idle)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertLess(time.monotonic() - start, 5)


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
        env = self.subprocess_env()
        result = subprocess.run([sys.executable, str(script), "hold", "via bin"], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(load(SESSION, self.env).hold.reason, "via bin")

    def test_bin_script_handles_utf8_whatever_the_stdio_encoding(self):
        script = REPO_ROOT / "bin" / "compactor"
        reason = "fix caf\u00e9 parser \U0001f50d"
        for encoding in (None, "cp1252"):
            with self.subTest(PYTHONIOENCODING=encoding):
                env = self.subprocess_env()
                env.pop("PYTHONIOENCODING", None)
                if encoding:
                    env["PYTHONIOENCODING"] = encoding
                for argv in (["hold", reason], ["status"]):
                    result = subprocess.run([sys.executable, str(script), *argv], env=env,
                                            capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
                    self.assertIn(reason, result.stdout.decode("utf-8"))
                self.assertEqual(load(SESSION, self.env).hold.reason, reason)
