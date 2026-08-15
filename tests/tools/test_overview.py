"""One board, four consumers, one answer.

Reconciliation used to live inside `get_daily_overview`'s presentation code, so
it was the only place that knew an assignment had been turned in or ignored.
`list_tasks`, the 4 PM brief's overdue lines and the iOS widget all read raw
Google Tasks — and an assignment he had submitted disappeared from the board
while still being announced as pending everywhere else. The tests that matter
here are the ones that check the *same fixture* through all four.
"""

from __future__ import annotations

import json

from argon.commitments import SourceSnapshot, build_board
from argon.tools.overview import GetDailyOverviewTool


def _snapshots(assignments=(), tasks=(), classroom_error=None, classroom_warnings=()):
    return (
        SourceSnapshot("classroom", tuple(assignments), classroom_error,
                       tuple(classroom_warnings)),
        SourceSnapshot("tasks", tuple(tasks), None, ()),
    )


def _assignment(**kw):
    base = {
        "classroom_key": "physics:lab", "title": "Lab report",
        "course_name": "Physics", "due": "2026-08-14", "due_when": "Fri 08/14",
    }
    return {**base, **kw}


# -- the join ---------------------------------------------------------------


def test_a_legacy_classroom_task_is_an_overlay_not_a_second_row():
    """Two rows for one assignment is the duplicate he had to reconcile by hand.

    The copied Google Task carries the one fact Classroom cannot know — the
    earlier date he chose to actually do it — so it contributes that and emits
    no row of its own.
    """
    board = build_board(*_snapshots(
        assignments=[_assignment()],
        tasks=[
            {"id": "t1", "title": "Lab report", "source": "classroom",
             "classroom_key": "physics:lab", "work_by": "2026-08-13",
             "due_when": "Thu 08/13"},
            {"id": "t2", "title": "Lab report", "source": "manual",
             "due": "2026-08-14", "due_when": "Fri 08/14"},
        ],
    ))

    assert [c.title for c in board.commitments] == ["Lab report", "Lab report"]
    assignment = next(c for c in board.commitments if c.origin == "classroom")
    assert assignment.official_due == "2026-08-14", "the school's deadline"
    assert assignment.work_by == "2026-08-13", "and his own earlier plan, kept apart"
    assert assignment.google_task_id == "t1"
    manual = next(c for c in board.commitments if c.origin == "task")
    assert manual.source == "manual", "a real manual task of the same name survives"


def test_a_turned_in_assignment_is_absent_and_takes_its_overlay_with_it():
    board = build_board(*_snapshots(
        assignments=[_assignment(submission_state="TURNED_IN",
                                 suppressed_reason="turned in")],
        tasks=[{"id": "t1", "title": "Lab report", "source": "classroom",
                "classroom_key": "physics:lab"}],
    ))

    assert board.commitments == ()
    assert [c.title for c in board.suppressed] == ["Lab report"]


def test_an_ignored_assignment_is_absent_and_takes_its_overlay_with_it():
    board = build_board(*_snapshots(
        assignments=[_assignment(suppressed_reason="ignored")],
        tasks=[{"id": "t1", "title": "Lab report", "source": "classroom",
                "classroom_key": "physics:lab"}],
    ))

    assert board.commitments == ()


def test_a_legacy_task_matches_on_title_only_when_exactly_one_assignment_answers():
    """Guessing here hides real work behind an unrelated assignment."""
    board = build_board(*_snapshots(
        assignments=[
            _assignment(classroom_key="a:quiz", title="Weekly Quiz", course_name="A"),
            _assignment(classroom_key="b:quiz", title="weekly-quiz", course_name="B"),
        ],
        tasks=[{"id": "t1", "title": "Weekly Quiz", "source": "classroom",
                "work_by": "2026-08-13"}],
    ))

    assert len(board.commitments) == 3, "ambiguous: the task stays as its own row"
    assert all(c.work_by is None for c in board.commitments if c.origin == "classroom")


def test_a_task_is_never_lost_when_classroom_is_down():
    board = build_board(*_snapshots(
        tasks=[{"id": "t1", "title": "Chemistry", "source": "manual"}],
        classroom_error="school account needs re-authentication",
    ))

    assert [c.title for c in board.commitments] == ["Chemistry"]
    assert board.complete is False
    assert board.health_lines() == [
        "Unavailable: Classroom — school account needs re-authentication"
    ]


def test_a_partial_classroom_read_is_incomplete_not_empty():
    board = build_board(*_snapshots(classroom_warnings=["Locked course: forbidden"]))

    assert board.complete is False
    assert board.health_lines() == ["Classroom incomplete: Locked course: forbidden"]


# -- the four consumers, one fixture ----------------------------------------


SUBMITTED = [_assignment(title="Submitted lab", classroom_key="science:lab",
                         submission_state="TURNED_IN", suppressed_reason="turned in")]
SUBMITTED_TASKS = [
    {"id": "t1", "title": "Submitted lab", "source": "classroom",
     "classroom_key": "science:lab", "days_overdue": 3},
    {"id": "t2", "title": "Chemistry", "source": "manual", "days_overdue": 2,
     "due": "2026-08-11", "due_when": "Tue 08/11"},
]


def _patch_sources(monkeypatch, assignments, tasks, classroom_error=None):
    """Point every consumer at the same two snapshots."""
    classroom, task_snap = _snapshots(assignments, tasks, classroom_error)
    monkeypatch.setattr("argon.commitments.classroom_snapshot",
                        lambda *a, **k: classroom)
    monkeypatch.setattr("argon.commitments.tasks_snapshot", lambda *a, **k: task_snap)


