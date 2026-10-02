from __future__ import annotations

import unittest

from compactor.config import Settings
from compactor.usage import (
    LARGE_WINDOW, Usage, find_transcript, guess_window, read_last_usage, read_model_id, read_usage,
    usage_from_statusline, window_for_model,
)
from tests.helpers import (
    SESSION, TempEnvTestCase, assistant_entry, identity_entry, tool_result_entry, user_entry,
    write_transcript,
)

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

    def test_include_sidechain_reads_subagent_transcripts(self):
        path = write_transcript(self.tmp / "t.jsonl", [
            assistant_entry(input_tokens=500), assistant_entry(input_tokens=9, sidechain=True),
        ])
        self.assertEqual(read_last_usage(path, include_sidechain=True), 9)

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

    def test_unreadable_path_is_none(self):
        self.assertIsNone(read_last_usage(self.tmp / "missing.jsonl"))
        self.assertIsNone(read_last_usage(self.tmp))  # a directory


class ReadUsageTest(TempEnvTestCase):
    def test_missing_or_empty_path_is_none(self):
        s = Settings(threshold=350_000)
        self.assertIsNone(read_usage(None, s))
        self.assertIsNone(read_usage("", s))
        self.assertIsNone(read_usage(str(self.tmp / "nope.jsonl"), s))

    def test_returns_usage_with_resolved_window(self):
        path = self.write_usage(410_000)
        usage = read_usage(str(path), Settings(threshold=350_000))
        self.assertEqual(usage, Usage(410_000, W, window_known=False))
        self.assertAlmostEqual(usage.pct, 41.0)

    def test_counts_tool_results_since_the_last_reply(self):
        # The last reply's usage predates the tool results that followed it; ~3 chars per token.
        path = write_transcript(self.transcript_path(), [
            user_entry(), assistant_entry(input_tokens=100_000),
            tool_result_entry("x" * 15_000), tool_result_entry("y" * 15_000),
            tool_result_entry("z" * 99_000, sidechain=True),  # a subagent's, not in this context
        ])
        usage = read_usage(str(path), Settings(context_window=W))
        self.assertGreaterEqual(usage.used, 110_000)
        self.assertLess(usage.used, 111_000)
        self.assertEqual(usage.growth, usage.used - 100_000)

    def test_growth_is_the_larger_of_pending_and_the_last_turn(self):
        path = write_transcript(self.transcript_path(), [
            user_entry(), assistant_entry(input_tokens=30_000), assistant_entry(input_tokens=30_000),
            assistant_entry(input_tokens=100_000), assistant_entry(input_tokens=100_000),
            tool_result_entry("x" * 3_000)])
        usage = read_usage(str(path), Settings(context_window=W))
        self.assertEqual(usage.growth, 70_000)
        self.assertTrue(101_000 <= usage.used < 101_100)  # 3k chars of results + JSON framing

    def test_a_drop_from_compaction_is_not_growth(self):
        path = write_transcript(self.transcript_path(), [
            assistant_entry(input_tokens=180_000), assistant_entry(input_tokens=40_000)])
        self.assertEqual(read_usage(str(path), Settings(context_window=W)), Usage(40_000, W))

    def test_entries_before_the_last_reply_are_not_pending(self):
        path = write_transcript(self.transcript_path(), [
            tool_result_entry("x" * 30_000), assistant_entry(input_tokens=100_000)])
        self.assertEqual(read_usage(str(path), Settings(context_window=W)), Usage(100_000, W))

    def test_explicit_setting_beats_cache_and_identity(self):
        path = self.write_usage(10, model_id="claude-opus-5-5[1m]")
        usage = read_usage(str(path), Settings(context_window=500_000), cached_window=200_000)
        self.assertEqual(usage, Usage(10, 500_000, window_known=True))

    def test_cached_window_beats_identity(self):
        path = self.write_usage(10, model_id="claude-opus-5-5[1m]")
        usage = read_usage(str(path), Settings(), cached_window=200_000)
        self.assertEqual(usage, Usage(10, 200_000, window_known=True))

    def test_identity_gives_1m_with_threshold_100k(self):
        path = self.write_usage(145_000, model_id="claude-opus-5-5[1m]")
        usage = read_usage(str(path), Settings(threshold=100_000))
        self.assertEqual(usage, Usage(145_000, 1_000_000, window_known=True))

    def test_no_identity_falls_back_to_unknown_heuristic(self):
        path = self.write_usage(10, model_id="claude-opus-5-5")
        usage = read_usage(str(path), Settings(threshold=100_000))
        self.assertEqual(usage, Usage(10, 200_000, window_known=False))


