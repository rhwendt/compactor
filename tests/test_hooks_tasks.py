from __future__ import annotations

import json
import unittest

from compactor.model import BackgroundTask
from compactor.state import Hold, State, save
from tests.helpers import NOW, SESSION, assistant_entry, iso_minutes_ago, user_entry, write_transcript
from tests.hook_helpers import HookTestCase

# Shapes captured live from Claude Code 2.1.287.
BG_START = {"tool_name": "Bash",
            "tool_input": {"command": "sleep 120; echo bg-two-done", "run_in_background": True,
                           "description": "Watchdog for the task 10 re-review"},
            "tool_response": {"stdout": "", "stderr": "", "interrupted": False, "isImage": False,
                              "noOutputExpected": False, "backgroundTaskId": "bbkwem7r3"}}
TASK_STOP = {"tool_name": "TaskStop", "tool_input": {"task_id": "bbkwem7r3"},
             "tool_response": {"message": "Successfully stopped task: bbkwem7r3 (sleep 120)",
                               "task_id": "bbkwem7r3", "task_type": "local_bash"}}


def notification(task_id: str, status: str = "completed"):
    return user_entry(f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>toolu_x</tool-use-id>\n"
                      f"<status>{status}</status>\n<summary>Background command done</summary>\n</task-notification>")


class TaskTrackingTest(HookTestCase):
    def test_background_bash_start_is_recorded_even_without_a_hold(self):
        self.write_usage(10_000)
        self.run_hook("post_tool_use", BG_START)
        self.assertEqual(self.state().tasks, [
            BackgroundTask("bbkwem7r3", "Watchdog for the task 10 re-review", iso_minutes_ago(0))])

    def test_task_stop_forgets_it(self):
        self.write_usage(10_000)
        self.run_hook("post_tool_use", BG_START)
        self.run_hook("post_tool_use", TASK_STOP)
        self.assertEqual(self.state().tasks, [])

    def test_teammate_tasks_are_not_tracked(self):
        self.write_usage(10_000)
        self.run_hook("post_tool_use", dict(BG_START, agent_id="aprobe-1", agent_type="probe"))
        self.assertEqual(self.state().tasks, [])

    def test_bad_ids_and_foreground_bash_are_ignored(self):
        self.write_usage(10_000)
        bad = json.loads(json.dumps(BG_START))
        bad["tool_response"]["backgroundTaskId"] = "../../etc"
        self.run_hook("post_tool_use", bad)
        self.run_hook("post_tool_use", {"tool_name": "Bash", "tool_input": {"command": "ls"},
                                        "tool_response": {"stdout": "x"}})
        self.assertEqual(self.state().tasks, [])

    def test_compaction_lists_running_tasks_and_drops_finished_ones(self):
        save(SESSION, State(tasks=[BackgroundTask("bbkwem7r3", "watchdog", iso_minutes_ago(12)),
                                   BackgroundTask("b9zxz36m5", "short job", iso_minutes_ago(30))]), self.env)
        write_transcript(self.transcript_path(), [user_entry(), notification("b9zxz36m5"),
                                                  assistant_entry(input_tokens=50_000)])
        text = self.context_of(self.run_hook("session_start", {"source": "compact"})[1])
        self.assertIn("bbkwem7r3 (12m): watchdog", text)
        self.assertIn("TaskStop", text)
        self.assertNotIn("b9zxz36m5", text)
        self.assertEqual([t.task_id for t in self.state().tasks], ["bbkwem7r3"])

    def test_session_end_forgets_tasks(self):
        save(SESSION, State(tasks=[BackgroundTask("bbkwem7r3", "w", iso_minutes_ago(1))]), self.env)
        self.run_hook("session_end", {"reason": "exit"})
        self.assertEqual(self.state().tasks, [])


if __name__ == "__main__":
    unittest.main()
