from __future__ import annotations

import io
import json
import unittest
from unittest import mock
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from compactor import messages, subagents
from compactor.hooks import dispatch
from compactor.state import CeilingOverride, Hold, Note, State, save, state_path
from tests.helpers import (
    FIXTURES, NOW, SESSION, active_ids, assistant_entry, compact_boundary_entry, iso_minutes_ago, real_meta, user_entry, write_subagent,
    write_transcript,
)
from tests.hook_helpers import HookTestCase


def start_payload(agent_id: str = "a1", agent_type: str = "general-purpose"):
    return {"agent_id": agent_id, "agent_type": agent_type, "hook_event_name": "SubagentStart"}


def stop_payload(agent_id: str = "a1", agent_type: str = "general-purpose"):
    return {"agent_id": agent_id, "agent_type": agent_type, "hook_event_name": "SubagentStop",
            "stop_hook_active": False, "last_assistant_message": "done"}


class SubagentTrackingTest(HookTestCase):
    def active(self):
        return active_ids(SESSION, NOW, self.env)

    def test_start_and_stop_track_real_subagents(self):
        self.assertEqual(self.run_hook("subagent_start", start_payload("a1")), (0, "", ""))
        self.run_hook("subagent_start", start_payload("a2"))
        self.assertEqual(self.active(), ["a1", "a2"])
        self.assertEqual(self.run_hook("subagent_stop", stop_payload("a1")), (0, "", ""))
        self.assertEqual(self.active(), ["a2"])

    def test_empty_agent_type_is_ignored(self):
        self.run_hook("subagent_start", start_payload("c1", agent_type=""))
        self.assertEqual(self.active(), [])
        self.run_hook("subagent_start", start_payload("a1"))
        self.run_hook("subagent_stop", stop_payload("c2", agent_type=""))  # a compaction-summary agent
        self.assertEqual(self.active(), ["a1"])

    def test_tracking_never_touches_the_state_file(self):
        self.run_hook("subagent_start", start_payload("a1"))
        self.run_hook("subagent_stop", stop_payload("a1"))
        self.assertFalse(state_path(SESSION, self.env).exists())

    def test_agent_id_payloads_still_skipped_by_other_hooks(self):
        self.hold()
        self.write_usage(410_000)
        for event in ("precompact", "post_tool_use", "stop", "session_start"):
            with self.subTest(event=event):
                self.assertEqual(self.run_hook(event, {"agent_id": "a1"}), (0, "", ""))

    def test_disabled_or_inactive_does_not_track(self):
        self.run_hook("subagent_start", start_payload("a1"), env=dict(self.env, COMPACTOR_DISABLE="1"))
        inactive = dict(self.env)
        del inactive["CLAUDE_CODE_AUTO_COMPACT_WINDOW"]
        self.run_hook("subagent_start", start_payload("a2"), env=inactive)
        self.assertEqual(self.active(), [])

    def test_garbage_fails_open(self):
        for payload in ({"agent_id": ["x"], "agent_type": "general-purpose"},
                        {"agent_id": "../../x", "agent_type": "general-purpose"},
                        {"agent_id": "a1", "agent_type": 5}):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_hook("subagent_start", payload), (0, "", ""))
        self.assertEqual(self.run_hook("subagent_start", raw="{nope")[0], 0)
        self.assertEqual(self.active(), [])

    def test_session_end_clears_markers(self):
        self.run_hook("subagent_start", start_payload("a1"))
        self.run_hook("session_end", {"reason": "other"})
        self.assertEqual(self.active(), [])

    def test_main_stop_leaves_subagent_markers_alone(self):
        # Markers end at SubagentStop, idle expiry, or an error answer to the Agent call.
        main = write_transcript(self.transcript_path(), [user_entry()])
        write_subagent(main, "fg", used=1, meta=real_meta())
        write_subagent(main, "bg", used=1, meta=real_meta(requestShape="background"))
        self.run_hook("subagent_start", start_payload("fg"))
        self.run_hook("subagent_start", start_payload("bg"))
        self.assertEqual(self.run_hook("stop", {"stop_hook_active": False}), (0, "", ""))
        self.assertEqual(self.active(), ["bg", "fg"])