class ReadModelIdTest(TempEnvTestCase):
    def test_finds_id_after_other_entries(self):
        path = write_transcript(self.tmp / "t.jsonl", [
            user_entry(), identity_entry("claude-opus-5-5[1m]"), assistant_entry(input_tokens=1),
        ])
        self.assertEqual(read_model_id(path), "claude-opus-5-5[1m]")

    def test_absent_is_none(self):
        path = write_transcript(self.tmp / "t.jsonl", [user_entry(), assistant_entry(input_tokens=1)])
        self.assertIsNone(read_model_id(path))

    def test_missing_file_is_none(self):
        self.assertIsNone(read_model_id(self.tmp / "missing.jsonl"))

    def test_malformed_entries_are_none(self):
        for bad in (
            {"type": "attachment", "attachment": "not-a-dict"},
            {"type": "attachment", "attachment": {}},
            {"type": "attachment", "attachment": {"identity": {"modelId": 5}}},
            {"type": "attachment", "attachment": {"identity": "not-a-dict"}},
        ):
            with self.subTest(bad=bad):
                path = write_transcript(self.tmp / "t.jsonl", [bad])
                self.assertIsNone(read_model_id(path))

    def test_latest_identity_wins(self):
        # Seen live: a resumed 78 MB session started on one model and switched; Claude Code writes
        # a fresh identity entry after every compaction.
        path = write_transcript(self.tmp / "t.jsonl", [
            identity_entry("claude-fable-5-1"), user_entry("x" * 100_000),
            identity_entry("claude-opus-5-5[1m]"), assistant_entry(input_tokens=1)])
        self.assertEqual(read_model_id(path), "claude-opus-5-5[1m]")

    def test_identity_deep_into_a_long_transcript_is_found(self):
        # The old reader only looked at the first 64 KB; this session's first identity was 12 MB in.
        path = write_transcript(self.tmp / "t.jsonl", [
            *[user_entry("x" * 10_000) for _ in range(20)],
            identity_entry("claude-opus-5-5[1m]"), user_entry("y" * 10_000)])
        self.assertEqual(read_model_id(path), "claude-opus-5-5[1m]")

    def test_falls_back_to_the_start_when_the_tail_has_none(self):
        path = write_transcript(self.tmp / "t.jsonl", [
            identity_entry("claude-opus-5-5[1m]"), *[user_entry("x" * 200) for _ in range(50)]])
        self.assertEqual(read_model_id(path, tail_bytes=256), "claude-opus-5-5[1m]")

    def test_beyond_both_scans_is_none(self):
        entries = [user_entry("x" * 200) for _ in range(50)] + [identity_entry("claude-opus-5-5[1m]")]
        entries += [user_entry("y" * 200) for _ in range(50)]
        path = write_transcript(self.tmp / "t.jsonl", entries)
        self.assertIsNone(read_model_id(path, head_bytes=256, tail_bytes=256))


class WindowForModelTest(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(window_for_model("claude-opus-5-5[1m]"), LARGE_WINDOW)
        self.assertIsNone(window_for_model("claude-opus-5-5"))
        self.assertIsNone(window_for_model(None))


class GuessWindowTest(unittest.TestCase):
    def test_heuristic(self):
        self.assertEqual(guess_window(Settings(threshold=350_000), 10), 200_000)
        self.assertEqual(guess_window(Settings(threshold=350_000), 200_001), 1_000_000)
        self.assertEqual(guess_window(Settings(threshold=150_000), 250_000), 1_000_000)
        self.assertEqual(guess_window(Settings(threshold=150_000), 10), 200_000)
        self.assertEqual(guess_window(Settings(), 10), 200_000)


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
