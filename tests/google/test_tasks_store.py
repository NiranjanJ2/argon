from datetime import datetime

from argon.google import tasks_store
from argon.google.service import LOCAL_TZ


def test_google_tasks_due_is_a_local_calendar_day_not_a_utc_instant(monkeypatch):
    monkeypatch.setattr(
        tasks_store, "_now", lambda: datetime(2026, 8, 16, 12, tzinfo=LOCAL_TZ)
    )

    task = tasks_store._to_task(
        {"id": "task", "title": "Essay", "due": "2026-08-16T00:00:00.000Z"}
    )

    assert task["work_by"] == "2026-08-16"
    assert task["days_overdue"] is None


def test_google_tasks_due_becomes_overdue_on_the_following_local_day(monkeypatch):
    monkeypatch.setattr(
        tasks_store, "_now", lambda: datetime(2026, 8, 17, 12, tzinfo=LOCAL_TZ)
    )

    assert tasks_store._overdue_days("2026-08-16T00:00:00.000Z") == 1


def test_classroom_task_exposes_its_composite_identity_from_notes_metadata():
    task = tasks_store._to_task(
        {
            "id": "task",
            "title": "Essay",
            "notes": '~argon~{"s":"cl","ck":"course-a:42"}',
        }
    )

    assert task["source"] == "classroom"
    assert task["classroom_key"] == "course-a:42"


class _Request:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


def test_get_all_follows_google_tasks_pages(monkeypatch, tmp_path):
    class Tasks:
        def list(self, *, pageToken=None, **_kwargs):  # noqa: N803
            if pageToken is None:
                return _Request({"items": [{"id": "a"}], "nextPageToken": "more"})
            return _Request({"items": [{"id": "b"}]})

    class Service:
        def tasks(self):
            return Tasks()

    store = tasks_store.GoogleTasksStore(tmp_path)
    monkeypatch.setattr(store, "_svc", lambda: Service())
    monkeypatch.setattr(store, "_tl", lambda: "list")

    assert {task["id"] for task in store.get_all()} == {"a", "b"}


def test_carry_over_uses_a_date_without_inventing_work_by_time(monkeypatch, tmp_path):
    seen = {}
    store = tasks_store.GoogleTasksStore(tmp_path)
    monkeypatch.setattr(
        tasks_store, "_now", lambda: datetime(2026, 8, 16, 16, 5, tzinfo=LOCAL_TZ)
    )
    monkeypatch.setattr(
        store, "update_due", lambda _task_id, due: seen.setdefault("due", due) is not None
    )

    assert store.carry_over_task("task")
    assert seen["due"] == "2026-08-17"


def test_bulk_projection_does_not_treat_date_only_due_as_official_instant(monkeypatch, tmp_path):
    captured = []
    store = tasks_store.GoogleTasksStore(tmp_path)
    monkeypatch.setattr(store, "get_all", lambda **_kwargs: [])
    monkeypatch.setattr(store, "add_task", lambda **kwargs: captured.append(kwargs) or kwargs)

    added = store.bulk_add_from_classroom([{
        "id": "42", "courseId": "course-a", "title": "Essay",
        "dueDate": {"year": 2026, "month": 8, "day": 17},
    }])

    assert added == 1
    assert captured[0]["due"] == "2026-08-17"
    assert captured[0]["official_due"] is None


def test_legacy_bare_id_does_not_suppress_same_id_from_another_course(monkeypatch, tmp_path):
    captured = []
    store = tasks_store.GoogleTasksStore(tmp_path)
    monkeypatch.setattr(
        store,
        "get_all",
        lambda **_kwargs: [{"classroom_key": None, "classroom_id": "42", "subject": "Course A"}],
    )
    monkeypatch.setattr(store, "add_task", lambda **kwargs: captured.append(kwargs) or kwargs)

    added = store.bulk_add_from_classroom([{
        "id": "42", "courseId": "course-b", "course_name": "Course B", "title": "Lab",
        "dueDate": {"year": 2026, "month": 8, "day": 17},
    }])

    assert added == 1
    assert captured[0]["classroom_key"] == "course-b:42"
