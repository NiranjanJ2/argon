"""A task with a time set aside for it is not outstanding work.

At noon, with "Math homework" open and a 3 PM reminder Argon had itself
created, the check-in gate counted the task as "free time, with things
outstanding" and told him to start it — arguing with a plan he had made two
hours earlier. Deciding when to do something is doing something about it.
"""

from __future__ import annotations

from datetime import timedelta

from argon import clock
from argon.tools.tasks import mark_scheduled, unscheduled


def _entry(summary, minutes_out=180):
    return {"summary": summary, "start": clock.now() + timedelta(minutes=minutes_out)}


class TestMarkScheduled:
    def test_a_reminder_naming_the_task_stamps_it(self):
        tasks = [{"id": "a", "title": "Math homework"}]
        out = mark_scheduled(tasks, [_entry("Start Math homework")])
        assert out[0]["scheduled_for"]

    def test_an_unrelated_reminder_does_not(self):
        tasks = [{"id": "a", "title": "Math homework"}]
        out = mark_scheduled(tasks, [_entry("Dentist")])
        assert "scheduled_for" not in out[0]

    def test_the_time_is_given_in_a_form_he_reads(self):
        tasks = [{"id": "a", "title": "Math homework"}]
        at = clock.now().replace(hour=15, minute=0)
        out = mark_scheduled(tasks, [{"summary": "Start Math homework", "start": at}])
        assert out[0]["scheduled_for"] == "3:00 PM"

    def test_matching_is_task_title_inside_reminder_not_the_reverse(self):
        """A task called "Prep" must not claim every reminder mentioning prep."""
        tasks = [{"id": "a", "title": "Start Math homework and then some"}]
        assert "scheduled_for" not in mark_scheduled(tasks, [_entry("Math")])[0]

    def test_an_empty_title_matches_nothing(self):
        assert "scheduled_for" not in mark_scheduled(
            [{"id": "a", "title": ""}], [_entry("Start Math homework")]
        )[0]

    def test_the_original_list_is_not_mutated(self):
        tasks = [{"id": "a", "title": "Math homework"}]
        mark_scheduled(tasks, [_entry("Start Math homework")])
        assert "scheduled_for" not in tasks[0]


class TestUnscheduled:
    def test_a_scheduled_task_is_not_outstanding(self):
        tasks = mark_scheduled(
            [{"id": "a", "title": "Math homework"}], [_entry("Start Math homework")]
        )
        assert unscheduled(tasks) == []

    def test_a_running_task_is_not_outstanding_either(self):
        """It is being done right now; there is nothing to nudge about."""
        assert unscheduled([{"id": "a", "title": "X", "running": True}]) == []

    def test_genuinely_untouched_work_still_counts(self):
        tasks = [
            {"id": "a", "title": "Math homework"},
            {"id": "b", "title": "SAT prep"},
        ]
        remaining = unscheduled(mark_scheduled(tasks, [_entry("Start Math homework")]))
        assert [t["title"] for t in remaining] == ["SAT prep"]


class TestTheGateGoesQuiet:
    def test_a_scheduled_commitment_is_material_for_the_secretary_brief(
        self, tmp_path, monkeypatch
    ):
        from argon.services import agenda
        from argon.services.reminder import ReminderService

        async def _never(_p):  # pragma: no cover
            raise AssertionError("no model call should happen")

        svc = ReminderService(tmp_path, "America/Los_Angeles", on_check_in=_never)
        monkeypatch.setattr(
            "argon.tasks.local_store.LocalTaskStore.get_all",
            lambda self, **kw: [{"id": "a", "title": "Math homework"}],
        )
        monkeypatch.setattr(agenda, "upcoming", lambda ws: [_entry("Start Math homework")])

        assert svc.pending_task_count() == 0
        assert svc.has_material() is True

    def test_unscheduled_work_still_earns_one(self, tmp_path, monkeypatch):
        from argon.services import agenda
        from argon.services.reminder import ReminderService

        async def _never(_p):  # pragma: no cover
            raise AssertionError("no model call should happen")

        svc = ReminderService(tmp_path, "America/Los_Angeles", on_check_in=_never)
        monkeypatch.setattr(
            "argon.tasks.local_store.LocalTaskStore.get_all",
            lambda self, **kw: [{"id": "b", "title": "SAT prep"}],
        )
        monkeypatch.setattr(agenda, "upcoming", lambda ws: [_entry("Start Math homework")])

        assert svc.pending_task_count() == 1
        assert svc.has_material() is True

    def test_a_calendar_outage_does_not_invent_free_time(self, tmp_path, monkeypatch):
        """Failing to read the calendar must not silently un-schedule his day."""
        from argon.services import agenda
        from argon.services.reminder import ReminderService

        async def _never(_p):  # pragma: no cover
            raise AssertionError("no model call should happen")

        svc = ReminderService(tmp_path, "America/Los_Angeles", on_check_in=_never)
        monkeypatch.setattr(
            "argon.tasks.local_store.LocalTaskStore.get_all",
            lambda self, **kw: [{"id": "a", "title": "Math homework"}],
        )

        def boom(_ws):
            raise RuntimeError("calendar down")

        monkeypatch.setattr(agenda, "upcoming", boom)
        # Degrades to the old behaviour — the task counts — rather than crashing.
        assert svc.pending_task_count() == 1