if __name__ == "__main__":
    unittest.main()


class SubagentGateTest(HookTestCase):
    """PreCompact with a real session layout: main .jsonl + <session>/subagents/agent-X.jsonl."""

    def setUp(self) -> None:
        super().setUp()
        self.main = self.write_usage(400_000, model_id="claude-opus-5-5[1m]")

    def start(self, agent_id: str = "a1", used: int = 150_000, **meta_fields):
        write_subagent(self.main, agent_id, used=used, model_id="claude-opus-5-5", meta=real_meta(**meta_fields))
        self.run_hook("subagent_start", start_payload(agent_id))

    def test_hold_mode_blocks_a_subagent_compaction_with_its_own_message(self):
        self.start()
        code, out, err = self.run_hook("precompact", {"trigger": "auto"})
        self.assertEqual((code, out), (2, ""))
        self.assertIn("subagent", err)
        self.assertIn("until the subagent finishes or the compacting agent reaches its safety ceiling", err)
        self.assertIn("150k of 200k", err)

    def test_main_hold_does_not_hide_a_subagent_at_its_ceiling(self):
        self.hold()
        self.start(used=180_000)
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"})[0], 0)
        state = self.state()
        self.assertIsNotNone(state.hold)  # the main agent still holds
        self.assertIsNone(state.ceiling_override)

    def test_main_at_its_ceiling_overrides_the_main_hold(self):
        # A background subagent: the compaction may be the main agent's (with only foreground
        # subagents it is theirs, and the hold waits; see FinishedForegroundAgentTest).
        self.hold()
        self.write_usage(900_000, model_id="claude-opus-5-5[1m]")
        self.start(requestShape="background")
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"})[0], 0)
        state = self.state()
        self.assertIsNone(state.hold)
        self.assertEqual(state.ceiling_override.pct, 90.0)

    def test_allow_mode_lets_a_foreground_subagent_compact_despite_main_hold(self):
        self.hold()
        self.start()
        env = dict(self.env, COMPACTOR_SUBAGENTS="allow")
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}, env=env), (0, "", ""))
        self.assertIsNotNone(self.state().hold)

    def test_allow_mode_with_background_subagent_and_main_hold_blocks(self):
        self.hold()
        self.start(requestShape="background")
        env = dict(self.env, COMPACTOR_SUBAGENTS="allow")
        code, _, err = self.run_hook("precompact", {"trigger": "auto"}, env=env)
        self.assertEqual(code, 2)
        self.assertIn("refactor", err)

    def test_compaction_summary_agent_does_not_count(self):
        write_subagent(self.main, "c1", used=150_000, meta=real_meta(agentType=""))
        self.run_hook("subagent_start", start_payload("c1", agent_type=""))
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}), (0, "", ""))

    def test_after_subagent_stop_main_rules_apply_again(self):
        self.start()
        self.run_hook("subagent_stop", stop_payload("a1"))
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}), (0, "", ""))

    def test_unreadable_background_subagent_is_not_held_past_the_max_hold_age(self):
        # Main usage is readable and low; the background subagent's transcript isn't. The
        # compaction may be the subagent's (possibly near its hard limit): the main hold's age
        # fallback lets it through, but without clearing the hold, which may not be why it ran.
        self.hold(minutes_ago=120)
        self.start(requestShape="background")
        (self.main.with_suffix("") / "subagents" / "agent-a1.jsonl").write_bytes(b"\xff\xfe garbage\n{not json")
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}), (0, "", ""))
        state = self.state()
        self.assertEqual(state.hold.reason, "refactor")
        self.assertIsNone(state.ceiling_override)

    def test_corrupt_subagent_files_fail_open(self):
        self.start()
        sub_dir = self.main.with_suffix("") / "subagents"
        (sub_dir / "agent-a1.jsonl").write_bytes(b"\xff\xfe garbage\n{not json")
        (sub_dir / "agent-a1.meta.json").write_text("[1, 2")
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}), (0, "", ""))


