from __future__ import annotations

import json
import subprocess
import sys
import unittest

from compactor.hooks import HANDLERS
from compactor.state import CeilingOverride, Note, State, last_error, save, state_path
from tests.helpers import (
    REPO_ROOT, SESSION, assistant_entry, iso_minutes_ago, tool_result_entry, user_entry, write_transcript,
)
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

    def test_unknown_window_with_threshold_above_200k_still_overrides_near_200k(self):
        # A 200k model whose id doesn't name its window, with a threshold carried over from a 1M
        # setup: guessing 1M from the threshold would put the ceiling at 900k, past the real limit.
        self.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "300000"
        self.hold()
        self.write_usage(190_000, model_id="claude-haiku-4-5-20251001")
        self.assertEqual(self.run_hook("precompact")[0], 0)
        self.assertIsNone(self.state().hold)

    def test_tool_results_since_the_last_reply_count_toward_the_ceiling(self):
        # Replays a live run that died with "Prompt is too long": the last reply reported 104.5k,
        # then 10 parallel tool results (~258k chars, ~78k tokens) landed before the PreCompact.
        # Blocking there let the next turn push a 200k model past its limit.
        self.env.update(CLAUDE_CODE_AUTO_COMPACT_WINDOW="150000", COMPACTOR_CEILING_PCT="98",
                        COMPACTOR_CONTEXT_WINDOW="200000")
        self.hold()
        results = [tool_result_entry("x" * 25_800) for _ in range(10)]
        write_transcript(self.transcript_path(), [user_entry(), assistant_entry(input_tokens=104_544), *results])
        self.assertEqual(self.run_hook("precompact")[0], 0)
        self.assertIsNone(self.state().hold)

    def test_growth_of_the_last_turn_counts_toward_the_ceiling(self):
        # Replays the A/B live run: PreCompact fires as a reply arrives, before most of its tool
        # results are written. The previous turn grew 26.9k -> 102.1k, and the next one grew as
        # much again; blocking here let it reach 177k on a 200k model with a 180k ceiling.
        self.env["COMPACTOR_CONTEXT_WINDOW"] = "200000"
        self.hold()
        write_transcript(self.transcript_path(), [
            user_entry(), assistant_entry(input_tokens=26_850),
            *[tool_result_entry("x" * 32_000) for _ in range(7)],
            assistant_entry(input_tokens=102_132), tool_result_entry("x" * 32_000)])
        self.assertEqual(self.run_hook("precompact")[0], 0)
        override = self.state().ceiling_override
        self.assertEqual(round(override.pct), 56)  # 102.1k reported + ~10.7k of written results
        self.assertEqual(round(override.growth_pct), 38)  # the last turn's 75.3k

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

    def test_1m_model_sets_window_when_unknown(self):
        self.run_hook("session_start", {"source": "startup", "model": "claude-opus-5-5[1m]"})
        self.assertEqual(self.state().window, 1_000_000)

    def test_standard_model_leaves_window_unset(self):
        for model in ("claude-opus-5-5", None, 42):
            with self.subTest(model=model):
                self.run_hook("session_start", {"source": "startup", "model": model})
                self.assertIsNone(self.state().window)

    def test_1m_model_does_not_overwrite_known_window(self):
        save(SESSION, State(window=200_000), self.env)
        self.run_hook("session_start", {"source": "startup", "model": "claude-opus-5-5[1m]"})
        self.assertEqual(self.state().window, 200_000)

    def test_inactive_does_not_set_window(self):
        inactive = dict(self.env)
        del inactive["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
        self.run_hook("session_start", {"source": "startup", "model": "claude-opus-5-5[1m]"}, env=inactive)
        self.assertFalse(state_path(SESSION, self.env).exists())

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
        env = self.subprocess_env()
        return subprocess.run([sys.executable, str(REPO_ROOT / "hooks" / "run.py"), event], input=stdin,
                              env=env, capture_output=True, text=True, timeout=30).returncode

    def test_utf8_payload_is_read_whatever_the_stdio_encoding(self):
        self.hold()
        self.write_usage(410_000)
        body = json.dumps({"session_id": SESSION, "transcript_path": str(self.transcript_path()),
                           "cwd": "/tmp/\U0001f50d"}, ensure_ascii=False)
        for encoding in (None, "cp1252"):
            with self.subTest(PYTHONIOENCODING=encoding):
                env = self.subprocess_env()
                env.pop("PYTHONIOENCODING", None)
                if encoding:
                    env["PYTHONIOENCODING"] = encoding
                result = subprocess.run([sys.executable, str(REPO_ROOT / "hooks" / "run.py"), "precompact"],
                                        input=body.encode("utf-8"), env=env, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr.decode("utf-8", "replace"))

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
        config = json.loads((REPO_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        targets, matchers = set(), {}
        for event_name, groups in config["hooks"].items():
            for group in groups:
                matchers[event_name] = group.get("matcher")
                for hook in group["hooks"]:
                    self.assertIn("${CLAUDE_PLUGIN_ROOT}/hooks/run.py", hook["command"])
                    targets.add(hook["command"].rsplit(" ", 1)[-1])
        self.assertEqual(targets, set(HANDLERS))
        self.assertEqual(matchers["PreCompact"], "auto")
        self.assertIsNone(matchers["PostToolUse"])  # nudges on every tool, not only Bash


if __name__ == "__main__":
    unittest.main()
