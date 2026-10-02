from __future__ import annotations

import json
import os
import unittest
from datetime import timedelta

from compactor import subagents
from compactor.subagents import ActiveSubagent
from compactor.usage import Usage
from tests.helpers import (
    NOW, SESSION, TempEnvTestCase, active_ids, real_meta, write_subagent,
    write_transcript, user_entry,
)


class MarkerTest(TempEnvTestCase):
    def test_start_then_stop(self):
        subagents.mark_started(SESSION, "a1", NOW, self.env)
        subagents.mark_started(SESSION, "a2", NOW, self.env)
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1", "a2"])
        subagents.mark_stopped(SESSION, "a1", self.env)
        subagents.mark_stopped(SESSION, "never-started", self.env)
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a2"])

    def test_sessions_are_isolated(self):
        subagents.mark_started(SESSION, "a1", NOW, self.env)
        self.assertEqual(active_ids("other", NOW, self.env), [])

    def test_clear_removes_everything(self):
        subagents.mark_started(SESSION, "a1", NOW, self.env)
        subagents.clear(SESSION, self.env)
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])
        subagents.clear(SESSION, self.env)  # nothing left: still fine

    def test_idle_and_garbage_markers_are_pruned(self):
        subagents.mark_started(SESSION, "old", NOW - timedelta(minutes=subagents.IDLE_MIN + 1), self.env)
        subagents.mark_started(SESSION, "fresh", NOW - timedelta(minutes=subagents.IDLE_MIN - 1), self.env)
        subagents.mark_started(SESSION, "junk", NOW, self.env)
        (subagents.markers_dir(SESSION, self.env) / "junk").write_text("not json")
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["fresh"])
        self.assertFalse((subagents.markers_dir(SESSION, self.env) / "old").exists())

    def test_touch_keeps_a_running_subagent_alive(self):
        subagents.mark_started(SESSION, "a1", NOW - timedelta(minutes=14), self.env)
        subagents.touch(SESSION, "a1", NOW, create=False, env=self.env)
        later = NOW + timedelta(minutes=subagents.IDLE_MIN - 1)
        self.assertEqual(active_ids(SESSION, later, self.env), ["a1"])
        self.assertEqual(active_ids(SESSION, later + timedelta(minutes=2), self.env), [])

    def test_touch_recreates_an_expired_marker_only_when_asked(self):
        subagents.touch(SESSION, "a1", NOW, create=False, env=self.env)
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])
        subagents.touch(SESSION, "a1", NOW, create=True, env=self.env)
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1"])

    def test_recreated_marker_is_background(self):
        # Its marker expired mid-run, so the main agent can't still be waiting on it.
        subagents.touch(SESSION, "a1", NOW, create=True, env=self.env)
        self.assertEqual(subagents.read_markers(SESSION, NOW, self.env), {"a1": True})

    def test_demote_all_marks_background_and_keeps_liveness_clock(self):
        subagents.mark_started(SESSION, "a1", NOW - timedelta(minutes=10), self.env)
        marker = subagents.markers_dir(SESSION, self.env) / "a1"
        mtime = marker.stat().st_mtime
        subagents.demote_all(SESSION, self.env)
        self.assertEqual(subagents.read_markers(SESSION, NOW, self.env), {"a1": True})
        self.assertEqual(marker.stat().st_mtime, mtime)
        subagents.demote_all("no-markers-here", self.env)  # nothing to do: fine

    def test_marker_writes_are_atomic(self):
        subagents.mark_started(SESSION, "a1", NOW, self.env)
        subagents.demote_all(SESSION, self.env)
        names = [p.name for p in subagents.markers_dir(SESSION, self.env).iterdir()]
        self.assertEqual(names, ["a1"])  # no temp files left behind
        self.assertEqual(json.loads((subagents.markers_dir(SESSION, self.env) / "a1").read_text(encoding="utf-8")),
                         {"demoted": True})

    def test_empty_marker_dirs_are_pruned(self):
        subagents.mark_started(SESSION, "a1", NOW, self.env)
        subagents.mark_stopped(SESSION, "a1", self.env)
        subagents.mark_started("dead-session", "x", NOW - timedelta(minutes=subagents.IDLE_MIN + 1), self.env)
        subagents.mark_started("live-session", "y", NOW, self.env)
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])
        root = subagents.markers_root(self.env)
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["live-session"])

    def old_file(self, path, name="x"):
        path.mkdir(parents=True, exist_ok=True)
        target = path / name
        target.write_text('{"demoted": false}')
        stamp = (NOW - timedelta(minutes=subagents.IDLE_MIN + 5)).timestamp()
        os.utime(str(target), (stamp, stamp))
        return target

    def test_pruning_never_follows_symlinks_or_touches_foreign_files(self):
        outside = self.tmp / "outside"
        victims = [self.old_file(outside, "x"), self.old_file(outside, ".tmp-1")]
        root = subagents.markers_root(self.env)
        root.mkdir(parents=True)
        os.symlink(str(outside), str(root / "linked-session"))  # a symlinked session dir
        other = root / "other-session"
        victims.append(self.old_file(other, "notes.txt"))  # not a marker name
        os.symlink(str(victims[0]), str(other / "y"))  # a symlinked marker
        victims.append(self.old_file(root / "not.a.session", "z"))  # not a session-id name
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])
        for victim in victims:
            with self.subTest(victim=victim):
                self.assertTrue(victim.exists())
        self.assertTrue((root / "linked-session").is_symlink())
        self.assertTrue((other / "y").is_symlink())

    def test_pruning_still_expires_other_sessions_markers_and_temp_files(self):
        other = subagents.markers_root(self.env) / "other-session"
        self.old_file(other, "a1")
        self.old_file(other, ".tmp-abc")
        active_ids(SESSION, NOW, self.env)
        self.assertFalse(other.exists())

    def test_own_symlinked_marker_dir_is_not_pruned_through(self):
        outside = self.tmp / "outside"
        victim = self.old_file(outside, "a1")
        stray = self.old_file(outside, "notes.txt")
        root = subagents.markers_root(self.env)
        root.mkdir(parents=True)
        os.symlink(str(outside), str(subagents.markers_dir(SESSION, self.env)))
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])
        subagents.mark_stopped(SESSION, "a1", self.env)
        subagents.mark_started(SESSION, "a2", NOW, self.env)
        subagents.touch(SESSION, "a1", NOW, create=True, env=self.env)
        subagents.demote_all(SESSION, self.env)
        subagents.clear(SESSION, self.env)
        self.assertTrue(victim.exists())
        self.assertTrue(stray.exists())
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ["a1", "notes.txt"])
        self.assertEqual(json.loads(victim.read_text(encoding="utf-8")), {"demoted": False})

    def test_unsafe_agent_ids_are_ignored(self):
        for agent_id in ("../escape", "", "a/b", None, 7):
            with self.subTest(agent_id=agent_id):
                subagents.mark_started(SESSION, agent_id, NOW, self.env)  # type: ignore[arg-type]
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])
        self.assertFalse((self.tmp / "state" / "escape").exists())

    def test_unwritable_state_dir_never_raises(self):
        blocker = self.tmp / "state"
        blocker.write_text("a file where the state dir should be")
        subagents.mark_started(SESSION, "a1", NOW, self.env)
        subagents.mark_stopped(SESSION, "a1", self.env)
        subagents.clear(SESSION, self.env)
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])


