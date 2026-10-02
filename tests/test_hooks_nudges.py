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

    def test_nudge_uses_window_from_transcript_identity(self):
        """The eval scenario (Task 13): 1M model, T=100k, used=145k — window must not default to 200k."""
        self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "100000"
        self.hold()
        self.write_usage(145_000, model_id="claude-opus-5-5[1m]")
        text = self.context_of(self.run_hook("user_prompt_submit")[1])
        self.assertIn("145k of 1M tokens used (14% of the model's window)", text)

    def test_no_hold_is_silent_and_writes_nothing(self):
        self.write_usage(410_000)
        self.assertEqual(self.run_hook("user_prompt_submit")[1], "")
        self.assertFalse(state_path(SESSION, self.env).exists())


class PostToolUseTest(HookTestCase):
    def test_nudges_during_autonomous_work(self):
        self.hold()
        self.write_usage(410_000)
        self.assertIn("Compaction is being held", self.context_of(self.run_hook("post_tool_use", bash("ls"))[1]))

    def test_nudges_on_non_bash_tools(self):
        self.hold()
        self.write_usage(410_000)
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/x/compactor/cli.py"},
                   "tool_response": {"type": "text"}}
        self.assertIn("Compaction is being held", self.context_of(self.run_hook("post_tool_use", payload)[1]))

    def test_non_bash_tools_never_suggest_breakpoints(self):
        self.hold(nudge=NudgeState(last_level=1))
        self.write_usage(410_000)
        for payload in ({"tool_name": "Read", "tool_input": {"command": "git commit -m x"}},
                        {"tool_name": "Grep", "tool_input": {"pattern": "pytest"}}):
            with self.subTest(tool=payload["tool_name"]):
                self.assertEqual(self.run_hook("post_tool_use", payload)[1], "")
        self.assertFalse(self.state().nudge.breakpoint_suggested)

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
        self.assertFalse(bash_succeeded(json.loads(path.read_text(encoding="utf-8"))))


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
