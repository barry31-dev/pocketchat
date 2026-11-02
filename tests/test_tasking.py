"""The task state machine and task record: one owner, closed transitions.

A task's state is what a resume and a report both have to agree on. It lives
here and nowhere else -- not in the CLI, not in the session file -- so that
"where did this stop?" has exactly one answer. The transitions are a closed
table: an event the machine does not name is refused rather than ignored, and
a terminal task is finished rather than quietly restartable.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from pocketchat.tasking import (ACTIVE_STATES, TASK_RECORD_SCHEMA,
                                TERMINAL_STATES, TaskError,
                                _as_payload, create_record, load_record,
                                next_state, policy_digest, record_step,
                                save_record, task_summary, transition)
from pocketchat.task_policy import parse_policy


POLICY = {
    "schema_version": "pocketchat-task-policy.v1",
    "objective": "compare two documents",
    "max_steps": 3,
    "max_tool_calls": 5,
    "max_context_chars": 48000,
    "max_turn_tokens": 8000,
    "tools": {"read_file": {"allow_paths": ["input/a.md"]}},
}


class TransitionTests(unittest.TestCase):
    def test_the_declared_transitions_are_the_only_legal_ones(self):
        legal = {
            ("created", "start"): "running",
            ("running", "checkpoint"): "checkpointed",
            ("running", "interrupt"): "interrupted",
            ("running", "complete"): "completed",
            ("running", "fail"): "failed",
            ("running", "cancel"): "cancelled",
            ("checkpointed", "resume"): "running",
            ("checkpointed", "cancel"): "cancelled",
            ("interrupted", "resume"): "running",
            ("interrupted", "fail"): "failed",
            ("interrupted", "cancel"): "cancelled",
        }
        for (state, event), expected in sorted(legal.items()):
            with self.subTest(state=state, event=event):
                self.assertEqual(expected, next_state(state, event))

    def test_an_unknown_state_is_refused(self):
        with self.assertRaises(TaskError) as caught:
            next_state("paused", "start")
        self.assertIn("paused", str(caught.exception))

    def test_an_unknown_event_is_refused(self):
        with self.assertRaises(TaskError) as caught:
            next_state("running", "auto_approve")
        self.assertIn("auto_approve", str(caught.exception))

    def test_an_illegal_transition_names_both_sides(self):
        with self.assertRaises(TaskError) as caught:
            next_state("created", "resume")
        message = str(caught.exception)
        self.assertIn("created", message)
        self.assertIn("resume", message)

    def test_a_terminal_state_cannot_restart(self):
        """Terminal is terminal: a resume of a finished task is not a fact."""
        for state in sorted(TERMINAL_STATES):
            for event in ("start", "resume", "checkpoint", "complete",
                          "fail", "cancel"):
                with self.subTest(state=state, event=event):
                    with self.assertRaises(TaskError):
                        next_state(state, event)

    def test_the_state_sets_partition_every_state_exactly_once(self):
        self.assertEqual(set(), ACTIVE_STATES & TERMINAL_STATES)
        self.assertEqual({"created", "running", "checkpointed",
                          "interrupted"}, set(ACTIVE_STATES))
        self.assertEqual({"completed", "failed", "cancelled"},
                         set(TERMINAL_STATES))


class RecordTests(unittest.TestCase):
    def test_a_new_record_names_its_schema_objective_and_policy_digest(self):
        policy = parse_policy(POLICY)
        record = create_record(policy)
        self.assertEqual(TASK_RECORD_SCHEMA, record.schema_version)
        self.assertEqual("created", record.state)
        self.assertEqual("compare two documents", record.objective)
        self.assertEqual(policy_digest(POLICY), record.policy_sha256)
        self.assertEqual(3, record.max_steps)
        self.assertEqual([], record.steps)

    def test_the_task_id_is_unique_and_bounded(self):
        first = create_record(parse_policy(POLICY))
        second = create_record(parse_policy(POLICY))
        self.assertNotEqual(first.task_id, second.task_id)
        self.assertTrue(first.task_id.startswith("task_"))
        self.assertLessEqual(len(first.task_id), 64)

    def test_the_policy_digest_is_stable_and_key_order_does_not_matter(self):
        reordered = {"max_steps": 3, "objective": "compare two documents",
                     "schema_version": "pocketchat-task-policy.v1",
                     "max_tool_calls": 5, "max_context_chars": 48000,
                     "max_turn_tokens": 8000,
                     "tools": {"read_file": {"allow_paths": ["input/a.md"]}}}
        self.assertEqual(policy_digest(POLICY), policy_digest(reordered))
        self.assertNotEqual(policy_digest(POLICY),
                            policy_digest(dict(reordered, max_steps=4)))

    def test_a_refused_terminal_transition_leaves_the_record_unchanged(self):
        """Validation before mutation: a refusal must not half-apply.

        A record left in a terminal state with no reason is the exact thing the
        format says never happens -- a reader would see "finished" and no
        answer to "why", and could only invent one.
        """
        record = create_record(parse_policy(POLICY))
        transition(record, "start")
        before = (record.state, record.terminal_reason, len(record.steps))
        with self.assertRaises(TaskError):
            transition(record, "complete")
        with self.assertRaises(TaskError):
            transition(record, "fail", reason="   ")
        self.assertEqual(before,
                         (record.state, record.terminal_reason, len(record.steps)))
        self.assertEqual("running", record.state)

    def test_a_terminal_transition_keeps_the_reason_it_was_given(self):
        record = create_record(parse_policy(POLICY))
        transition(record, "start")
        self.assertEqual("completed", transition(record, "complete",
                                                 reason="objective met"))
        self.assertEqual("objective met", record.terminal_reason)

    def test_a_step_is_appended_in_order_with_its_number(self):
        record = create_record(parse_policy(POLICY))
        record_step(record, "model", state="running", detail="first")
        record_step(record, "tool", state="completed", tool="read_file",
                    detail="input/a.md")
        self.assertEqual([1, 2], [step["index"] for step in record.steps])
        self.assertEqual(["model", "tool"],
                         [step["kind"] for step in record.steps])
        self.assertEqual("read_file", record.steps[1]["tool"])

    def test_a_step_beyond_the_declared_budget_is_refused(self):
        """The budget is enforced where the state lives, not only by a caller."""
        record = create_record(parse_policy(POLICY))
        for _ in range(3):
            record_step(record, "model", state="running", detail="x")
        with self.assertRaises(TaskError):
            record_step(record, "model", state="running", detail="y")

    def test_a_step_must_say_how_it_ended(self):
        """`state` is required; `detail` is optional, but not blank."""
        record = create_record(parse_policy(POLICY))
        with self.assertRaises(TaskError):
            record_step(record, "model", state="", detail="x")
        with self.assertRaises(TaskError):
            record_step(record, "model", state="   ", detail="x")
        step = record_step(record, "model", state="running")
        self.assertNotIn("detail", step)
        with self.assertRaises(TaskError):
            record_step(record, "model", state="running", detail="  ")

    def test_a_tool_step_names_its_tool_and_only_a_tool_step_does(self):
        record = create_record(parse_policy(POLICY))
        step = record_step(record, "tool", state="completed",
                           tool="read_file", detail="input/a.md")
        self.assertEqual("read_file", step["tool"])
        with self.assertRaises(TaskError):
            record_step(record, "model", state="running", tool="read_file")
        with self.assertRaises(TaskError):
            record_step(record, "tool", state="completed", tool="  ")

    def test_an_unknown_step_kind_is_refused(self):
        record = create_record(parse_policy(POLICY))
        with self.assertRaises(TaskError):
            record_step(record, "shell", state="completed", detail="rm")


class PersistenceTests(unittest.TestCase):
    def test_a_record_round_trips_through_disk(self):
        policy = parse_policy(POLICY)
        record = create_record(policy)
        record_step(record, "model", state="running", detail="first")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = save_record(root, record)
            self.assertTrue(path.is_file(), path)
            loaded = load_record(root, record.task_id)
            self.assertEqual(record.task_id, loaded.task_id)
            self.assertEqual(record.steps, loaded.steps)
            self.assertEqual(record.policy_sha256, loaded.policy_sha256)

    def test_an_unknown_task_id_is_refused_with_a_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TaskError) as caught:
                load_record(Path(directory), "task_absent")
            self.assertIn("task_absent", str(caught.exception))

    def test_a_record_with_an_unknown_schema_version_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["schema_version"] = "pocketchat-task.v9"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("pocketchat-task.v9", str(caught.exception))

    def test_an_unparsable_or_non_object_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ".pocketchat" / "tasks"
            root.mkdir(parents=True)
            broken = root / "task_broken.json"
            broken.write_text("{not json", encoding="utf-8")
            with self.assertRaises(TaskError):
                load_record(root.parent.parent, "task_broken")
            listed = root / "task_listed.json"
            listed.write_text("[]", encoding="utf-8")
            with self.assertRaises(TaskError):
                load_record(root.parent.parent, "task_listed")

    def _raw_record(self, root, task_id, **overrides):
        """Write one record file by hand, so a broken one can be read back."""
        record = create_record(parse_policy(POLICY))
        payload = _as_payload(record)
        payload["task_id"] = task_id
        payload.update(overrides)
        directory = Path(root) / ".pocketchat" / "tasks"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ("%s.json" % task_id)).write_text(
            json.dumps(payload), encoding="utf-8")
        return record

    def test_a_record_missing_a_required_field_names_it_and_is_refused(self):
        """A broken record is a refusal, not a KeyError out of the loader.

        Task resume reads these files, so a record a newer or interrupted
        writer left incomplete has to fail closed with the field named --
        anything else crashes the reader with no answer to "which file, why".
        """
        with tempfile.TemporaryDirectory() as directory:
            for missing in ("max_steps", "max_tool_calls", "objective",
                            "policy_sha256", "created", "state", "steps"):
                with self.subTest(missing=missing):
                    self._raw_record(directory, "task_missing")
                    path = (Path(directory) / ".pocketchat" / "tasks"
                            / "task_missing.json")
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    del payload[missing]
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(TaskError) as caught:
                        load_record(Path(directory), "task_missing")
                    self.assertIn(missing, str(caught.exception))
                    path.unlink()

    def test_a_record_with_malformed_root_evidence_is_refused(self):
        """A report must not stringify a forged objective or digest."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for field, value in (
                    ("created", 123),
                    ("objective", ["not", "text"]),
                    ("policy_sha256", "not-a-digest"),
                    ("workspace_root", 7)):
                with self.subTest(field=field):
                    record = create_record(parse_policy(POLICY))
                    path = save_record(root, record)
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload[field] = value
                    path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(TaskError) as caught:
                        load_record(root, record.task_id)
                    self.assertIn(field, str(caught.exception))
                    path.unlink()

    def test_a_record_with_a_non_integer_budget_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            for bad in ("8", None, [8]):
                with self.subTest(max_steps=bad):
                    self._raw_record(directory, "task_badbudget",
                                     max_steps=bad)
                    with self.assertRaises(TaskError) as caught:
                        load_record(Path(directory), "task_badbudget")
                    self.assertIn("max_steps", str(caught.exception))
                    (Path(directory) / ".pocketchat" / "tasks"
                     / "task_badbudget.json").unlink()

    def test_a_record_whose_steps_are_not_a_list_of_objects_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            self._raw_record(directory, "task_badsteps", steps=[{"index": 1}])
            path = (Path(directory) / ".pocketchat" / "tasks"
                    / "task_badsteps.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["steps"] = ["not a step"]
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(Path(directory), "task_badsteps")
            self.assertIn("steps", str(caught.exception))

    def test_a_terminal_record_must_name_its_terminal_reason(self):
        """A terminal record without a reason cannot answer why it stopped."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            transition(record, "start", "task run started")
            transition(record, "fail", "provider failed")
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("terminal_reason")
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("terminal_reason", str(caught.exception))

    def test_an_active_record_cannot_carry_a_terminal_reason(self):
        """An active record must not look terminal to a status reader."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["terminal_reason"] = "forged"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("terminal_reason", str(caught.exception))

    def test_a_record_task_id_must_match_its_filename(self):
        """The file's name and the record's identity must be one fact."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["task_id"] = "task_someone_else"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("task_id", str(caught.exception))

    def test_a_record_with_unknown_fields_is_refused(self):
        """An unknown field cannot become a fact a report silently displays."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["forged_state"] = "completed"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("forged_state", str(caught.exception))

    def test_a_record_step_must_match_the_schema_shape(self):
        """A step with a bad kind or missing state is not reportable."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            record_step(record, "model", state="completed")
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["steps"][0]["kind"] = "shell"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("kind", str(caught.exception))

    def test_a_record_cannot_exceed_its_tool_call_budget(self):
        """A forged over-budget record is a refusal, not a valid report."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            record_step(record, "tool", state="completed", tool="read_file")
            record_step(record, "tool", state="completed", tool="read_file")
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["max_tool_calls"] = 1
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("max_tool_calls", str(caught.exception))

    def test_a_record_step_sequence_must_be_contiguous_and_within_budget(self):
        """A malformed step list must not become reportable evidence."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = create_record(parse_policy(POLICY))
            record_step(record, "model", state="completed")
            path = save_record(root, record)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["steps"][0]["index"] = 2
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TaskError) as caught:
                load_record(root, record.task_id)
            self.assertIn("index", str(caught.exception))

    def test_a_record_is_written_once_and_a_second_save_is_refused(self):
        """An exclusive create is what makes the record evidence."""
        policy = parse_policy(POLICY)
        record = create_record(policy)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_record(root, record)
            with self.assertRaises(TaskError):
                save_record(root, record)

    def test_the_task_id_must_be_a_safe_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            for bad in ("../escape", "a b", "", "x" * 80):
                with self.subTest(task_id=bad):
                    with self.assertRaises(TaskError):
                        load_record(Path(directory), bad)


class SummaryTests(unittest.TestCase):
    def test_the_summary_reports_only_what_the_record_says(self):
        record = create_record(parse_policy(POLICY))
        record_step(record, "tool", state="completed", tool="read_file",
                    detail="input/a.md")
        summary = task_summary(record)
        self.assertEqual("created", summary["state"])
        self.assertEqual(1, summary["steps"])
        self.assertEqual(1, summary["tool_calls"])
        self.assertEqual(3, summary["max_steps"])
        self.assertIsNone(summary["terminal_reason"])
        self.assertEqual({}, summary["budget"])

    def test_a_terminal_reason_is_reported_not_inferred(self):
        record = create_record(parse_policy(POLICY))
        record.state = next_state(record.state, "start")
        record.state = next_state(record.state, "fail")
        record.terminal_reason = "tool_call_budget_exceeded"
        self.assertEqual("failed", task_summary(record)["state"])
        self.assertEqual("tool_call_budget_exceeded",
                         task_summary(record)["terminal_reason"])


class SchemaAgreementTests(unittest.TestCase):
    """The shipped task schema and this module must not drift apart.

    The project is standard-library only, so these are structural guards rather
    than a jsonschema run: the declared property names, enums and constants are
    compared against what the code actually writes.
    """

    def setUp(self):
        path = (Path(__file__).resolve().parents[1] / "pocketchat"
                / "schemas" / "task.schema.json")
        self.schema = json.loads(path.read_text(encoding="utf-8"))
        self.record_def = self.schema["$defs"]["task_record"]
        self.step_def = self.schema["$defs"]["step"]

    def _payload(self):
        from pocketchat.tasking import _as_payload
        record = create_record(parse_policy(POLICY))
        record_step(record, "model", state="running", run_id="run_1")
        record_step(record, "tool", state="completed", tool="read_file",
                    detail="input/a.md")
        return record, _as_payload(record)

    def test_every_written_record_field_is_declared_by_the_schema(self):
        record, payload = self._payload()
        declared = set(self.record_def["properties"])
        self.assertLessEqual(set(payload), declared,
                             sorted(set(payload) - declared))
        self.assertLessEqual(set(self.record_def["required"]), set(payload))

    def test_the_record_state_enum_is_exactly_the_machine_states(self):
        from pocketchat.tasking import ACTIVE_STATES, TERMINAL_STATES
        self.assertEqual(set(self.record_def["properties"]["state"]["enum"]),
                         ACTIVE_STATES | TERMINAL_STATES)

    def test_the_step_schema_declares_exactly_the_written_step_fields(self):
        _record, payload = self._payload()
        steps = payload["steps"]
        written = set()
        for step in steps:
            written |= set(step)
        self.assertLessEqual(written, set(self.step_def["properties"]),
                             sorted(written - set(self.step_def["properties"])))
        for key in self.step_def["required"]:
            for step in steps:
                self.assertIn(key, step)

    def test_the_step_kind_enum_is_exactly_the_kinds_the_recorder_accepts(self):
        from pocketchat.tasking import STEP_KINDS
        self.assertEqual(set(self.step_def["properties"]["kind"]["enum"]),
                         set(STEP_KINDS))

    def test_the_record_schema_version_const_is_the_one_written(self):
        _record, payload = self._payload()
        self.assertEqual(
            self.record_def["properties"]["schema_version"]["const"],
            TASK_RECORD_SCHEMA)
        self.assertEqual(TASK_RECORD_SCHEMA, payload["schema_version"])


class SkeletonInventoryTests(unittest.TestCase):
    """The v1 artifact inventory names the task schemas too."""

    def test_the_skeleton_inventory_includes_the_task_schemas(self):
        """The artifact inventory is one list; task joins it, not a copy."""
        text = (Path(__file__).resolve().parent / "test_skeleton.py"
                ).read_text(encoding="utf-8")
        missing = [name for name in ("TASK_RECORD_SCHEMA", "POLICY_SCHEMA",
                                     "pocketchat-task.v1",
                                     "pocketchat-task-policy.v1")
                   if name not in text]
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
