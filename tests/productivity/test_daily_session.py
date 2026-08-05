"""One record answers "what is he doing right now".

Four independent records used to answer it — ``mode``, ``current_task``,
``work_session_start``, and a ``started_at`` stamp in Google Tasks metadata —
with no transition keeping them together. Each test here pins one of the
drifts that actually happened.
"""

from __future__ import annotations

import json

import pytest

from argon.productivity.state import DailyState


@pytest.fixture
def state(tmp_path):
    return DailyState(tmp_path)


class TestStartingWork:
    def test_starting_a_session_puts_him_in_working_mode(self, state):
        """The missing edge: start_task set the task and left mode on idle, so
        the check-in gate read a working afternoon as free time."""
        state.start_session(kind="working", task_id="abc", title="SAT prep")

        assert state.get_mode() == "working"
        assert state.get_current_task() == "SAT prep"
        assert state.get_work_session_duration_minutes() is not None

    def test_mode_and_task_cannot_disagree(self, state):
        state.start_session(task_id="abc", title="SAT prep")
        data = state.get()
        assert data["mode"] == "working"
        assert data["current_task"] == "SAT prep"
        assert data["work_session_start"] is not None

    def test_a_second_start_replaces_the_first(self, state):
        """He has one attention; two sessions is a lie about it."""
        state.start_session(task_id="a", title="First")
        state.start_session(task_id="b", title="Second")

        assert state.get_current_task() == "Second"
        assert state.get_session()["task_id"] == "b"


class TestEndingWork:
    def test_finishing_returns_him_to_idle(self, state):
        """mode stuck on "working" with nothing running made pick_occasion take
        its mid-flow branch, measure zero minutes and go silent all afternoon."""
        state.start_session(task_id="abc", title="SAT prep")
        state.end_session()

        assert state.get_mode() == "idle"
        assert state.get_current_task() is None
        assert state.get_work_session_duration_minutes() is None

    def test_ending_reports_what_it_ended(self, state):
        state.start_session(task_id="abc", title="SAT prep")
        ended = state.end_session()

        assert ended["title"] == "SAT prep"
        assert ended["elapsed_min"] == 0

    def test_ending_nothing_is_not_an_error(self, state):
        assert state.end_session() is None
        assert state.get_mode() == "idle"


class TestModeAndSessionStayTogether:
    def test_a_resting_mode_ends_the_session(self, state):
        state.start_session(task_id="abc", title="SAT prep")
        state.set_mode("done")

        assert state.get_session() is None
        assert state.get_mode() == "done"

    def test_working_mode_starts_a_session_even_with_no_task(self, state):
        """"working" with no work_session_start is the state that muted him."""
        state.set_mode("working")

        assert state.get_session() is not None
        assert state.get_work_session_duration_minutes() is not None

    def test_moving_between_working_and_lock_in_keeps_the_task(self, state):
        state.start_session(task_id="abc", title="SAT prep")
        state.set_mode("lock_in")

        assert state.get_current_task() == "SAT prep"
        assert state.get_lock_in_duration_minutes() is not None
        assert state.get_work_session_duration_minutes() is None

    def test_napping_clears_the_session(self, state):
        state.start_session(title="SAT prep")
        state.set_mode("napping")

        assert state.get_session() is None
        assert state.get()["nap_start"] is not None


class TestTheDayBoundary:
    def test_a_session_cannot_survive_the_day(self, state, tmp_path):
        """The whole point of moving this out of Google Tasks metadata.

        "SAT prep", started 1:42 AM on 2026-08-02, still read as running on the
        evening of the 4th because the durable task store has no 4 AM reset.
        """
        state.start_session(task_id="abc", title="SAT prep")

        stored = json.loads((tmp_path / "daily" / "state.json").read_text())
        stored["date"] = "1999-01-01"
        (tmp_path / "daily" / "state.json").write_text(json.dumps(stored))

        assert state.get_session() is None
        assert state.get_mode() == "idle"
        assert state.get_current_task() is None

    def test_a_corrupt_state_file_reads_as_a_fresh_day(self, state, tmp_path):
        (tmp_path / "daily" / "state.json").write_text("{not json")
        assert state.get_mode() == "idle"
        assert state.get_session() is None


class TestMarkRunning:
    def test_the_running_task_is_flagged_from_the_session(self):
        from argon.tools.tasks import mark_running

        tasks = [{"id": "a", "title": "Chem"}, {"id": "b", "title": "SAT prep"}]
        session = {"task_id": "b", "title": "SAT prep", "elapsed_min": 42}

        out = mark_running(tasks, session)

        assert "running" not in out[0]
        assert out[1]["running"] is True
        assert out[1]["running_minutes"] == 42

    def test_no_session_means_nothing_is_running(self):
        from argon.tools.tasks import mark_running

        tasks = [{"id": "a", "title": "Chem"}]
        assert mark_running(tasks, None) == tasks

    def test_a_taskless_session_flags_nothing(self):
        """set_mode("working") starts a session with no task attached."""
        from argon.tools.tasks import mark_running

        tasks = [{"id": "a", "title": "Chem"}]
        out = mark_running(tasks, {"task_id": None, "title": None, "elapsed_min": 5})
        assert all("running" not in t for t in out)