def stamp(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SessionStartWithSubagentTest(HookTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.main = self.write_usage(400_000)
        save(SESSION, State(hold=Hold("refactor", iso_minutes_ago(5)), note=Note("main note", iso_minutes_ago(3)),
                            ceiling_override=CeilingOverride(iso_minutes_ago(1), 90.1, "old hold")), self.env)

    def start(self, agent_id: str = "a1", boundary_s_ago: Optional[float] = None, **meta_fields):
        extra = [compact_boundary_entry(stamp(boundary_s_ago), sidechain=True)] if boundary_s_ago is not None else []
        write_subagent(self.main, agent_id, used=60_000, meta=real_meta(**meta_fields), extra=extra)
        self.run_hook("subagent_start", start_payload(agent_id))

    def assert_suppressed(self):
        self.assertEqual(self.run_hook("session_start", {"source": "compact"}), (0, "", ""))
        self.assertIsNotNone(self.state().ceiling_override)  # still pending for the main agent

    def assert_main_injection(self):
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        text = self.context_of(out)
        self.assertIn("main note", text)
        self.assertIn("A compaction hold is active", text)
        self.assertIsNone(self.state().ceiling_override)

    def test_foreground_subagent_compaction_injects_nothing(self):
        self.start(boundary_s_ago=0.3)
        self.assert_suppressed()

    def test_subagent_compaction_does_not_cache_its_window_for_the_main_agent(self):
        self.start(boundary_s_ago=0.3)
        self.run_hook("session_start", {"source": "compact", "model": "claude-opus-5-5[1m]"})
        self.assertIsNone(self.state().window)

    def test_compaction_with_a_background_subagent_running_does_not_cache_its_window(self):
        # SessionStart(compact)'s `model` is the compacting agent's (live check: "claude-sonnet-5-5"
        # from a sonnet subagent under an opus main agent), and this compaction may be the subagent's.
        self.start("b1", requestShape="background")
        self.run_hook("session_start", {"source": "compact", "model": "claude-sonnet-5-5[1m]"})
        self.assertIsNone(self.state().window)

    def test_main_compaction_still_caches_the_1m_window(self):
        self.run_hook("session_start", {"source": "compact", "model": "claude-opus-5-5[1m]"})
        self.assertEqual(self.state().window, 1_000_000)

    def test_foreground_subagent_needs_no_boundary_on_disk(self):
        # Claude Code writes the compacting agent's boundary only after SessionStart returns.
        self.start()
        self.assert_suppressed()

    def test_demoted_foreground_subagent_gets_the_main_note(self):
        self.start(boundary_s_ago=0.3)
        self.run_hook("post_tool_use", {"tool_name": "Read"})  # the main agent acted: not waiting
        self.assert_main_injection()

    def test_background_subagent_compaction_cannot_be_told_apart(self):
        # Known limitation: with a background subagent running it may be either agent's
        # compaction, so the main agent's note is injected rather than lost.
        self.start(boundary_s_ago=0.3, requestShape="background")
        self.assert_main_injection()

    def test_injection_while_a_subagent_runs_tells_a_subagent_to_ignore_it(self):
        self.start("b1", requestShape="background")
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        text = self.context_of(out)
        self.assertTrue(text.startswith(messages.SUBAGENT_IGNORE + "\n"), text)
        self.assertIn("main note", text)

    def test_injection_with_no_subagent_running_has_no_subagent_line(self):
        for source in ("compact", "startup", "resume"):
            with self.subTest(source=source):
                _, out, _ = self.run_hook("session_start", {"source": source})
                self.assertNotIn("If you are a subagent", self.context_of(out))

    def test_foreground_and_background_running_is_the_main_agents(self):
        self.start("f1")
        self.start("b1", requestShape="background")
        self.assert_main_injection()

    def test_main_compaction_while_a_background_subagent_runs_is_the_main_agents(self):
        for agent_id, boundary in (("b1", None), ("b2", 600)):
            with self.subTest(boundary=boundary):
                self.start(agent_id, boundary_s_ago=boundary, requestShape="background")
                self.assert_main_injection()
                self.run_hook("subagent_stop", stop_payload(agent_id))
                save(SESSION, State(hold=Hold("refactor", iso_minutes_ago(5)),
                                    note=Note("main note", iso_minutes_ago(3)),
                                    ceiling_override=CeilingOverride(iso_minutes_ago(1), 90.1, "old")), self.env)

    def test_main_compaction_soon_after_a_background_subagents_is_the_main_agents(self):
        # The subagent's previous compaction, 20 s ago, says nothing about this one.
        self.start("b1", boundary_s_ago=20, requestShape="background")
        self.assert_main_injection()

    def test_after_subagent_stop_the_main_note_returns(self):
        self.start(boundary_s_ago=0.3)
        self.run_hook("subagent_stop", stop_payload("a1"))
        self.assert_main_injection()

    def test_startup_and_resume_are_unchanged(self):
        self.start()
        _, out, _ = self.run_hook("session_start", {"source": "resume"})
        self.assertIn("main note", self.context_of(out))


def agent_call(tool_use_id: str = "toolu_1", used: int = 29_001) -> Dict[str, Any]:
    """The main agent's Agent tool_use, as Claude Code writes it."""
    return {"type": "assistant", "isSidechain": False,
            "message": {"role": "assistant", "model": "claude-opus-5-5",
                        "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}],
                        "usage": {"input_tokens": 1, "cache_read_input_tokens": used - 1,
                                  "cache_creation_input_tokens": 0, "output_tokens": 50}}}


def agent_result(tool_use_id: str = "toolu_1", is_error: bool = False) -> Dict[str, Any]:
    """Its tool_result. Live R2: "Agent terminated early due to an API error: ..." with is_error
    true, and no SubagentStop, no PostToolUse and no Stop for that subagent."""
    text = ("Agent terminated early due to an API error: Autocompact is thrashing"
            if is_error else "one summary line per file")
    return {"type": "user", "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "tool_result", "content": text,
                                                     "is_error": is_error, "tool_use_id": tool_use_id}]}}


