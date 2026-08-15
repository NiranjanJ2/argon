"""The August 12–13 failures, as tests.

Each of these is something that actually happened over two days and cost trust:
Argon claimed he had been working for 83 minutes when he had not started; a
cancelled block reminded him anyway the next day; the same assignment was
written down twice; a deadline lost its time on the way into storage. They are
grouped here because they are one story, and because a unit test per function
would not have caught any of them — the bugs lived between components.
"""

from __future__ import annotations

import json

import pytest

from argon.tools.registry import ToolRegistry


class TestACronReminderCannotClaimHeStartedWorking:
    """"Chemistry has been running 83 minutes." — "I'm just starting."

    Scheduled jobs were replayed through the full agent, so the model woken by
    a reminder could call `start_task` and mutate the authoritative daily
    state. "A reminder fired" and "he began working" became the same event, and
    the fabricated session then drove follow-up nags.
    """

    def _registry(self):
        from argon.tools.tasks import (
            AddTaskTool,
            CompleteTaskTool,
            StartTaskTool,
            UpdateTaskTool,
        )

        registry = ToolRegistry()
        for tool in (
            StartTaskTool(None, None, None),
            AddTaskTool(None, None),
            CompleteTaskTool(None, None, None, None),
            UpdateTaskTool(None),
        ):
            registry.register(tool)
        return registry

    def test_a_background_turn_is_not_offered_a_single_mutating_task_tool(self):
        offered = {
            d["function"]["name"]
            for d in self._registry().get_definitions(background=True)
        }
        assert offered == set(), "an automated turn may not see task mutations at all"

    @pytest.mark.parametrize(
        "tool_name", ["start_task", "add_task", "complete_task", "update_task"]
    )
    def test_a_background_turn_calling_one_anyway_is_refused(self, tool_name):
        _tool, _params, error = self._registry().prepare_call(
            tool_name, {"task_id": "abc"}, background=True
        )
        assert error is not None
        assert "unavailable to background automation" in error

    def test_an_interactive_turn_still_has_them(self):
        offered = {
            d["function"]["name"]
            for d in self._registry().get_definitions(background=False)
        }
        assert "start_task" in offered and "complete_task" in offered


class TestCancelledWorkLosesItsReminders:
    """"No reflection today" — and the next evening it said the Reflection
    was starting now.

    Replacing the plan and retiring its automation were separate operations and
    only the first one happened. Two stale jobs once fired together and each
    started a different task.
    """

    def _cron(self, tmp_path, monkeypatch):
        from argon.services import cron as cron_mod
        from argon.services.cron import CronSchedule, CronService

        now = 1_000 * 60 * 60 * 1000
        monkeypatch.setattr(cron_mod, "_now_ms", lambda: now)
        service = CronService(tmp_path / "jobs.json")
        service.add_job(
            "Start Racism Reflection",
            CronSchedule(kind="at", at_ms=now + 3 * 60 * 60 * 1000),
            "Start Racism Reflection",
            deliver=True, channel="discord", to="1",
            delete_after_run=True, kind="reminder",
        )
        service.add_job(
            "Start Chemistry reading",
            CronSchedule(kind="at", at_ms=now + 4 * 60 * 60 * 1000),
            "Start Chemistry reading & notes",
            deliver=True, channel="discord", to="1",
            delete_after_run=True, kind="reminder",
        )
        return service

    async def test_dropping_a_block_cancels_only_that_block_s_reminder(
        self, tmp_path, monkeypatch
    ):
        from argon.productivity.plan import DayPlan
        from argon.tools.plan import SetDayPlanTool

        cron = self._cron(tmp_path, monkeypatch)
        plan = DayPlan(tmp_path)
        plan.set_blocks([
            {"start": "6pm", "what": "Racism Reflection"},
            {"start": "7pm", "what": "Chemistry reading & notes"},
        ])

        tool = SetDayPlanTool(plan, cron)
        result = await tool.execute(blocks=[{"start": "7pm", "what": "Chemistry reading & notes"}])

        assert "Racism Reflection" in result, "he must be told what was dropped"
        assert "Cancelled the reminder" in result
        remaining = [j.payload.message for j in cron.list_jobs()]
        assert remaining == ["Start Chemistry reading & notes"], (
            "the reflection reminder is gone and chemistry's is untouched"
        )

    async def test_clearing_the_plan_retires_all_of_its_reminders(
        self, tmp_path, monkeypatch
    ):
        from argon.productivity.plan import DayPlan
        from argon.tools.plan import SetDayPlanTool

        cron = self._cron(tmp_path, monkeypatch)
        plan = DayPlan(tmp_path)
        plan.set_blocks([
            {"start": "6pm", "what": "Racism Reflection"},
            {"start": "7pm", "what": "Chemistry reading & notes"},
        ])

        result = await SetDayPlanTool(plan, cron).execute(blocks=[])

        assert "Plan cleared" in result
        assert cron.list_jobs() == []

    async def test_removing_one_block_by_id_retires_its_reminder(
        self, tmp_path, monkeypatch
    ):
        from argon.productivity.plan import DayPlan
        from argon.tools.plan import UpdatePlanBlockTool

        cron = self._cron(tmp_path, monkeypatch)
        plan = DayPlan(tmp_path)
        stored = plan.set_blocks([{"start": "6pm", "what": "Racism Reflection"}])

        result = await UpdatePlanBlockTool(plan, cron).execute(
            action="remove", block_id=stored[0].id
        )

        assert "cancelled its reminder" in result.lower()
        assert [j.payload.message for j in cron.list_jobs()] == [
            "Start Chemistry reading & notes"
        ], "only the removed block's reminder goes"

    async def test_an_unrelated_reminder_is_never_cancelled(self, tmp_path, monkeypatch):
        """Cancelling the wrong reminder is as bad as keeping a stale one."""
        from argon.productivity.plan import DayPlan
        from argon.tools.plan import SetDayPlanTool

        cron = self._cron(tmp_path, monkeypatch)
        plan = DayPlan(tmp_path)
        plan.set_blocks([{"start": "9pm", "what": "Gym"}])

        await SetDayPlanTool(plan, cron).execute(
            blocks=[{"start": "10pm", "what": "Reading"}]
        )

        assert len(cron.list_jobs()) == 2, "dropping 'Gym' touches neither job"


