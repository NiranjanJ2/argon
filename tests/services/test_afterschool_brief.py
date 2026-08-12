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


class TestTheBriefCarriesClassroom:
    def test_assignments_are_in_the_prompt(self, service, monkeypatch):
        monkeypatch.setattr(agenda, "schoolwork", lambda ws, **k: [
            {"title": "Racism Reflection", "course": "APUSH PM",
             "due": "2026-08-14T23:59:00-07:00", "due_when": "Fri 08/14, 11:59 PM",
             "days_left": 2},
        ])
        prompt = service.build_prompt(OCCASIONS["plan_request"])

        assert "Due from Google Classroom" in prompt
        assert "Racism Reflection" in prompt and "APUSH PM" in prompt

    def test_an_empty_classroom_says_so(self, service, monkeypatch):
        monkeypatch.setattr(agenda, "schoolwork", lambda ws, **k: [])
        assert "nothing due from Classroom" in service.build_prompt(
            OCCASIONS["plan_request"])

    def test_school_auth_failing_does_not_blank_the_brief(self, service, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("school account needs re-authentication")

        monkeypatch.setattr(agenda, "schoolwork", boom)
        prompt = service.build_prompt(OCCASIONS["plan_request"])
        assert "Classroom unavailable" in prompt
        assert "AFTER-SCHOOL BRIEF" in prompt

    def test_overdue_work_is_stated_not_left_to_a_tool_call(self, service, monkeypatch):
        monkeypatch.setattr(agenda, "schoolwork", lambda ws, **k: [])
        monkeypatch.setattr(
            "argon.google.tasks_store.GoogleTasksStore.get_all",
            lambda self, **k: [
                {"id": "a", "title": "UCLA work", "days_overdue": 7},
                {"id": "b", "title": "SAT prep", "days_overdue": None},
            ],
        )
        prompt = service.build_prompt(OCCASIONS["plan_request"])

        assert "UCLA work — 7 days past due" in prompt
        assert "SAT prep" not in prompt.split("Past due")[1].split("\n\n")[0]

    def test_it_asks_for_one_question_not_a_list(self, service, monkeypatch):
        monkeypatch.setattr(agenda, "schoolwork", lambda ws, **k: [])
        prompt = service.build_prompt(OCCASIONS["plan_request"])
        assert "at most two or three things" in prompt
        assert "one plain question" in prompt


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