class FinishedForegroundAgentTest(HookTestCase):
    """A foreground subagent whose Agent call already returned in the main transcript (its
    SubagentStop missed, e.g. an API error) isn't running: the next compaction is the main agent's."""

    def setUp(self) -> None:
        super().setUp()
        save(SESSION, State(hold=Hold("refactor", iso_minutes_ago(5)), note=Note("main note", iso_minutes_ago(4))),
             self.env)

    def launch(self, main_used: int = 30_000, sub_used: int = 50_000, **meta_fields: Any) -> None:
        self.entries = [user_entry(), agent_call(used=main_used)]
        self.main = write_transcript(self.transcript_path(), self.entries)
        write_subagent(self.main, "a1", used=sub_used, meta=real_meta(**meta_fields))
        self.run_hook("subagent_start", start_payload("a1"))
        self.main_used = main_used

    def agent_returns(self, is_error: bool) -> None:
        self.entries += [agent_result(is_error=is_error), assistant_entry(self.main_used)]
        write_transcript(self.main, self.entries)

    def test_failed_agent_then_main_compaction_gets_note_and_ceiling_notice(self):
        # The reviewer's probe: hold mode, the main agent at its ceiling right after the failed call.
        self.launch(main_used=990_000)
        self.agent_returns(is_error=True)
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"})[0], 0)
        self.assertIsNone(self.state().hold)
        self.assertIsNotNone(self.state().ceiling_override)
        _, out, _ = self.run_hook("session_start", {"source": "compact", "model": "claude-opus-5-5"})
        text = self.context_of(out) or ""
        self.assertIn("main note", text)
        self.assertIn("safety ceiling", text)
        self.assertIsNone(self.state().ceiling_override)  # consumed by this, the main agent's, compaction

    def test_live_foreground_subagent_compaction_keeps_the_main_hold_at_its_ceiling(self):
        self.launch(main_used=990_000)  # its Agent call hasn't returned: the main agent is waiting
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}), (0, "", ""))
        state = self.state()
        self.assertEqual(state.hold.reason, "refactor")
        self.assertIsNone(state.ceiling_override)

    def test_failed_agent_then_main_compaction_in_allow_mode(self):
        self.env["COMPACTOR_SUBAGENTS"] = "allow"
        self.launch()
        self.agent_returns(is_error=True)
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        self.assertIn("main note", self.context_of(out) or "")

    def test_main_compaction_right_after_an_agent_returns(self):
        self.launch()
        self.agent_returns(is_error=False)  # SubagentStop not seen (yet)
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        self.assertIn("main note", self.context_of(out) or "")
        # Not an error, so it may have been moved to the background: kept, but no longer foreground.
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1"])

    def background_it(self) -> None:
        """The user moves the running foreground call to the background: Claude Code answers the
        Agent tool_use at once (status async_launched), meta.json keeps requestShape "foreground",
        and the subagent keeps running."""
        self.entries += [{"type": "user", "isSidechain": False, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": "Async agent launched successfully. agentId: a1"}]}},
            assistant_entry(self.main_used)]
        write_transcript(self.main, self.entries)

    def test_backgrounded_subagent_over_its_ceiling_may_compact_despite_the_main_hold(self):
        self.launch(main_used=100_000, sub_used=195_000)
        self.background_it()
        self.run_hook("post_tool_use", {"tool_name": "Read"})  # the main agent acts again
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}), (0, "", ""))
        self.assertEqual(self.state().hold.reason, "refactor")

    def test_backgrounded_subagent_below_its_ceiling_is_held_as_a_background_one(self):
        self.launch(main_used=100_000, sub_used=150_000)
        self.background_it()
        code, _, err = self.run_hook("precompact", {"trigger": "auto"})
        self.assertEqual(code, 2)
        self.assertIn("subagent", err)
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1"])

    def test_backgrounded_subagent_compaction_gets_the_full_main_context(self):
        self.launch()
        self.background_it()
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        text = self.context_of(out) or ""
        self.assertIn("main note", text)
        self.assertIn("A compaction hold is active", text)

    def test_recreated_marker_of_a_backgrounded_call_stays_background(self):
        self.launch()
        self.background_it()
        subagents.mark_stopped(SESSION, "a1", self.env)  # e.g. expired while it ran one long step
        self.run_hook("post_tool_use", {"agent_id": "a1", "agent_type": "general-purpose", "tool_name": "Read"})
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1"])
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        self.assertIn("main note", self.context_of(out) or "")
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1"])

    def test_failed_agent_no_longer_holds_the_main_compaction_below_its_ceiling(self):
        self.launch(main_used=100_000)
        self.agent_returns(is_error=True)
        save(SESSION, State(), self.env)  # no hold: nothing should block
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}), (0, "", ""))

    def test_running_foreground_subagent_compaction_is_still_suppressed(self):
        self.launch()  # tool_use written, no tool_result yet
        self.assertEqual(self.run_hook("session_start", {"source": "compact"}), (0, "", ""))

    def test_demoted_marker_with_a_result_is_removed(self):
        self.launch()
        self.run_hook("post_tool_use", {"tool_name": "Read"})  # demote
        self.agent_returns(is_error=True)
        self.run_hook("precompact", {"trigger": "auto"})
        self.assertEqual(active_ids(SESSION, NOW, self.env), [])

    def test_background_launch_result_keeps_the_marker(self):
        # A background Agent call returns its tool_result ("launched") at once; it keeps running.
        self.launch(requestShape="background")
        self.agent_returns(is_error=False)
        self.run_hook("session_start", {"source": "compact"})
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1"])


