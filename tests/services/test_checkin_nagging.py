"""The after-school brief is one-way and bounded.

It used to ask "what's your plan?" nine times a day, then started adopting
calendar entries as a plan. A secretary reports verified state once and leaves
the user's planning decisions alone.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from argon.productivity import state as state_mod
from argon.services import reminder as reminder_mod
from argon.services.reminder import OCCASIONS, ReminderService

LA = ZoneInfo("America/Los_Angeles")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 11, hour, minute, tzinfo=LA)


class _Clock:
    def __init__(self, now): self.now = now
    def __call__(self): return self.now
    def advance(self, minutes): self.now += timedelta(minutes=minutes)


async def _silent(_prompt): return ""


def _service(tmp_path, monkeypatch, now, **kwargs):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    clock = _Clock(now)
    day_key = lambda *a, **k: clock.now.strftime("%Y-%m-%d")  # noqa: E731
    monkeypatch.setattr(state_mod, "_now", clock)
    monkeypatch.setattr(state_mod, "_today_key", day_key)
    monkeypatch.setattr(reminder_mod.clock, "today_key", day_key)
    monkeypatch.setattr(reminder_mod.clock, "now", clock)
    service = ReminderService(tmp_path, "America/Los_Angeles", _silent, **kwargs)
    monkeypatch.setattr(service, "_now", clock)
    monkeypatch.setattr(service, "pending_task_count", lambda: 3)
    monkeypatch.setattr(service, "_pending_event", lambda: None)
    return service, clock


class TestTheBriefIsBounded:
    def test_a_silent_brief_is_attempted_only_once(self, tmp_path, monkeypatch):
        service, clock = _service(tmp_path, monkeypatch, _at(16, 0))

        briefs = 0
        for _ in range(42):  # 4 PM to 11 PM at ten-minute ticks
            occasion = service.pick_occasion()
            if occasion is not None and occasion.kind == "daily_brief":
                briefs += 1
                service.ledger.record_fired("daily_brief", clock.now)
            clock.advance(10)

        assert briefs == 1

    def test_an_explicit_plan_does_not_suppress_the_brief(self, tmp_path, monkeypatch):
        service, _ = _service(tmp_path, monkeypatch, _at(17, 0))
        service._plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])

        assert service.pick_occasion().kind == "daily_brief"

    def test_the_brief_expires_with_the_evening(self, tmp_path, monkeypatch):
        service, _ = _service(tmp_path, monkeypatch, _at(21, 0))
        assert service.pick_occasion() is None

    def test_the_prompt_does_not_request_plan_management(self, tmp_path, monkeypatch):
        service, _ = _service(tmp_path, monkeypatch, _at(18, 0))
        prompt = service.build_prompt(OCCASIONS["daily_brief"])
        assert "He decides when he works" in prompt
        assert "set_day_plan" not in prompt
        assert "what is he doing" not in prompt


class TestCalendarIsNotAPlan:
    def test_calendar_entries_are_not_adopted_as_plan_blocks(
        self, tmp_path, monkeypatch
    ):
        from argon.services import agenda

        service, _ = _service(tmp_path, monkeypatch, _at(17, 0))
        monkeypatch.setattr(agenda, "upcoming", lambda ws: [
            {"summary": "All Project Sync", "start": _at(19), "end": _at(20)},
        ])

        assert service.pick_occasion().kind == "daily_brief"
        assert service._plan.blocks() == []


class TestConservativeOccasions:
    def test_only_a_planned_start_and_imminent_event_remain_after_the_brief(
        self, tmp_path, monkeypatch
    ):
        service, _ = _service(tmp_path, monkeypatch, _at(20, 30))
        service._plan.set_blocks([{"start": "17:00", "end": "20:00", "what": "Gym"}])

        assert service.pick_occasion() is None

    def test_the_daily_cap_applies_to_a_planned_start(self, tmp_path, monkeypatch):
        service, clock = _service(tmp_path, monkeypatch, _at(17, 0), max_per_day=3)
        service._plan.set_blocks([{"start": "17:00", "what": "SAT prep"}])
        for i in range(3):
            service.ledger.record_said("brief", f"message {i}", clock.now - timedelta(hours=2))

        assert service.pick_occasion() is None
