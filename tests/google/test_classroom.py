import json
from datetime import datetime, timedelta, timezone

from argon.google.classroom import GetAllAssignmentsTool, classroom_due, upcoming_assignments
from argon.google.classroom_dispositions import ClassroomDispositionStore
from argon.google.service import LOCAL_TZ


class _Request:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class _Submissions:
    def __init__(self, states):
        self._states = states

    def list(self, *, courseWorkId, **_kwargs):  # noqa: N803 — API spelling
        result = self._states[courseWorkId]
        if isinstance(result, Exception):
            return _Request(error=result)
        if isinstance(result, list):
            return _Request({"studentSubmissions": result})
        return _Request({"studentSubmissions": [{"state": result}]})


class _CourseWork:
    def __init__(self, works, states):
        self._works = works
        self._submissions = _Submissions(states)

    def list(self, *, courseId, **_kwargs):  # noqa: N803 — API spelling
        return _Request({"courseWork": self._works[courseId]})

    def studentSubmissions(self):  # noqa: N802 — API spelling
        return self._submissions


class _Courses:
    def __init__(self, works, states):
        self._course_work = _CourseWork(works, states)

    def list(self, **_kwargs):
        return _Request({"courses": [{"id": "course", "name": "Physics"}]})

    def courseWork(self):  # noqa: N802 — API spelling
        return self._course_work


class _Service:
    def __init__(self, works, states):
        self._courses = _Courses(works, states)

    def courses(self):
        return self._courses


def _coursework(item_id, due):
    return {
        "id": item_id,
        "courseId": "course",
        "title": item_id,
        "state": "PUBLISHED",
        "dueDate": {"year": due.year, "month": due.month, "day": due.day},
        "dueTime": {"hours": due.hour, "minutes": due.minute},
    }


def test_classroom_due_keeps_seconds_and_nanoseconds():
    due = classroom_due(
        {
            "dueDate": {"year": 2026, "month": 8, "day": 17},
            "dueTime": {"hours": 6, "minutes": 59, "seconds": 30, "nanos": 123456000},
        }
    )

    assert due == datetime(2026, 8, 16, 23, 59, 30, 123456, tzinfo=LOCAL_TZ)


def test_date_only_classroom_due_is_exposed_as_a_date_not_a_fabricated_time():
    from argon.google.classroom import _fmt_coursework

    item = _fmt_coursework(
        {
            "id": "essay",
            "courseId": "english",
            "title": "Essay",
            "dueDate": {"year": 2026, "month": 8, "day": 17},
        }
    )

    assert item["due"] == "2026-08-17"
    assert item["due_when"] == "Mon 08/17"
    assert item["due_precision"] == "work_by_day"


def test_upcoming_assignments_filters_submitted_and_keeps_unknown_submission_visible():
    due = datetime.now(timezone.utc) + timedelta(days=1)
    works = {
        "course": [
            _coursework("open", due), _coursework("turned-in", due),
            _coursework("returned", due), _coursework("unknown", due),
        ]
    }
    service = _Service(
        works,
        {
            "open": "NEW", "turned-in": "TURNED_IN", "returned": "RETURNED",
            "unknown": RuntimeError("denied"),
        },
    )

    assignments, warnings = upcoming_assignments(service, days_ahead=7)

    assert [assignment["id"] for assignment in assignments] == ["open", "unknown"]
    assert assignments[0]["classroom_key"] == "course:open"
    assert assignments[0]["submission_state"] == "NEW"
    assert assignments[1]["submission_error"] == "RuntimeError: denied"
    assert warnings == ["Physics / unknown: RuntimeError: denied"]


def test_upcoming_assignments_marks_an_empty_submission_response_unknown():
    due = datetime.now(timezone.utc) + timedelta(days=1)
    service = _Service({"course": [_coursework("missing", due)]}, {"missing": []})

    assignments, warnings = upcoming_assignments(service, days_ahead=7)

    assert assignments[0]["submission_error"] == "No submission record returned"
    assert warnings == ["Physics / missing: No submission record returned"]


def test_get_all_assignments_honors_an_ignored_composite_assignment(tmp_path):
    due = datetime.now(timezone.utc) + timedelta(days=1)
    service = _Service({"course": [_coursework("open", due)]}, {"open": "NEW"})
    ClassroomDispositionStore(tmp_path).ignore("course:open")
    tool = GetAllAssignmentsTool(tmp_path)
    tool._svc = lambda: service

    payload = json.loads(tool._run({}))

    assert payload == {"count": 0, "assignments": []}


def test_upcoming_can_return_ignored_and_submitted_items_for_reconciliation(tmp_path):
    due = datetime.now(timezone.utc) + timedelta(days=1)
    service = _Service(
        {"course": [_coursework("ignored", due), _coursework("submitted", due)]},
        {"ignored": "NEW", "submitted": "TURNED_IN"},
    )
    dispositions = ClassroomDispositionStore(tmp_path)
    dispositions.ignore("course:ignored")

    assignments, _warnings = upcoming_assignments(
        service, days_ahead=7, dispositions=dispositions, include_suppressed=True
    )

    assert [(item["id"], item.get("suppressed_reason")) for item in assignments] == [
        ("ignored", "ignored"),
        ("submitted", "TURNED_IN"),
    ]


def test_upcoming_assignments_follows_course_and_coursework_pages():
    due = datetime.now(timezone.utc) + timedelta(days=1)

    class PagedCourseWork(_CourseWork):
        def list(self, *, courseId, pageToken=None, **_kwargs):  # noqa: N803
            pages = self._works[courseId]
            index = int(pageToken or 0)
            result = {"courseWork": pages[index]}
            if index + 1 < len(pages):
                result["nextPageToken"] = str(index + 1)
            return _Request(result)

    class PagedCourses(_Courses):
        def __init__(self):
            self._course_work = PagedCourseWork(
                {
                    "course-a": [[_coursework("first", due)], [_coursework("second", due)]],
                    "course-b": [[_coursework("third", due)]],
                },
                {"first": "NEW", "second": "NEW", "third": "NEW"},
            )

        def list(self, *, pageToken=None, **_kwargs):  # noqa: N803
            if pageToken is None:
                return _Request(
                    {"courses": [{"id": "course-a", "name": "A"}], "nextPageToken": "more"}
                )
            return _Request({"courses": [{"id": "course-b", "name": "B"}]})

    class PagedService:
        def __init__(self):
            self._courses = PagedCourses()

        def courses(self):
            return self._courses

    assignments, warnings = upcoming_assignments(PagedService(), days_ahead=7)

    assert warnings == []
    assert {item["classroom_key"] for item in assignments} == {
        "course-a:first", "course-a:second", "course-b:third"
    }