class SessionStartDuringLiveSubagentCompactionTest(HookTestCase):
    """Replays what SessionStart(compact) saw in the live check (verification doc, "Live subagent
    gating check"): Claude Code stamps the subagent's compact_boundary ~0.2 s before this hook
    runs but only writes it to the transcript after every SessionStart hook has returned. The
    fixture is the (sanitized) subagent transcript exactly as it stood on disk at that moment."""

    FIXTURE = FIXTURES / "transcripts" / "subagent-mid-compaction.jsonl"

    def setUp(self) -> None:
        super().setUp()
        self.main = write_transcript(self.transcript_path(), [user_entry(), assistant_entry(30_000)])
        save(SESSION, State(hold=Hold("waiting on subagent review", "2026-09-30T16:37:11Z"),
                            note=Note("main note: R1 marker", "2026-09-30T16:37:13Z")), self.env)

    def at(self, event: str, payload: Dict[str, Any], iso: str):
        body: Dict[str, Any] = {"session_id": SESSION, "transcript_path": str(self.main)}
        body.update(payload)
        out, err = io.StringIO(), io.StringIO()
        now = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dispatch(event, stdin=io.StringIO(json.dumps(body)), out=out, err=err, env=self.env, now=now), out.getvalue()

    def subagent_on_disk(self, lines: int, **meta_fields: Any) -> None:
        directory = self.main.with_suffix("") / "subagents"
        directory.mkdir(parents=True, exist_ok=True)
        kept = self.FIXTURE.read_text(encoding="utf-8").splitlines(keepends=True)[:lines]
        (directory / "agent-a1.jsonl").write_text("".join(kept), encoding="utf-8")
        meta = real_meta(model="sonnet", requestShape="foreground", requestNonInteractive=True)
        meta.update(meta_fields)
        (directory / "agent-a1.meta.json").write_text(json.dumps(meta), encoding="utf-8")
        self.at("subagent_start", start_payload("a1"), "2026-09-30T16:37:16.423Z")

    # Under the current rule (every running subagent foreground, its Agent call unanswered) these
    # would pass with an empty subagent file too; the fixture pins down the on-disk state that
    # made the old compact_boundary rule fail, so no boundary-based rule returns.

    def test_first_subagent_compaction_injects_nothing(self):
        self.subagent_on_disk(75)  # up to the last entry before its first boundary (16:38:08.046Z)
        code, out = self.at("session_start", {"source": "compact", "model": "claude-sonnet-5-5"},
                            "2026-09-30T16:38:08.208Z")
        self.assertEqual((code, out), (0, ""))

    def test_later_subagent_compaction_injects_nothing(self):
        self.subagent_on_disk(112)  # its previous boundary (16:38:08.046Z) is 34 s old; the new one isn't written yet
        code, out = self.at("session_start", {"source": "compact", "model": "claude-sonnet-5-5"},
                            "2026-09-30T16:38:42.213Z")
        self.assertEqual((code, out), (0, ""))


