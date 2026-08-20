"""Tasks owned by Argon rather than by Google.

The interface is deliberately identical to GoogleTasksStore, so these are the
same guarantees the old store had to make — id resolution that refuses to
guess, deduplication on Classroom identity, and completion that records time.
"""

import pytest

from argon.google.tasks_store import TaskResolutionAmbiguityError
from argon.tasks.local_store import LocalTaskStore


@pytest.fixture
def tasks(tmp_path):
    return LocalTaskStore(tmp_path)


class TestBasics:
    def test_a_new_task_comes_back_pending(self, tasks):
        tasks.add_task("Send Vasquez email", subject="UCLA", due="2026-08-19")

        [task] = tasks.get_all()
        assert task["title"] == "Send Vasquez email"
        assert task["done"] is False
        assert task["subject"] == "UCLA"

    def test_completed_work_leaves_the_pending_list(self, tasks):
        tasks.add_task("HW 3")
        tasks.complete_task("HW 3")

        assert tasks.get_all() == []
        assert len(tasks.get_all(include_done=True)) == 1

    def test_completion_records_time_spent(self, tasks):
        tasks.add_task("APUSH reading")

        done = tasks.complete_task("APUSH reading", actual_min=35)

        assert done["time_actual_min"] == 35
        assert done["done_at"]

    def test_high_priority_sorts_first(self, tasks):
        tasks.add_task("Low thing", priority="low", due="2026-08-19")
        tasks.add_task("Urgent thing", priority="high", due="2026-08-25")

        assert [t["title"] for t in tasks.get_all()][0] == "Urgent thing"

    def test_starting_stamps_the_task(self, tasks):
        tasks.add_task("Physics lab")

        started = tasks.start_task("Physics lab")

        assert started["started_at"]


class TestResolution:
    def test_an_exact_id_wins(self, tasks):
        made = tasks.add_task("Chapter 2 key terms")

        assert tasks._resolve(made["id"])["title"] == "Chapter 2 key terms"

    def test_a_partial_title_resolves(self, tasks):
        tasks.add_task("Chapter 2 key terms")

        assert tasks._resolve("key terms")["title"] == "Chapter 2 key terms"

    def test_an_ambiguous_title_refuses_to_guess(self, tasks):
        # Guessing once completed the wrong "Math homework" of two, and a wrong
        # completion is invisible — the board just looks one item shorter.
        tasks.add_task("Math homework Monday")
        tasks.add_task("Math homework Tuesday")

        with pytest.raises(TaskResolutionAmbiguityError):
            tasks._resolve("Math homework")

    def test_an_unknown_id_is_simply_missing(self, tasks):
        assert tasks._resolve("nothing like this") is None

    def test_a_classroom_key_addresses_its_task(self, tasks):
        tasks.add_task("Serving Time in Virginia", source="classroom",
                       classroom_key="871775830226:874660636840")

        found = tasks._resolve("871775830226:874660636840")

        assert found["title"] == "Serving Time in Virginia"


class TestDeduplication:
    def test_the_same_title_twice_is_one_commitment(self, tasks):
        tasks.add_task("Chemistry reading & notes")
        again = tasks.add_task("Chemistry reading & notes")

        assert again["already_existed"] is True
        assert len(tasks.get_all()) == 1

    def test_one_assignment_is_one_row_even_retitled(self, tasks):
        key = "871775830226:874660636840"
        tasks.add_task("Key terms", source="classroom", classroom_key=key)
        again = tasks.add_task("Chapter 2 Key Terms", source="classroom", classroom_key=key)

        assert again["already_existed"] is True
        assert len(tasks.get_all()) == 1

    def test_completing_frees_the_title_again(self, tasks):
        # Next week's identically-named reading is genuinely new work.
        tasks.add_task("Weekly reading")
        tasks.complete_task("Weekly reading")

        again = tasks.add_task("Weekly reading")

        assert not again.get("already_existed")


class TestRescheduling:
    def test_due_dates_move(self, tasks):
        tasks.add_task("HW 4", due="2026-08-19")

        assert tasks.update_due("HW 4", "2026-08-21") is True
        assert tasks.get_all()[0]["due"] == "2026-08-21"

    def test_moving_something_that_is_not_there_says_so(self, tasks):
        assert tasks.update_due("ghost", "2026-08-21") is False