class TestTheSameThingIsNotWrittenDownTwice:
    """"Chemistry reading & notes" was added twice in one conversation.

    `add_task` had no idempotency, so two rows existed for one piece of work and
    each was counted, reminded on and reported separately.
    """

    class _Store:
        def __init__(self):
            self.inserted = []
            self._tasks = []

        def get_all(self, *, include_done=False):
            return list(self._tasks)

        def _svc(self):
            raise AssertionError("must not reach Google when a duplicate is found")

        def _tl(self):
            return "list"

    def _store_with(self, existing):
        from argon.google.tasks_store import GoogleTasksStore

        store = GoogleTasksStore.__new__(GoogleTasksStore)
        store.get_all = lambda **_kw: existing
        return store

    def test_an_identical_title_is_not_added_again(self):
        existing = [{
            "id": "t1", "title": "Chemistry reading & notes", "due_when": "Fri 08/14",
            "classroom_key": None, "classroom_id": None,
        }]
        store = self._store_with(existing)

        assert store._existing_match("Chemistry reading & notes")["id"] == "t1"
        assert store._existing_match("  chemistry   READING & notes ")["id"] == "t1", (
            "case and spacing are not a different task"
        )

    def test_a_classroom_key_matches_even_when_the_title_was_reworded(self):
        existing = [{
            "id": "t1", "title": "Machado HW 1",
            "classroom_key": "871774268160:874448544472", "classroom_id": None,
        }]
        store = self._store_with(existing)

        found = store._existing_match("HW 1", classroom_key="871774268160:874448544472")
        assert found["id"] == "t1"

    def test_genuinely_different_work_is_still_added(self):
        existing = [{
            "id": "t1", "title": "Chemistry reading & notes",
            "classroom_key": None, "classroom_id": None,
        }]
        store = self._store_with(existing)

        assert store._existing_match("Math Summer Assignment") is None

    def test_a_read_failure_does_not_block_the_add(self):
        from argon.google.tasks_store import GoogleTasksStore

        store = GoogleTasksStore.__new__(GoogleTasksStore)

        def boom(**_kw):
            raise RuntimeError("tasks unreachable")

        store.get_all = boom
        assert store._existing_match("anything") is None


class TestADeadlineKeepsItsTime:
    """"Stored for Thursday 8 PM" — Google Tasks keeps only the date.

    The time was preserved in Argon's own metadata for `manual` tasks and
    silently dropped for everything else, so the assistant reported a precision
    the record did not hold.
    """

    @pytest.mark.parametrize("source", ["manual", "classroom"])
    def test_the_time_survives_whatever_the_source(self, source, monkeypatch):
        from argon.google.tasks_store import GoogleTasksStore, _decode_meta

        captured = {}

        class _Insert:
            def __init__(self, body):
                self._body = body

            def execute(self):
                captured.update(self._body)
                return {"id": "new", "title": self._body["title"],
                        "due": self._body.get("due"), "notes": self._body.get("notes")}

        class _Tasks:
            def insert(self, *, tasklist, body):
                return _Insert(body)

        store = GoogleTasksStore.__new__(GoogleTasksStore)
        store.get_all = lambda **_kw: []
        store._svc = lambda: type("S", (), {"tasks": lambda _s: _Tasks()})()
        store._tl = lambda: "list"

        store.add_task("Machado HW 1", source=source, due="2026-08-13T20:00:00-07:00")

        meta, _ = _decode_meta(captured["notes"])
        assert meta.get("wb", "").startswith("2026-08-13T20:00"), (
            "the work-by time must be retained even though Google stores a date"
        )
        assert captured["due"].startswith("2026-08-13T00:00:00"), (
            "and Google still gets the date-only stamp it requires"
        )


class TestAskingIsNotChanging:
    """"What's the board looking like?" rewrote his whole day."""

    def test_every_read_tool_is_marked_read_only(self):
        from argon.productivity.state import DailyState
        from argon.tools.overview import GetDailyOverviewTool
        from argon.tools.plan import GetDayPlanTool
        from argon.tools.status import GetStatusTool
        from argon.tools.tasks import ListTasksTool

        readers = [
            GetDailyOverviewTool("/tmp"),
            GetDayPlanTool(None),
            ListTasksTool(None, DailyState("/tmp"), "/tmp"),
            GetStatusTool(DailyState("/tmp"), "/tmp"),
        ]
        for tool in readers:
            assert tool.read_only is True, f"{tool.name} must not be able to change anything"

    async def test_reading_the_plan_does_not_write_a_version(self, tmp_path):
        from argon.core import store
        from argon.productivity.plan import PLAN_DOC, DayPlan
        from argon.tools.plan import GetDayPlanTool

        plan = DayPlan(tmp_path)
        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        before = store.doc_version(PLAN_DOC)

        json.loads(await GetDayPlanTool(plan).execute())

        assert store.doc_version(PLAN_DOC) == before, "a question must leave no trace"
