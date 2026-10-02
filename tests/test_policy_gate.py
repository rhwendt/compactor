from __future__ import annotations

import unittest

from compactor.config import Settings
from compactor.policy import ALLOW, BLOCK, GateDecision, decide_compaction, decide_gate, hold_age_minutes
from compactor.state import Hold
from compactor.subagents import ActiveSubagent
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

    def test_hold_overridden_when_one_more_turn_would_reach_the_ceiling(self):
        hold = Hold("x", iso_minutes_ago(5))
        self.assertEqual(decide_gate(hold, Usage(850_000, W, growth=50_000), S, NOW),
                         GateDecision(ALLOW, override=True))
        self.assertEqual(decide_gate(hold, Usage(850_000, W, growth=49_999), S, NOW), GateDecision(BLOCK))

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

    def test_threshold_above_ceiling_still_overrides_at_ceiling(self):
        hold = Hold("x", iso_minutes_ago(1))
        for threshold, window in ((950_000, W), (1_000_000, W), (350_000, 200_000)):
            with self.subTest(threshold=threshold, window=window):
                settings = Settings(threshold=threshold)
                ceiling = int(window * 0.9)
                self.assertEqual(decide_gate(hold, Usage(ceiling - 1, window), settings, NOW),
                                 GateDecision(BLOCK))
                self.assertEqual(decide_gate(hold, Usage(ceiling, window), settings, NOW),
                                 GateDecision(ALLOW, override=True))


ALLOW_MODE = Settings(threshold=350_000, subagents="allow")
HOLD = Hold("x", iso_minutes_ago(5))
MAIN = Usage(400_000, W)  # below its 900k ceiling


def sub(used=None, window=200_000, foreground=True, agent_id="a1"):
    return ActiveSubagent(agent_id, None if used is None else Usage(used, window), foreground)


