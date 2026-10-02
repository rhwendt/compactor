from __future__ import annotations

import unittest

from compactor import messages as m
from compactor.config import Settings
from compactor.model import ActiveSubagent
from compactor.state import CeilingOverride, Hold, Note, State
from compactor.usage import Usage
from tests.helpers import NOW, iso_minutes_ago

S = Settings(threshold=350_000)
W = 1_000_000
HOLD = Hold("mid-refactor of auth", iso_minutes_ago(10))


class FormatTest(unittest.TestCase):
    def test_fmt_tokens(self):
        for n, text in ((999, "999"), (60_000, "60k"), (410_000, "410k"),
                        (999_499, "999k"), (999_600, "1M"),
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

    def test_large_last_turn_warns_the_override_may_come_sooner(self):
        self.assertEqual(m.context_line(Usage(600_000, W, growth=120_000), S, holding=True).splitlines()[2],
                         "Safety ceiling: 900k (90% of window) — your hold is overridden there, 300k from "
                         "now, or a turn sooner: the last turn added 120k.")
        self.assertEqual(m.context_line(Usage(850_000, W, growth=60_000), S, holding=True).splitlines()[2],
                         "Safety ceiling: 900k (90% of window) — 50k away, but the last turn added 60k, so "
                         "a hold is overridden at the next compaction check.")

    def test_ceiling_reached(self):
        self.assertEqual(m.context_line(Usage(900_000, W), S, holding=True).splitlines()[2],
                         "Safety ceiling: 900k (90% of window) — reached; a hold is overridden "
                         "at the next compaction check.")

    def test_threshold_above_ceiling_stays_truthful(self):
        high = Settings(threshold=950_000)
        self.assertEqual(m.context_line(Usage(400_000, W), high, holding=True), (
            "Context: 400k of 1M tokens used (40% of the model's window).\n"
            "Auto-compact threshold: 950k (CLAUDE_CODE_AUTO_COMPACT_WINDOW) — 550k away.\n"
            "Safety ceiling: 900k (90% of window) — your hold is overridden there, 500k from now."))
        past = m.context_line(Usage(960_000, W), high, holding=True).splitlines()
        self.assertEqual(past[1], "Auto-compact threshold: 950k (CLAUDE_CODE_AUTO_COMPACT_WINDOW) — "
                                  "you are 10k past it.")
        self.assertEqual(past[2], "Safety ceiling: 900k (90% of window) — reached; a hold is overridden "
                                  "at the next compaction check.")

    def test_threshold_not_set(self):
        self.assertEqual(m.context_line(Usage(100_000, 200_000), Settings()).splitlines()[1],
                         "Auto-compact threshold: not set (CLAUDE_CODE_AUTO_COMPACT_WINDOW).")

    def test_assumed_window_is_flagged(self):
        # 145_000 / 200_000 = 72.5%; Python's ".0f" rounds .5 to even, giving "72", not "73".
        self.assertEqual(m.context_line(Usage(145_000, 200_000, window_known=False), S).splitlines()[0],
                         "Context: 145k of 200k tokens used (72% of the model's window — window size "
                         "assumed; set COMPACTOR_CONTEXT_WINDOW for exact figures).")

    def test_known_window_output_is_unchanged_when_flag_explicit(self):
        self.assertEqual(m.context_line(Usage(410_000, W, window_known=True), S, holding=True),
                         m.context_line(Usage(410_000, W), S, holding=True))

    def test_unknown(self):
        self.assertEqual(m.context_line(None, S), (
            "Context: usage unknown (no reply has reported it yet, or the transcript couldn't be read).\n"
            "Auto-compact threshold: 350k (CLAUDE_CODE_AUTO_COMPACT_WINDOW).\n"
            "Safety ceiling: with usage unknown, a hold is overridden after 60 minutes "
            "(COMPACTOR_MAX_HOLD_MIN)."))


class AgentMessagesTest(unittest.TestCase):
    def test_gate_block_reads_correctly_to_the_human(self):
        text = m.gate_block(HOLD, Usage(410_000, W), S, NOW)
        self.assertEqual(text.splitlines()[0],
                         "compactor: auto-compaction held by the agent for 10m: mid-refactor of auth. "
                         "It will release at a safe point; the safety ceiling overrides the hold near "
                         "the context limit.")
        self.assertEqual(text.splitlines()[1:], m.context_line(Usage(410_000, W), S).splitlines())
        self.assertNotIn("your hold", text)

    def test_subagent_gate_block_does_not_claim_whose_compaction_it_is(self):
        from compactor.model import ActiveSubagent
        text = m.subagent_gate_block([ActiveSubagent("a1", Usage(150_000, 200_000))], S)
        first = text.splitlines()[0]
        self.assertIn("auto-compaction is being held while a subagent runs", first)
        self.assertIn("may be the main agent's", first)
        self.assertNotIn("a subagent's auto-compaction", first)
        self.assertIn("COMPACTOR_SUBAGENTS=hold", first)
        self.assertIn("Subagent a1: 150k of 200k tokens used", text)

    def test_subagent_notice_tells_a_subagent_to_ignore_the_main_context(self):
        self.assertEqual(m.SUBAGENT_IGNORE, "If you are a subagent: this is the main agent's note and "
                                            "hold — ignore them and don't run compactor.")

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

    def test_ceiling_notice_explains_an_early_override(self):
        text = m.ceiling_notice(CeilingOverride(iso_minutes_ago(0), 56.4, "x", growth_pct=37.6))
        self.assertIn("at 56% context", text)
        self.assertIn("the last turn added 38%", text)

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

    def test_status_line_flags_assumed_window(self):
        assumed = Usage(410_000, W, window_known=False)
        self.assertEqual(m.status_line(None, assumed, S, NOW), "▶ ~41%")
        self.assertEqual(m.status_line(HOLD, assumed, S, NOW), "⏸ held 10m · ~41%")

    def test_status_text(self):
        settings = Settings(threshold=350_000, warnings=("COMPACTOR_CEILING_PCT='x' is not a number; using 90",))
        text = m.status_text(State(), None, settings, NOW, "2026-09-29T12:00:00Z ValueError: boom")
        for part in ("compactor: active (threshold 350k)", "hold: none", "note: none",
                     "Context: usage unknown", "config warning:", "last hook error:"):
            self.assertIn(part, text)
        held = m.status_text(State(hold=HOLD), None, S, NOW, None)
        self.assertIn("hold: mid-refactor of auth (10m)", held)

    def test_status_text_lists_running_subagents(self):
        none = m.status_text(State(), None, S, NOW, None, subagents=[])
        self.assertIn("subagents: none running", none)
        self.assertIn("not background shell commands or monitors", none)
        two = m.status_text(State(), None, S, NOW, None, subagents=[
            ActiveSubagent("a1", None, foreground=True), ActiveSubagent("a2", None)])
        self.assertIn("subagents: 2 running (1 foreground, 1 background)", two)
        self.assertNotIn("subagents:", m.status_text(State(), None, S, NOW, None))  # unknown: no line


if __name__ == "__main__":
    unittest.main()
