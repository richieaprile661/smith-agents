"""A session's step list and turn times, read from its own transcript."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import ImageDraw

from smith_agents import core, progress

PLAN = "mcp__ccd_turn__update_plan"
WRAP = "mcp__ccd_turn__wrap_up"


def user(text, at):
    return {"type": "user", "timestamp": at, "message": {"role": "user", "content": text}}


def result(at):
    return {"type": "user", "timestamp": at, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


def call(key, name, inputs, at, stop=None):
    return {"type": "assistant", "timestamp": at, "message": {
        "role": "assistant", "model": "claude-test", "stop_reason": stop,
        "content": [{"type": "tool_use", "id": key, "name": name, "input": inputs}]}}


def reply(text, at):
    return {"type": "assistant", "timestamp": at, "message": {
        "role": "assistant", "model": "claude-test", "stop_reason": "end_turn",
        "content": [{"type": "text", "text": text}]}}


def fold(records):
    reader = progress.Progress()
    for record in records:
        line = json.dumps(record, separators=(",", ":")).encode()
        if reader.wanted(line):
            reader.record(json.loads(line))
    return reader.snapshot()


def steps(*pairs):
    return [{"content": content, "status": status} for content, status in pairs]


class PlanTests(unittest.TestCase):
    def test_plan_tool_sets_advances_and_marks_steps(self):
        items = steps(("Read the flow", "in_progress"), ("Write the test", "pending"),
                      ("Fix rounding", "pending"), ("Run tests", "pending"))
        records = [user("fix the tax bug", 100), call("a", PLAN, {"items": items, "scope": "first_task"}, 101)]
        self.assertEqual(fold(records)["steps"], items)
        records.append(call("b", PLAN, {"advance": True}, 102))
        self.assertEqual([step["status"] for step in fold(records)["steps"]],
                         ["completed", "in_progress", "pending", "pending"])
        records.append(call("c", PLAN, {"done": [2], "start": 4}, 103))
        self.assertEqual([step["status"] for step in fold(records)["steps"]],
                         ["completed", "completed", "pending", "in_progress"])

    def test_a_replayed_call_is_counted_once(self):
        items = steps(("One", "in_progress"), ("Two", "pending"), ("Three", "pending"))
        advance = call("b", PLAN, {"advance": True}, 102)
        found = fold([user("go", 100), call("a", PLAN, {"items": items}, 101), advance, advance])
        self.assertEqual([step["status"] for step in found["steps"]],
                         ["completed", "in_progress", "pending"])

    def test_wrap_up_completes_the_list_and_todo_lists_replace_it(self):
        items = steps(("One", "completed"), ("Two", "in_progress"))
        found = fold([user("go", 100), call("a", PLAN, {"items": items}, 101),
                      call("b", WRAP, {"recap": "Done."}, 102)])
        self.assertEqual([step["status"] for step in found["steps"]], ["completed", "completed"])
        todos = [{"content": "Ship it", "status": "in_progress", "activeForm": "Shipping it"},
                 {"content": "Rest", "status": "pending", "activeForm": "Resting"}]
        found = fold([user("go", 100), call("a", "TodoWrite", {"todos": todos}, 101)])
        self.assertEqual(found["steps"], steps(("Ship it", "in_progress"), ("Rest", "pending")))

    def test_malformed_plans_change_nothing(self):
        items = steps(("One", "in_progress"))
        for bad in ({"items": "no"}, {"done": ["x"], "start": 9}, {"advance": "yes"}, None, {"items": [1, {"content": 5}]}):
            with self.subTest(bad=bad):
                found = fold([user("go", 100), call("a", PLAN, {"items": items}, 101), call("b", PLAN, bad, 102)])
                self.assertIn(found["steps"], (items, []))
        self.assertEqual(fold([user("go", 100), call("a", PLAN, {"items": [{"content": "x", "status": "odd"}]}, 101)])["steps"],
                         steps(("x", "pending")))


class TurnTests(unittest.TestCase):
    def test_a_turn_runs_from_the_prompt_to_the_end_of_the_reply(self):
        found = fold([user("fix it", 100), call("a", "Bash", {"command": "ls"}, 105), result(106)])
        self.assertEqual((found["turn_started"], found["turn_ended"]), (100, None))
        found = fold([user("fix it", 100), call("a", "Bash", {"command": "ls"}, 105), result(106),
                      reply("Fixed.", 160)])
        self.assertEqual((found["turn_started"], found["turn_ended"]), (100, 160))

    def test_an_interrupted_turn_ends_and_local_commands_start_none(self):
        found = fold([user("fix it", 100), user("[Request interrupted by user]", 130)])
        self.assertEqual((found["turn_started"], found["turn_ended"]), (100, 130))
        found = fold([user("fix it", 100), reply("Done.", 120),
                      user("<local-command-stdout>ok</local-command-stdout>", 200),
                      user("<bash-input>ls</bash-input>", 210),
                      dict(user("skill text", 220), isMeta=True)])
        self.assertEqual((found["turn_started"], found["turn_ended"]), (100, 120))

    def test_a_finished_list_is_cleared_by_the_next_prompt_and_an_open_one_is_kept(self):
        done = steps(("One", "completed"), ("Two", "completed"))
        found = fold([user("go", 100), call("a", PLAN, {"items": done}, 101), reply("Done.", 110), user("next", 200)])
        self.assertEqual(found["steps"], [])
        open_ = steps(("One", "completed"), ("Two", "in_progress"))
        found = fold([user("go", 100), call("a", PLAN, {"items": open_}, 101), reply("Paused.", 110), user("carry on", 200)])
        self.assertEqual(found["steps"], open_)


class ReaderTests(unittest.TestCase):
    def test_reads_only_what_a_transcript_gained_and_nothing_until_read_through(self):
        items = steps(("One", "in_progress"), ("Two", "pending"))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.jsonl"
            lines = [user("go", 100), call("a", PLAN, {"items": items}, 101)]
            path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
            with patch.object(progress, "_readers", type(progress._readers)()):
                with patch.object(progress.Progress, "BUDGET", 40):
                    self.assertIsNone(progress.read(path))  # An old plan, half read, is not the plan.
                self.assertEqual(progress.read(path)["steps"], items)
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(call("b", PLAN, {"advance": True}, 102)) + "\n")
                self.assertEqual([step["status"] for step in progress.read(path)["steps"]],
                                 ["completed", "in_progress"])
            self.assertIsNone(progress.read(Path(folder) / "missing.jsonl"))

    def test_tool_output_is_never_parsed(self):
        reader = progress.Progress()
        big = json.dumps(result(5), separators=(",", ":")).encode()
        self.assertFalse(reader.wanted(big))
        self.assertFalse(reader.wanted(json.dumps(call("a", "Bash", {"command": "ls"}, 5), separators=(",", ":")).encode()))
        self.assertTrue(reader.wanted(json.dumps(user("hello", 5), separators=(",", ":")).encode()))


class RowTests(unittest.TestCase):
    def row(self, **extra):
        return {**core.demo_agents()[1], "sub": False, "state": "working", "permissions": [], **extra}

    def test_turn_clock_runs_while_working_and_rests_at_the_last_turn(self):
        self.assertEqual(core.turn_clock(134), "02:14")
        self.assertEqual(core.turn_clock(3734), "1:02:14")
        working = self.row(turn_started=1000, turn_ended=None)
        self.assertEqual(core.agent_turn_seconds(working, 1134), 134)
        # A new prompt the transcript has not shown yet: no stale clock.
        self.assertIsNone(core.agent_turn_seconds(self.row(turn_started=1000, turn_ended=1100), 1500))
        done = self.row(state="done", turn_started=1000, turn_ended=1532)
        self.assertEqual(core.agent_turn_seconds(done, 9999), 532)
        self.assertIsNone(core.agent_turn_seconds(self.row(state="done", turn_started=1000, turn_ended=None), 9999))
        self.assertIsNone(core.agent_turn_seconds(self.row(), 9999))

    def test_steps_line_names_the_count_and_the_step_in_hand(self):
        row = self.row(steps=steps(("Read", "completed"), ("Fix tax rounding", "in_progress"), ("Test", "pending")),
                       turn_started=1000, turn_ended=None)
        self.assertEqual(core.agent_steps_line(row, 1100), "1/3 · Fix tax rounding")
        row["steps"][1]["status"] = "pending"
        self.assertEqual(core.agent_steps_line(row, 1100), "1/3 · Fix tax rounding")
        done = self.row(state="done", turn_started=1000, turn_ended=1532,
                        steps=steps(("Read", "completed"), ("Fix", "completed")))
        self.assertEqual(core.agent_steps_line(done, 9999), "2/2 · last turn 08:52")
        done["turn_ended"] = None
        self.assertEqual(core.agent_steps_line(done, 9999), "2/2 · all done")

    def texts(self, row, open_):
        chip = core.console_base((core.CONSOLE_W, core.agent_row_height(row, open_)))
        draw = ImageDraw.Draw(chip)
        pen = Mock(wraps=draw)
        with patch.object(core.time, "time", return_value=1134):
            core.render_row(pen, chip, row, 0, 0, open_, False, draw_figure=False)
        return [call.args[1] for call in pen.text.call_args_list], pen, draw

    def test_row_shows_steps_and_turn_time_and_a_row_without_them_is_unchanged(self):
        plain = self.row()
        listed = self.row(steps=steps(("Read the flow", "completed"), ("Fix tax rounding", "in_progress"),
                                      ("Run the tests", "pending")), turn_started=1000, turn_ended=None)
        for scale in (1, 2):
            with patch.object(core, "SCALE", scale):
                for open_ in (False, True):
                    with self.subTest(scale=scale, open_=open_):
                        before, _, _ = self.texts(plain, open_)
                        self.assertFalse([text for text in before if "Turn " in text or "/3" in text])
                        self.assertGreater(core.agent_row_height(listed, open_), core.agent_row_height(plain, open_))
                        after, pen, draw = self.texts(listed, open_)
                        self.assertIn("Turn 02:14", after)
                        if open_:
                            self.assertIn("1 of 3", after)
                            for content in ("Read the flow", "Fix tax rounding", "Run the tests"):
                                self.assertIn(content, after)
                        else:
                            self.assertIn("1/3 · Fix tax rounding", after)
                        # Nothing drawn runs past the row's right edge or its bottom.
                        height = core.agent_row_height(listed, open_)
                        for item in pen.text.call_args_list:
                            box = draw.textbbox(item.args[0], item.args[1], font=item.kwargs["font"])
                            self.assertLessEqual(box[2], core.CONSOLE_W - core.PAD_X + 1, item.args[1])
                            self.assertLess(box[3], height, item.args[1])

    def test_helpers_and_long_lists_stay_bounded(self):
        helper = dict(self.row(steps=steps(("One", "in_progress"))), sub=True)
        self.assertEqual(core.agent_row_height(helper), core.agent_row_height(dict(helper, steps=[])))
        many = self.row(steps=steps(*[("Step %d with a rather long description of the work" % index, "pending")
                                      for index in range(40)]))
        texts, _, _ = self.texts(many, True)
        self.assertIn("0 of 40", texts)
        self.assertLessEqual(len([text for text in texts if text.startswith("Step ")]), core.STEP_ROWS)
        self.assertIn("28 more", " ".join(texts))

    def test_decorate_reads_progress_for_claude_sessions_only(self):
        found = {"steps": steps(("One", "in_progress")), "turn_started": 5, "turn_ended": None}
        claude = {"id": "s", "cwd": "/projects/shop", "state": "working", "provider": "claude"}
        helper = {"id": "agent-x", "cwd": "/projects/shop", "state": "working", "provider": "claude",
                  "sub": True, "transcript": "/projects/x.jsonl"}
        hosted = {"id": "codex:1", "cwd": "/projects/shop", "state": "working", "provider": "codex"}
        with patch.object(core, "_agent_transcript", return_value="/projects/s.jsonl"), \
             patch.object(core, "_tail_records", return_value=[]), \
             patch.object(core, "read_capacity", return_value=None), \
             patch.object(core, "pending_permissions", return_value=[]), \
             patch.object(core, "git_branch", return_value=None), \
             patch.object(progress, "read", return_value=found) as read:
            core.decorate_agents([claude, helper, hosted])
        read.assert_called_once_with("/projects/s.jsonl")
        self.assertEqual(claude["steps"], found["steps"])
        self.assertEqual((claude["turn_started"], claude["turn_ended"]), (5, None))
        self.assertNotIn("steps", helper)
        self.assertNotIn("steps", hosted)


class OrderTests(unittest.TestCase):
    def test_sessions_of_one_project_sit_together_in_the_slot_of_its_oldest(self):
        rows = [
            {"id": "a", "cwd": "/projects/shop", "since": 10},
            {"id": "b", "cwd": "/projects/site", "since": 20},
            {"id": "c", "cwd": "/projects/shop/.claude/worktrees/fix-1b2c", "since": 30},
            {"id": "d", "cwd": "/projects/blog", "since": 40},
            {"id": "e", "cwd": "/Projects/Site/", "since": 50},
            {"id": "f", "cwd": "", "since": 60},
            {"id": "g", "cwd": "", "since": 70},
            {"id": "c1", "cwd": "/projects/shop", "since": 35, "sub": True, "parent": "c"},
        ]
        order = [row["id"] for row in core.sort_agents(list(reversed(rows)))]
        self.assertEqual(order, ["a", "c", "c1", "b", "e", "d", "f", "g"])

    def test_one_session_per_project_keeps_the_oldest_first_order(self):
        rows = [{"id": name, "cwd": "/projects/" + name, "since": since}
                for name, since in (("x", 3), ("y", 1), ("z", 2))]
        self.assertEqual([row["id"] for row in core.sort_agents(rows)], ["y", "z", "x"])


if __name__ == "__main__":
    unittest.main()
