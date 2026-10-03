from __future__ import annotations

import json
import unittest

from compactor.policy import compactor_state_command
from compactor.state import Hold, State, save
from tests.helpers import SESSION, iso_minutes_ago
from tests.hook_helpers import HookTestCase

TEAMMATE = {"agent_id": "aprobe-8d2da64117f22179", "agent_type": "probe"}  # seen live, Claude Code 2.1.287


def bash(command: str, **extra):
    return {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": command}, **extra}


class StateCommandTest(unittest.TestCase):
    def test_detects_hold_release_note_in_any_segment(self):
        for command, expected in (
            ('compactor hold "x"', "hold"),
            ('compactor release --note "n" | tail -2', "release"),
            ('cd /repo && grep -n x f && compactor note "y"', "note"),
            ("/home/u/.claude/plugins/compactor/bin/compactor hold z", "hold"),
            ("compactor status", None),
            ("compactor status --json", None),
            ("cat compactor/cli.py", None),
            ("git commit -m 'compactor hold'", None),
        ):
            with self.subTest(command=command):
                self.assertEqual(compactor_state_command(command), expected)


class TeammateGuardTest(HookTestCase):
    def deny_reason(self, out: str):
        if not out:
            return None
        output = json.loads(out)["hookSpecificOutput"]
        self.assertEqual((output["hookEventName"], output["permissionDecision"]), ("PreToolUse", "deny"))
        return output["permissionDecisionReason"]

    def test_teammate_cannot_hold_release_or_note(self):
        # Seen live: an in-process teammate held five times and replaced the main agent's note.
        for command in ('compactor hold "p7e-writer: revising"', 'compactor release --note "mine"',
                        'compactor note "mine"'):
            with self.subTest(command=command):
                code, out, _ = self.run_hook("pre_tool_use", bash(command, **TEAMMATE))
                self.assertEqual(code, 0)
                reason = self.deny_reason(out)
                self.assertIn("main agent", reason)
                self.assertIn("compactor status", reason)

    def test_teammate_may_run_status_and_other_commands(self):
        for command in ("compactor status", "go test ./..."):
            with self.subTest(command=command):
                self.assertEqual(self.run_hook("pre_tool_use", bash(command, **TEAMMATE))[1], "")

    def test_main_agent_is_never_blocked(self):
        self.assertEqual(self.run_hook("pre_tool_use", bash('compactor hold "x"')), (0, "", ""))

    def test_denied_call_leaves_state_alone(self):
        save(SESSION, State(hold=Hold("main's reason", iso_minutes_ago(5))), self.env)
        self.run_hook("pre_tool_use", bash('compactor hold "teammate reason"', **TEAMMATE))
        self.assertEqual(self.state().hold.reason, "main's reason")


if __name__ == "__main__":
    unittest.main()