class LocateTest(TempEnvTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.main = write_transcript(self.transcript_path(), [user_entry()])

    def test_subagent_dir_from_transcript_path(self):
        self.assertEqual(subagents.subagents_dir(str(self.main)), self.main.with_suffix("") / "subagents")
        for bad in (None, "", 42, "/x/not-a-transcript.txt"):
            with self.subTest(bad=bad):
                self.assertIsNone(subagents.subagents_dir(bad))  # type: ignore[arg-type]

    def test_measure_reads_sidechain_usage_and_1m_identity(self):
        write_subagent(self.main, "a1", used=150_000, model_id="claude-opus-5-5[1m]", meta=real_meta())
        (sub,) = subagents.measure(["a1"], str(self.main))
        self.assertEqual(sub, ActiveSubagent("a1", Usage(150_000, 1_000_000), foreground=True))

    def test_window_from_meta_model_when_identity_missing(self):
        write_subagent(self.main, "a1", used=150_000, meta=real_meta(model="claude-sonnet-5[1m]"))
        (sub,) = subagents.measure(["a1"], str(self.main))
        self.assertEqual(sub.usage, Usage(150_000, 1_000_000))

    def test_unknown_window_is_assumed_200k(self):
        write_subagent(self.main, "a1", used=150_000, model_id="claude-haiku-5", meta=real_meta(model="haiku"))
        (sub,) = subagents.measure(["a1"], str(self.main))
        self.assertEqual(sub.usage, Usage(150_000, 200_000, window_known=False))

    def test_empty_agent_type_is_not_a_real_subagent(self):
        write_subagent(self.main, "c1", used=50_000, meta=real_meta(agentType=""))
        self.assertEqual(subagents.measure(["c1"], str(self.main)), [])

    def test_missing_or_corrupt_meta_is_treated_conservatively(self):
        write_subagent(self.main, "a1", used=150_000)
        write_subagent(self.main, "a2", used=160_000)
        (self.main.with_suffix("") / "subagents" / "agent-a2.meta.json").write_text("{garbage")
        found = subagents.measure(["a1", "a2"], str(self.main))
        self.assertEqual(found, [ActiveSubagent("a1", Usage(150_000, 200_000, window_known=False), False),
                                 ActiveSubagent("a2", Usage(160_000, 200_000, window_known=False), False)])

    def test_missing_transcript_has_unknown_usage(self):
        found = subagents.measure(["gone"], str(self.main))
        self.assertEqual(found, [ActiveSubagent("gone", None, False)])

    def test_demoted_foreground_is_not_foreground(self):
        write_subagent(self.main, "a1", used=1, meta=real_meta())
        self.assertFalse(subagents.measure(["a1"], str(self.main), demoted={"a1"})[0].foreground)

    def test_background_is_not_foreground(self):
        write_subagent(self.main, "a1", used=1, meta=real_meta(requestShape="background"))
        self.assertFalse(subagents.measure(["a1"], str(self.main))[0].foreground)

    def test_no_transcript_path_measures_nothing_known(self):
        self.assertEqual(subagents.measure(["a1"], None), [ActiveSubagent("a1", None, False)])


class OwnsCompactionTest(unittest.TestCase):
    def test_every_running_subagent_foreground_owns(self):
        self.assertTrue(subagents.owns_compaction([ActiveSubagent("a1", None, True),
                                                   ActiveSubagent("a2", None, True)]))

    def test_any_background_or_none_running_does_not(self):
        self.assertFalse(subagents.owns_compaction([]))
        self.assertFalse(subagents.owns_compaction([ActiveSubagent("a1", None, True),
                                                    ActiveSubagent("a2", None, False)]))
        self.assertFalse(subagents.owns_compaction([ActiveSubagent("a1", None, False)]))


class AnsweredTest(TempEnvTestCase):
    def test_finds_only_tool_results_for_the_wanted_calls(self):
        path = write_transcript(self.tmp / "t.jsonl", [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_A"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_B"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_D",
                                                      "is_error": True}]}},
            {"type": "user", "message": {"content": "toolu_C tool_result mentioned in text"}},
        ], trailing="{not json toolu_A tool_result\n")
        self.assertEqual(subagents._answered(str(path), {"a": "toolu_A", "b": "toolu_B", "c": "toolu_C", "d": "toolu_D"}),
                         {"b": False, "d": True})

    def test_missing_file_or_nothing_wanted(self):
        self.assertEqual(subagents._answered(str(self.tmp / "missing.jsonl"), {"a": "toolu_A"}), {})
        self.assertEqual(subagents._answered(None, {"a": "toolu_A"}), {})
        self.assertEqual(subagents._answered(str(self.tmp / "missing.jsonl"), {}), {})


if __name__ == "__main__":
    unittest.main()