def test_a_turned_in_assignment_is_absent_from_the_overview(monkeypatch, tmp_path):
    _patch_sources(monkeypatch, SUBMITTED, SUBMITTED_TASKS)
    from argon.commitments import load_board

    rendered = GetDailyOverviewTool._board(load_board(tmp_path), [])

    assert "Submitted lab" not in rendered["text"]
    assert "Chemistry" in rendered["text"]


async def test_a_turned_in_assignment_is_absent_from_list_tasks(monkeypatch, tmp_path):
    from argon.productivity.state import DailyState
    from argon.tools.tasks import ListTasksTool

    _patch_sources(monkeypatch, SUBMITTED, SUBMITTED_TASKS)
    monkeypatch.setattr("argon.services.agenda.upcoming", lambda *a, **k: [])

    result = json.loads(await ListTasksTool(object(), DailyState(tmp_path), tmp_path).execute())

    titles = [c["title"] for c in result["commitments"]]
    assert titles == ["Chemistry"]
    assert result["complete"] is True


def test_a_turned_in_assignment_is_absent_from_the_overdue_brief(monkeypatch, tmp_path):
    from argon.services.reminder import ReminderService

    _patch_sources(monkeypatch, SUBMITTED, SUBMITTED_TASKS)

    async def _noop(_prompt):
        return ""

    service = ReminderService(tmp_path, "America/Los_Angeles", _noop)
    lines = service._overdue_lines()

    assert "Submitted lab" not in lines
    assert "Chemistry — 2 days past due" in lines


def test_a_turned_in_assignment_is_absent_from_the_widget_payload(monkeypatch, tmp_path):
    from argon.api import server
    from argon.productivity.state import DailyState

    _patch_sources(monkeypatch, SUBMITTED, SUBMITTED_TASKS)
    monkeypatch.setattr(server, "_rt", server.RuntimeRef() if hasattr(server, "RuntimeRef") else server._rt)
    monkeypatch.setattr(server, "_cached_tasks", lambda *a, **k: (SUBMITTED_TASKS, {}))
    monkeypatch.setattr(
        "argon.commitments.classroom_snapshot",
        lambda *a, **k: _snapshots(SUBMITTED, SUBMITTED_TASKS)[0],
    )

    payload = server._task_dashboard(object(), DailyState(tmp_path))

    titles = [t["title"] for t in payload["tasks"]]
    assert titles == ["Chemistry"]
    assert payload["complete"] is True


def test_a_classroom_outage_is_visible_in_every_consumer(monkeypatch, tmp_path):
    """A short board caused by an outage must never read as a free evening."""
    from argon.commitments import load_board
    from argon.productivity.state import DailyState
    from argon.services.reminder import ReminderService
    from argon.tools.tasks import ListTasksTool

    _patch_sources(monkeypatch, [], SUBMITTED_TASKS[1:], classroom_error="needs re-auth")
    monkeypatch.setattr("argon.services.agenda.upcoming", lambda *a, **k: [])

    board = load_board(tmp_path)
    assert board.complete is False

    rendered = GetDailyOverviewTool._board(board, [])
    assert "Unavailable: Classroom — needs re-auth" in rendered["text"]
    assert rendered["complete"] is False

    async def _noop(_prompt):
        return ""

    service = ReminderService(tmp_path, "America/Los_Angeles", _noop)
    # The outage is stated in the brief itself rather than muting it: what
    # Google Tasks did return is still real material.
    assert "Unavailable: Classroom" in service._overdue_lines()
    assert service.pending_task_count() == 1

    import asyncio

    listed = json.loads(
        asyncio.run(ListTasksTool(object(), DailyState(tmp_path), tmp_path).execute())
    )
    assert listed["complete"] is False


# -- rendering --------------------------------------------------------------


def test_the_rendered_board_states_both_dates_and_an_unreadable_submission():
    board = build_board(*_snapshots(
        assignments=[_assignment(submission_error="HttpError: forbidden")],
        tasks=[{"id": "t1", "title": "Lab report", "source": "classroom",
                "classroom_key": "physics:lab", "work_by": "2026-08-13",
                "due_when": "Thu 08/13"}],
    ))

    rendered = GetDailyOverviewTool._board(board, [])

    assert "personal work-by Thu 08/13" in rendered["text"]
    assert "submission status unavailable: HttpError: forbidden" in rendered["text"]
    assert rendered["counts"] == {"assignments": 1, "tasks": 0, "events": 0, "errors": 0}


def test_a_dead_calendar_is_stated_rather_than_rendered_as_an_empty_day():
    board = build_board(*_snapshots())

    rendered = GetDailyOverviewTool._board(board, {"error": "calendar is unreachable"})

    assert "Unavailable: Calendar — calendar is unreachable" in rendered["text"]
    assert rendered["complete"] is False
    assert rendered["counts"]["errors"] == 1


def test_overview_passes_its_durable_dispositions_to_classroom(monkeypatch, tmp_path):
    from argon import commitments

    commitments.invalidate()
    seen = {}
    monkeypatch.setattr("argon.google.service.build_google_service", lambda *args: object())

    def upcoming(_service, *, days_ahead, dispositions, include_suppressed):
        seen["days_ahead"] = days_ahead
        seen["dispositions"] = dispositions
        seen["include_suppressed"] = include_suppressed
        return [], []

    monkeypatch.setattr("argon.google.classroom.upcoming_assignments", upcoming)

    commitments.classroom_snapshot(tmp_path, days_ahead=7, fresh=True)

    assert seen["days_ahead"] == 7
    assert seen["include_suppressed"] is True, "suppressed items are needed to drop overlays"
    assert seen["dispositions"].is_ignored("course:assignment") is False