class SessionStartUnwritableStateTest(HookTestCase):
    def test_window_save_failure_does_not_suppress_output(self):
        blocker = self.tmp / "state" / "claude-compactor"
        blocker.parent.mkdir(parents=True)
        blocker.write_text("a file where the state dir should be")
        code, out, _ = self.run_hook("session_start", {"source": "startup", "model": "claude-opus-5-5[1m]"})
        self.assertEqual(code, 0)
        self.assertIn('compactor hold "<why>"', self.context_of(out))

    def test_ceiling_notice_save_failure_does_not_suppress_output(self):
        save(SESSION, State(note=Note("main note", iso_minutes_ago(3)),
                            ceiling_override=CeilingOverride(iso_minutes_ago(1), 90.1, "old hold")), self.env)
        with mock.patch("compactor.hooks._common.save", side_effect=OSError("read-only")):
            code, out, _ = self.run_hook("session_start", {"source": "compact"})
        self.assertEqual(code, 0)
        text = self.context_of(out) or ""
        self.assertIn("was overridden", text)
        self.assertIn("main note", text)


class SubagentLivenessTest(HookTestCase):
    """R24: markers stay alive only while the subagent keeps making tool calls, and a main-agent
    tool call or prompt shows the main agent isn't waiting on any of them."""

    def setUp(self) -> None:
        super().setUp()
        self.main = self.write_usage(400_000, model_id="claude-opus-5-5[1m]")
        write_subagent(self.main, "a1", used=150_000, model_id="claude-opus-5-5", meta=real_meta())
        self.hold(note=Note("main note", iso_minutes_ago(3)))

    def marker_started(self, minutes_ago: float) -> None:
        subagents.mark_started(SESSION, "a1", NOW - timedelta(minutes=minutes_ago), self.env)

    def test_subagent_tool_call_refreshes_its_marker_and_does_nothing_else(self):
        self.marker_started(14)
        before = self.state()
        tool = {"agent_id": "a1", "agent_type": "general-purpose", "tool_name": "Bash",
                "tool_input": {"command": "git commit -m x"}, "tool_response": {"exit_code": 0}}
        self.assertEqual(self.run_hook("post_tool_use", tool), (0, "", ""))
        self.assertEqual(self.state(), before)  # no nudge bookkeeping for a subagent's call
        later = NOW + timedelta(minutes=subagents.IDLE_MIN - 1)
        self.assertEqual(active_ids(SESSION, later, self.env), ["a1"])

    def test_subagent_tool_call_recreates_an_expired_marker(self):
        self.run_hook("post_tool_use", {"agent_id": "a1", "agent_type": "general-purpose", "tool_name": "Read"})
        self.assertEqual(subagents.read_markers(SESSION, NOW, self.env), {"a1": True})  # recreated as background
        self.run_hook("post_tool_use", {"agent_id": "c9", "agent_type": "", "tool_name": "Read"})
        self.assertEqual(active_ids(SESSION, NOW, self.env), ["a1"])

    def test_main_tool_call_and_prompt_demote_foreground_markers(self):
        for event in ("post_tool_use", "user_prompt_submit"):
            with self.subTest(event=event):
                self.marker_started(1)
                self.run_hook(event, {"tool_name": "Read"} if event == "post_tool_use" else {"prompt": "hi"})
                self.assertEqual(subagents.read_markers(SESSION, NOW, self.env), {"a1": True})

    def test_stale_foreground_marker_does_not_override_the_main_agent(self):
        self.marker_started(subagents.IDLE_MIN + 1)  # its SubagentStop was missed
        code, _, err = self.run_hook("precompact", {"trigger": "auto"})
        self.assertEqual(code, 2)
        self.assertIn("refactor", err)
        self.assertNotIn("subagent", err)
        env = dict(self.env, COMPACTOR_SUBAGENTS="allow")
        self.assertEqual(self.run_hook("precompact", {"trigger": "auto"}, env=env)[0], 2)
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        self.assertIn("main note", self.context_of(out))

    def test_demoted_marker_respects_the_main_hold_in_allow_mode(self):
        self.marker_started(1)
        self.run_hook("post_tool_use", {"tool_name": "Read"})  # main agent acts: not waiting
        env = dict(self.env, COMPACTOR_SUBAGENTS="allow")
        code, _, err = self.run_hook("precompact", {"trigger": "auto"}, env=env)
        self.assertEqual(code, 2)
        self.assertIn("refactor", err)
        _, out, _ = self.run_hook("session_start", {"source": "compact"})
        self.assertIn("main note", self.context_of(out))
