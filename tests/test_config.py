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

    def test_subagents_defaults_to_hold(self):
        self.assertEqual(load_settings({}).subagents, "hold")

    def test_subagents_accepts_hold_and_allow_case_insensitively(self):
        for raw, expected in (("hold", "hold"), ("allow", "allow"), (" ALLOW ", "allow"), ("", "hold")):
            with self.subTest(raw=raw):
                s = load_settings({"COMPACTOR_SUBAGENTS": raw})
                self.assertEqual(s.subagents, expected)
                self.assertEqual(s.warnings, ())

    def test_subagents_invalid_falls_back_with_warning(self):
        s = load_settings({"COMPACTOR_SUBAGENTS": "block"})
        self.assertEqual(s.subagents, "hold")
        self.assertEqual(len(s.warnings), 1)
        self.assertIn("COMPACTOR_SUBAGENTS", s.warnings[0])

    def test_disable_turns_plugin_off(self):
        s = load_settings({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "350000", "COMPACTOR_DISABLE": "1"})
        self.assertTrue(s.disabled)
        self.assertFalse(s.active)


class CeilingTokensTest(unittest.TestCase):
    def test_percent_of_window(self):
        self.assertEqual(Settings(threshold=350_000).ceiling_tokens(1_000_000), 900_000)

    def test_capped_at_pct_of_window_even_when_threshold_is_higher(self):
        self.assertEqual(Settings(threshold=950_000).ceiling_tokens(1_000_000), 900_000)
        self.assertEqual(Settings(threshold=1_000_000).ceiling_tokens(1_000_000), 900_000)
        self.assertEqual(Settings(threshold=350_000).ceiling_tokens(200_000), 180_000)

    def test_without_threshold(self):
        self.assertEqual(Settings().ceiling_tokens(200_000), 180_000)


if __name__ == "__main__":
    unittest.main()


class ProcessEnvTest(unittest.TestCase):
    def test_only_compactors_own_variables_are_read(self):
        from unittest import mock
        from compactor.config import ENV_KEYS, process_env
        fake = {"CLAUDE_CODE_SESSION_ID": "s", "COMPACTOR_CEILING_PCT": "80", "GH_TOKEN": "secret",
                "ANTHROPIC_API_KEY": "secret", "PATH": "/bin"}
        with mock.patch.dict("os.environ", fake, clear=True):
            self.assertEqual(process_env(), {"CLAUDE_CODE_SESSION_ID": "s", "COMPACTOR_CEILING_PCT": "80"})
        self.assertTrue(all(k.startswith(("CLAUDE_", "COMPACTOR_", "XDG_")) for k in ENV_KEYS))

    def test_nothing_else_touches_os_environ(self):
        # The whole environment never passes through compactor; process_env() is the one reader.
        import re
        from tests.helpers import PLUGIN_ROOT
        offenders = [str(p.relative_to(PLUGIN_ROOT)) for p in PLUGIN_ROOT.rglob("*.py")
                     if re.search(r"os\.environ\b", p.read_text(encoding="utf-8"))
                     and p.name != "config.py"]
        self.assertEqual(offenders, [])