class TestClassroomImport:
    ASSIGNMENTS = [
        {"title": "HW 2", "classroom_key": "c1:a1", "due": "2026-08-21",
         "course_name": "Math", "course_id": "c1"},
        {"title": "Key terms", "classroom_key": "c1:a2", "due": "2026-08-22",
         "course_name": "APUSH", "course_id": "c1"},
    ]

    def test_it_imports_each_assignment_once(self, tasks):
        assert tasks.bulk_add_from_classroom(self.ASSIGNMENTS) == 2
        assert tasks.bulk_add_from_classroom(self.ASSIGNMENTS) == 0
        assert len(tasks.get_all()) == 2

    def test_an_assignment_with_no_key_is_skipped(self, tasks):
        # Without durable identity it would duplicate on the next sync.
        assert tasks.bulk_add_from_classroom([{"title": "Loose"}]) == 0


class TestMigration:
    """Importing the existing list, safely enough to run twice."""

    class FakeGoogle:
        def __init__(self, tasks):
            self._tasks = tasks

        def get_all(self, *, include_done=False):
            return self._tasks

    ROWS = [
        {"title": "Send Vasquez email", "google_task_id": "g1", "priority": "high",
         "work_by": "2026-08-19", "source": "manual", "done": False},
        {"title": "Key terms", "google_task_id": "g2", "classroom_key": "c:1",
         "source": "classroom", "work_by": "2026-08-21", "done": False},
        {"title": "Old finished thing", "google_task_id": "g3", "done": True,
         "done_at": "2026-08-10T10:00:00", "source": "manual"},
    ]

    def _patch(self, monkeypatch, rows):
        import argon.google.tasks_store as gts
        monkeypatch.setattr(gts, "GoogleTasksStore", lambda ws: self.FakeGoogle(rows))

    def test_it_brings_everything_across(self, tmp_path, monkeypatch):
        from argon.tasks.local_store import migrate_from_google
        self._patch(monkeypatch, self.ROWS)

        out = migrate_from_google(tmp_path)

        assert out["imported"] == 3
        assert out["local_pending"] == 2
        assert out["local_total"] == 3

    def test_running_it_twice_imports_nothing(self, tmp_path, monkeypatch):
        # The safe way to do this is run it, check the counts, run it again.
        from argon.tasks.local_store import migrate_from_google
        self._patch(monkeypatch, self.ROWS)

        migrate_from_google(tmp_path)
        second = migrate_from_google(tmp_path)

        assert second["imported"] == 0
        assert second["already_present"] == 3
        assert second["local_total"] == 3

    def test_completion_state_survives(self, tmp_path, monkeypatch):
        from argon.tasks.local_store import migrate_from_google
        self._patch(monkeypatch, self.ROWS)
        migrate_from_google(tmp_path)

        store = LocalTaskStore(tmp_path)
        done = [t for t in store.get_all(include_done=True) if t["done"]]

        assert [t["title"] for t in done] == ["Old finished thing"]


class TestMigratedIdentity:
    """A migrated row keeps answering to the id the board shows for it."""

    def _with_google_id(self, tasks, title, gid):
        from argon.core import store as doc_store
        made = tasks.add_task(title)
        with doc_store.txn() as conn:
            conn.execute("UPDATE tasks SET google_task_id = ? WHERE id = ?", (gid, made["id"]))
        return made

    def test_the_board_id_of_a_migrated_task_resolves(self, tasks):
        from argon.commitments import _from_task

        self._with_google_id(tasks, "Send Vasquez email", "g_legacy")
        board_id = _from_task(tasks.get_all()[0]).as_dict()["id"]

        # The board prefers the Google id where a row has one, so the store has
        # to recognise it — otherwise starting the task falls back to matching
        # on title, which is precisely the guess it must never make.
        assert board_id == "g_legacy"
        assert tasks._resolve(board_id) is not None

    def test_it_does_not_fall_back_to_a_title_guess(self, tasks):
        self._with_google_id(tasks, "Math homework Monday", "g1")
        self._with_google_id(tasks, "Math homework Tuesday", "g2")

        # Both resolve exactly, with no ambiguity, because the id is real.
        assert tasks._resolve("g1")["title"] == "Math homework Monday"
        assert tasks._resolve("g2")["title"] == "Math homework Tuesday"

    def test_completing_by_board_id_completes_the_right_row(self, tasks):
        self._with_google_id(tasks, "Math homework Monday", "g1")
        self._with_google_id(tasks, "Math homework Tuesday", "g2")

        tasks.complete_task("g2")

        assert [t["title"] for t in tasks.get_all()] == ["Math homework Monday"]