class DecideCompactionTest(unittest.TestCase):
    def test_no_subagent_is_the_main_gate_unchanged(self):
        for hold in (None, HOLD):
            for usage in (None, MAIN, Usage(900_000, W)):
                with self.subTest(hold=hold, usage=usage):
                    self.assertEqual(decide_compaction(hold, usage, [], S, NOW), decide_gate(hold, usage, S, NOW))

    def test_hold_mode_blocks_while_a_subagent_is_below_its_ceiling(self):
        for hold in (None, HOLD):
            with self.subTest(hold=hold):
                self.assertEqual(decide_compaction(hold, MAIN, [sub(150_000)], S, NOW),
                                 GateDecision(BLOCK, subagent=True))

    def test_subagent_at_its_ceiling_allows_and_keeps_the_main_hold(self):
        self.assertEqual(decide_compaction(HOLD, MAIN, [sub(180_000)], S, NOW), GateDecision(ALLOW))
        self.assertEqual(decide_compaction(HOLD, MAIN, [sub(150_000, agent_id="a0"), sub(900_000, W)], S, NOW),
                         GateDecision(ALLOW))

    def test_main_at_its_ceiling_allows_and_overrides_the_hold_when_it_may_be_compacting(self):
        for active in ([sub(10_000, foreground=False)], [sub(10_000), sub(10_000, foreground=False, agent_id="b1")]):
            with self.subTest(active=active):
                self.assertEqual(decide_compaction(HOLD, Usage(900_000, W), active, S, NOW),
                                 GateDecision(ALLOW, override=True))
                self.assertEqual(decide_compaction(None, Usage(900_000, W), active, S, NOW), GateDecision(ALLOW))

    def test_all_foreground_main_at_its_ceiling_allows_without_overriding_the_hold(self):
        # The main agent is waiting on its foreground subagents, so this compaction is theirs:
        # the main hold and its ceiling override wait for the main agent's own next attempt.
        for settings in (S, ALLOW_MODE):
            for active in ([sub(10_000)], [sub(10_000), sub(None, agent_id="a2")]):
                with self.subTest(mode=settings.subagents, active=active):
                    self.assertEqual(decide_compaction(HOLD, Usage(900_000, W), active, settings, NOW),
                                     GateDecision(ALLOW))

    def test_allow_mode_lets_a_foreground_subagent_compact_even_while_main_holds(self):
        for hold in (None, HOLD):
            with self.subTest(hold=hold):
                self.assertEqual(decide_compaction(hold, MAIN, [sub(150_000)], ALLOW_MODE, NOW), GateDecision(ALLOW))

    def test_ambiguity_blocks_if_either_rule_blocks(self):
        background = [sub(150_000, foreground=False)]
        self.assertEqual(decide_compaction(HOLD, MAIN, background, ALLOW_MODE, NOW), GateDecision(BLOCK))
        self.assertEqual(decide_compaction(None, MAIN, background, ALLOW_MODE, NOW), GateDecision(ALLOW))
        self.assertEqual(decide_compaction(HOLD, MAIN, background, S, NOW), GateDecision(BLOCK, subagent=True))

    def test_ambiguity_never_blocks_at_any_ceiling(self):
        background = [sub(180_000, foreground=False)]
        for settings in (S, ALLOW_MODE):
            with self.subTest(mode=settings.subagents):
                self.assertEqual(decide_compaction(HOLD, MAIN, background, settings, NOW), GateDecision(ALLOW))

    def test_unmeasurable_subagent_is_not_held_blind(self):
        self.assertEqual(decide_compaction(None, MAIN, [sub(None)], S, NOW), GateDecision(ALLOW))
        self.assertEqual(decide_compaction(HOLD, MAIN, [sub(None, foreground=False)], S, NOW), GateDecision(BLOCK))

    def test_unmeasurable_background_subagent_gets_the_hold_age_fallback_without_override(self):
        # The compaction may be the unmeasurable subagent's, which could be near its hard limit,
        # so the main hold can't block it past COMPACTOR_MAX_HOLD_MIN, even when the main usage
        # is known and low. It may also not be the main agent's, so the main hold stays.
        old = Hold("x", iso_minutes_ago(61))
        young = Hold("x", iso_minutes_ago(5))
        for main in (None, MAIN):
            for active in ([sub(None, foreground=False)], [sub(None, foreground=False), sub(150_000, agent_id="a2")]):
                with self.subTest(main=main, active=active):
                    self.assertEqual(decide_compaction(old, main, active, S, NOW), GateDecision(ALLOW))
                    self.assertEqual(decide_compaction(young, main, active, S, NOW), GateDecision(BLOCK))
                    self.assertEqual(decide_compaction(None, main, active, S, NOW), GateDecision(ALLOW))
        self.assertEqual(decide_compaction(old, None, [sub(None)], S, NOW), GateDecision(ALLOW))


class UnknownMainUsageTest(unittest.TestCase):
    """R22: with main usage unknown and a background subagent, the compaction may be the main
    agent's, so the main gate (and its hold-age fallback) decides, not the subagent hold."""

    def test_no_main_hold_allows(self):
        background = [sub(120_000, W, foreground=False)]
        self.assertEqual(decide_compaction(None, None, background, S, NOW), GateDecision(ALLOW))
        self.assertEqual(decide_compaction(None, None, background, S, NOW), decide_gate(None, None, S, NOW))

    def test_old_main_hold_is_overridden_by_age(self):
        old = Hold("x", iso_minutes_ago(500))
        background = [sub(120_000, W, foreground=False)]
        self.assertEqual(decide_compaction(old, None, background, S, NOW), GateDecision(ALLOW, override=True))

    def test_young_main_hold_still_blocks(self):
        young = Hold("x", iso_minutes_ago(5))
        background = [sub(120_000, W, foreground=False)]
        self.assertEqual(decide_compaction(young, None, background, S, NOW), GateDecision(BLOCK))

    def test_foreground_subagent_is_still_held(self):
        self.assertEqual(decide_compaction(None, None, [sub(120_000, W)], S, NOW),
                         GateDecision(BLOCK, subagent=True))


class HoldAgeTest(unittest.TestCase):
    def test_minutes(self):
        self.assertEqual(hold_age_minutes(Hold("x", iso_minutes_ago(12.5)), NOW), 12.5)


if __name__ == "__main__":
    unittest.main()
