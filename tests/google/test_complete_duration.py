"""Completing a forgotten task must not invent a duration.

"SAT prep - English" was started at 1:42am on 2026-08-02 and completed at
11:42pm on 2026-08-04. ``complete_task`` did the subtraction and recorded
**2921 minutes** of English study, which went straight into the daily log and
into the habits tracker's per-subject average, where it stays forever.
"""

from __future__ import annotations

from datetime import timedelta

from argon.google.service import LOCAL_TZ
from argon.google.tasks_store import STALE_START_HOURS, GoogleTasksStore, _encode_meta, _now


class _FakeTasks:
    """Just enough Google Tasks to complete one task."""

    def __init__(self, task: dict) -> None:
        self.task = task
        self.patched: dict = {}

    def get(self, **_kw):
        return _Exec(self.task)

    def patch(self, *, body, **_kw):
        self.patched = body
        merged = dict(self.task)
        merged.update(body)
        return _Exec(merged)


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class _FakeService:
    def __init__(self, tasks):
        self._tasks = tasks

    def tasks(self):
        return self._tasks


def _store_with_start(tmp_path, hours_ago: float) -> tuple[GoogleTasksStore, _FakeTasks]:
    started = (_now() - timedelta(hours=hours_ago)).isoformat()
    task = {
        "id": "abc",
        "title": "SAT prep - English",
        "status": "needsAction",
        "notes": _encode_meta({"sat": started, "sub": "English"}, ""),
    }
    tasks = _FakeTasks(task)
    store = GoogleTasksStore(tmp_path)
    store._service = _FakeService(tasks)
    store._tl_id = "list1"
    return store, tasks


def test_a_normal_session_records_its_duration(tmp_path):
    store, _ = _store_with_start(tmp_path, 1.5)
    assert store.complete_task("abc")["time_actual_min"] == 90


def test_a_forgotten_start_records_no_duration(tmp_path):
    """None is honest. 2921 poisons the subject average it lands in."""
    store, tasks = _store_with_start(tmp_path, 46)
    assert store.complete_task("abc")["time_actual_min"] is None
    assert "act" not in tasks.patched.get("notes", "")


def test_the_task_still_completes(tmp_path):
    """Refusing the duration must not refuse the completion."""
    store, tasks = _store_with_start(tmp_path, 46)
    assert store.complete_task("abc") is not None
    assert tasks.patched["status"] == "completed"


def test_the_threshold_is_the_boundary(tmp_path):
    under, _ = _store_with_start(tmp_path, STALE_START_HOURS - 0.5)
    over, _ = _store_with_start(tmp_path, STALE_START_HOURS + 0.5)
    assert under.complete_task("abc")["time_actual_min"] is not None
    assert over.complete_task("abc")["time_actual_min"] is None


def test_the_start_stamp_is_cleared_either_way(tmp_path):
    """Left in place, the next start would inherit the stale timestamp."""
    store, tasks = _store_with_start(tmp_path, 46)
    store.complete_task("abc")
    assert "sat" not in tasks.patched.get("notes", "")
