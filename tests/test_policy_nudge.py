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

    def test_threshold_at_or_above_ceiling(self):
        high = Settings(threshold=950_000)  # ceiling 900k < threshold
        for used, expected in ((849_999, 0), (899_999, 0), (900_000, 3), (960_000, 3)):
            with self.subTest(used=used):
                self.assertEqual(nudge_level(HOLD, Usage(used, W), high), expected)
        near = Settings(threshold=880_000)  # effective threshold 880k is inside the level-3 band
        self.assertEqual(nudge_level(HOLD, Usage(880_000, W), near), 3)
        self.assertEqual(nudge_level(HOLD, Usage(879_999, W), near), 0)


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

    def test_threshold_above_ceiling_blocks_once_ceiling_reached(self):
        high = Settings(threshold=950_000)
        self.assertFalse(should_block_stop(HOLD, Usage(899_999, W), high, False, False, NOW))
        self.assertTrue(should_block_stop(HOLD, Usage(900_000, W), high, False, False, NOW))


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

    def test_test_runners_with_options(self):
        for cmd in ("mvn test", "mvn clean install test", "./gradlew clean test", "gradlew test",
                    "bun test", "yarn test:unit", "npm run test:unit"):
            with self.subTest(cmd=cmd):
                self.assertEqual(is_breakpoint(cmd, True, ()), "tests")

    def test_dry_run_only_cancels_its_own_segment(self):
        self.assertEqual(is_breakpoint("npm run build -- --dry-run && git commit -m ship", True, ()), "commit")
        self.assertIsNone(is_breakpoint("git commit --dry-run -m x && ls", True, ()))


class ShouldSuggestBreakpointTest(unittest.TestCase):
    def test_cases(self):
        past, below = Usage(400_000, W), Usage(100_000, W)
        self.assertTrue(should_suggest_breakpoint(HOLD, past, S, NudgeState()))
        self.assertFalse(should_suggest_breakpoint(HOLD, past, S, NudgeState(breakpoint_suggested=True)))
        self.assertFalse(should_suggest_breakpoint(HOLD, below, S, NudgeState()))
        self.assertFalse(should_suggest_breakpoint(HOLD, None, S, NudgeState()))
        self.assertFalse(should_suggest_breakpoint(None, past, S, NudgeState()))

    def test_threshold_above_ceiling_uses_ceiling(self):
        high = Settings(threshold=950_000)
        self.assertFalse(should_suggest_breakpoint(HOLD, Usage(899_999, W), high, NudgeState()))
        self.assertTrue(should_suggest_breakpoint(HOLD, Usage(900_000, W), high, NudgeState()))


class InvokesCompactorTest(unittest.TestCase):
    def test_cases(self):
        self.assertTrue(invokes_compactor("compactor release"))
        self.assertTrue(invokes_compactor("cd x && compactor hold 'y'"))
        self.assertTrue(invokes_compactor("/usr/local/bin/compactor status"))
        self.assertTrue(invokes_compactor("./compactor status"))
        self.assertFalse(invokes_compactor("cat compactor/cli.py"))
        self.assertFalse(invokes_compactor("echo compactor-ish"))

    def test_paths_and_arguments_named_compactor_are_not_invocations(self):
        for cmd in ("cd /home/user/src/compactor && git commit -m x", "ls ~/git/compactor ",
                    "python -m pytest tests/compactor", "cd compactor && make test",
                    "cat compactor/cli.py"):
            with self.subTest(cmd=cmd):
                self.assertFalse(invokes_compactor(cmd))


if __name__ == "__main__":
    unittest.main()
