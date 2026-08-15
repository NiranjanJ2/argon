"""4 PM is the message the tool exists for.

He gets home from school at four; the evening — after-school meetings, homework,
clubs — is the part of the day he actually runs. Argon could always read Google
Classroom and never did so unprompted: the prompt suggested calling a tool and
the model mostly skipped it. The brief fetches it now instead of hoping.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from argon.productivity import state as state_mod
from argon.services import agenda
from argon.services import reminder as reminder_mod
from argon.services.agenda import _one_per_thing, describe_assignment
from argon.services.reminder import OCCASIONS, ReminderService

LA = ZoneInfo("America/Los_Angeles")


def _at(hour, minute=0):
    return datetime(2026, 8, 12, hour, minute, tzinfo=LA)


async def _silent(_p):
    return ""


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    clock = lambda: _at(16, 5)  # noqa: E731
    monkeypatch.setattr(state_mod, "_now", clock)
    monkeypatch.setattr(state_mod, "_today_key", lambda *a, **k: "2026-08-12")
    monkeypatch.setattr(reminder_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    monkeypatch.setattr(reminder_mod.clock, "now", clock)
    svc = ReminderService(tmp_path, "America/Los_Angeles", _silent, unprompted_from_hour=16)
    monkeypatch.setattr(svc, "_now", clock)
    monkeypatch.setattr(svc, "_pending_event", lambda: None)
    monkeypatch.setattr(svc, "_agenda_lines", lambda: "- nothing else scheduled")
    return svc


def _sources(monkeypatch, assignments=(), tasks=(), classroom_error=None, warnings=()):
    """Point the brief's board at fixed snapshots.

    The brief reads the canonical board now. It used to call
    `agenda.schoolwork`, which swallows its own failure and returns `[]` — so a
    stale school token printed "nothing due from Classroom" into the one
    message this whole feature exists to send.
    """
    from argon.commitments import SourceSnapshot

    monkeypatch.setattr(
        "argon.commitments.classroom_snapshot",
        lambda *a, **k: SourceSnapshot("classroom", tuple(assignments),
                                       classroom_error, tuple(warnings)),
    )
    monkeypatch.setattr(
        "argon.commitments.tasks_snapshot",
        lambda *a, **k: SourceSnapshot("tasks", tuple(tasks), None, ()),
    )


class TestTheBriefCarriesClassroom:
    def test_assignments_are_in_the_prompt(self, service, monkeypatch):
        _sources(monkeypatch, assignments=[
            {"classroom_key": "apush:reflection", "title": "Racism Reflection",
             "course_name": "APUSH PM", "due": "2026-08-14T23:59:00-07:00",
             "due_when": "Fri 08/14, 11:59 PM"},
        ])
        prompt = service.build_prompt(OCCASIONS["daily_brief"])

        assert "Due from Google Classroom" in prompt
        assert "Racism Reflection" in prompt and "APUSH PM" in prompt

    def test_an_empty_classroom_says_so(self, service, monkeypatch):
        _sources(monkeypatch)
        assert "nothing due from Classroom" in service.build_prompt(
            OCCASIONS["daily_brief"])

    def test_school_auth_failing_never_reads_as_nothing_due(self, service, monkeypatch):
        """The bug: an outage and a clear plate produced the same sentence."""
        _sources(monkeypatch, classroom_error="school account needs re-authentication")

        prompt = service.build_prompt(OCCASIONS["daily_brief"])

        assert "Google Classroom unavailable" in prompt
        assert "needs re-authentication" in prompt
        assert "nothing due from Classroom" not in prompt, (
            "an unread Classroom is not an empty one"
        )
        assert "AFTER-SCHOOL BRIEF" in prompt

    def test_a_partly_read_classroom_names_what_it_could_not_see(
        self, service, monkeypatch
    ):
        _sources(monkeypatch, warnings=["Locked course: forbidden"])
        prompt = service.build_prompt(OCCASIONS["daily_brief"])
        assert "Locked course: forbidden" in prompt

    def test_overdue_work_is_stated_not_left_to_a_tool_call(self, service, monkeypatch):
        _sources(monkeypatch, tasks=[
            {"id": "a", "title": "UCLA work", "days_overdue": 7, "source": "manual"},
            {"id": "b", "title": "SAT prep", "days_overdue": None, "source": "manual"},
        ])
        prompt = service.build_prompt(OCCASIONS["daily_brief"])

        assert "UCLA work — 7 days past due" in prompt
        assert "SAT prep" not in prompt.split("Past due")[1].split("\n\n")[0]

    def test_it_is_a_one_way_secretary_brief_not_a_planning_request(
        self, service, monkeypatch
    ):
        monkeypatch.setattr(agenda, "schoolwork", lambda ws, **k: [])
        prompt = service.build_prompt(OCCASIONS["daily_brief"])

        assert "after-school brief" in prompt.lower()
        assert "deadline order" in prompt.lower()
        # It reports; it does not run his evening. These are the shapes that
        # turned a brief into a planning interview.
        assert "set_day_plan" not in prompt
        assert "hardest" not in prompt.lower()
        assert "what is he doing with the evening" not in prompt.lower()
        assert "he decides when he works" in prompt.lower()

    def test_it_does_not_turn_habits_or_old_plans_into_today_suggestions(
        self, service, monkeypatch
    ):
        monkeypatch.setattr(agenda, "schoolwork", lambda ws, **k: [])
        monkeypatch.setattr(
            "argon.productivity.history.PlanHistory.summary",
            lambda self: "Robotics usually starts at 5 PM",
        )
        monkeypatch.setattr(
            "argon.productivity.habits.HabitsTracker.get_summary",
            lambda self: {"completion_rate": 0.5, "typical_work_start": "17:00"},
        )

        prompt = service.build_prompt(OCCASIONS["daily_brief"])

        assert "Robotics usually starts" not in prompt
        assert "finishes about" not in prompt
        assert "usually starts" not in prompt

    def test_schoolwork_passes_durable_dispositions_to_classroom(self, tmp_path, monkeypatch):
        seen = {}
        monkeypatch.setattr("argon.google.service.build_google_service", lambda *args: object())

        def upcoming(_service, *, days_ahead, dispositions):
            seen["days_ahead"] = days_ahead
            seen["dispositions"] = dispositions
            return [], []

        monkeypatch.setattr("argon.google.classroom.upcoming_assignments", upcoming)

        assert agenda._fetch_schoolwork(tmp_path, 10) == []
        assert seen["days_ahead"] == 10
        assert not seen["dispositions"].is_ignored("course:assignment")

    def test_partial_classroom_failure_is_visible_in_schoolwork(self, tmp_path, monkeypatch):
        monkeypatch.setattr("argon.google.service.build_google_service", lambda *args: object())
        monkeypatch.setattr(
            "argon.google.classroom.upcoming_assignments",
            lambda *_args, **_kwargs: ([{
                "title": f"Assignment {index}", "course_name": "English",
                "due": "2026-08-14", "due_precision": "work_by_day",
            } for index in range(6)], ["Locked course: forbidden"]),
        )

        work = agenda._fetch_schoolwork(tmp_path, 10)

        assert agenda.describe_assignment(work[0]) == "Classroom incomplete: Locked course: forbidden"

    def test_submission_failure_is_visible_in_schoolwork(self, tmp_path, monkeypatch):
        monkeypatch.setattr("argon.google.service.build_google_service", lambda *args: object())
        monkeypatch.setattr(
            "argon.google.classroom.upcoming_assignments",
            lambda *_args, **_kwargs: ([{
                "title": "Essay", "course_name": "English", "due": "2026-08-14",
                "due_precision": "work_by_day", "submission_error": "forbidden",
            }], []),
        )

        work = agenda._fetch_schoolwork(tmp_path, 10)

        assert "submission status unavailable: forbidden" in agenda.describe_assignment(work[0])
        assert work[0]["due_when"] == "Fri 08/14"


class TestOneAssignmentPostedTwice:
    def test_the_real_duplicate_collapses(self):
        """His teacher posted the same work as both titles, one course, one time."""
        items = [
            {"title": "Math Analysis Summer Assignment", "course": "Math An",
             "due": "2026-08-16T20:00:00-07:00"},
            {"title": "Math An Summer Assignment", "course": "Math An",
             "due": "2026-08-16T20:00:00-07:00"},
        ]
        kept = _one_per_thing(items)

        assert len(kept) == 1
        assert kept[0]["title"] == "Math Analysis Summer Assignment"  # fuller reads better

    def test_two_real_assignments_due_together_both_survive(self):
        items = [
            {"title": "Racism Reflection", "course": "APUSH", "due": "2026-08-14T23:59:00-07:00"},
            {"title": "Chapter 3 Quiz", "course": "APUSH", "due": "2026-08-14T23:59:00-07:00"},
        ]
        assert len(_one_per_thing(items)) == 2

    def test_the_same_title_in_a_different_course_survives(self):
        items = [
            {"title": "Summer Assignment", "course": "APUSH", "due": "2026-08-16T20:00:00-07:00"},
            {"title": "Summer Assignment", "course": "Math An", "due": "2026-08-16T20:00:00-07:00"},
        ]
        assert len(_one_per_thing(items)) == 2

    def test_the_same_title_at_a_different_deadline_survives(self):
        items = [
            {"title": "Weekly Reading", "course": "APUSH", "due": "2026-08-14T23:59:00-07:00"},
            {"title": "Weekly Reading", "course": "APUSH", "due": "2026-08-21T23:59:00-07:00"},
        ]
        assert len(_one_per_thing(items)) == 2


class TestRunway:
    def test_it_says_how_long_is_left(self):
        base = {"title": "X", "course": "", "due_when": "Fri 08/14"}
        assert "due today" in describe_assignment({**base, "days_left": 0})
        assert "due tomorrow" in describe_assignment({**base, "days_left": 1})
        assert "3 days" in describe_assignment({**base, "days_left": 3})
