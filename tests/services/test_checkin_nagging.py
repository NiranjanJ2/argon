"""Four bugs that between them asked "what's your plan?" nine times a day.

Aug 8, 9 and 10, 2026: `plan_request` fired nine times each day, every 100
minutes from 8 AM to 9:30 PM, each a slight reword of the last, and Niranjan
never answered once. Aug 11 it swung the other way and said two things all day,
one of them about a recurring meeting he had never mentioned.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from argon.productivity import state as state_mod
from argon.services import reminder as reminder_mod
from argon.services.reminder import (
    HIS_OWN_SCHEDULE,
    MAX_PLAN_ASKS_PER_DAY,
    OCCASIONS,
    ReminderService,
    is_near_duplicate,
)

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
    monkeypatch.setattr(service, "_seed_plan", lambda: None)
    return service, clock


class TestItStopsAsking:
    def test_a_day_of_asking_is_bounded(self, tmp_path, monkeypatch):
        """Nine unanswered questions is not persistence, it is why he stopped
        reading them."""
        service, clock = _service(tmp_path, monkeypatch, _at(16, 0))

        asks = 0
        for _ in range(42):  # 4 PM to 11 PM at ten-minute ticks
            occasion = service.pick_occasion()
            if occasion is not None and occasion.kind == "plan_request":
                asks += 1
                service.ledger.record_fired("plan_request", clock.now)
                service._plan.record_asked()
                service.ledger.record_said("plan_request", "ask {}".format(asks), clock.now)
            clock.advance(10)

        assert asks == MAX_PLAN_ASKS_PER_DAY

    def test_answering_stops_it_immediately(self, tmp_path, monkeypatch):
        service, _ = _service(tmp_path, monkeypatch, _at(17, 0))
        service._plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        occasion = service.pick_occasion()
        assert occasion is None or occasion.kind != "plan_request"

    def test_the_prompt_admits_it_already_asked(self, tmp_path, monkeypatch):
        service, _ = _service(tmp_path, monkeypatch, _at(18, 0))
        service._plan.record_asked()
        prompt = service.build_prompt(OCCASIONS["plan_request"])
        assert "already asked" in prompt and "not ask the same question" in prompt


class TestASeededPlanIsNotAnAnswer:
    def test_one_calendar_entry_does_not_count_as_a_plan(self, tmp_path, monkeypatch):
        """"All Project Sync" 7-8pm was enough to silence the question entirely,
        so Argon said two things all day and never asked what he was doing."""
        service, _ = _service(tmp_path, monkeypatch, _at(17, 0))
        service._plan.seed_from([
            {"summary": "All Project Sync", "start": _at(19), "end": _at(20)},
        ])

        assert service.pick_occasion().kind == "plan_request"

    def test_but_a_plan_he_stated_does(self, tmp_path, monkeypatch):
        service, _ = _service(tmp_path, monkeypatch, _at(17, 0))
        service._plan.seed_from([{"summary": "Sync", "start": _at(19), "end": _at(20)}])
        service._plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])

        occasion = service.pick_occasion()
        assert occasion is None or occasion.kind != "plan_request"

    def test_the_ask_mentions_what_is_already_fixed(self, tmp_path, monkeypatch):
        service, _ = _service(tmp_path, monkeypatch, _at(17, 0))
        service._plan.seed_from([{"summary": "Sync", "start": _at(19), "end": _at(20)}])
        prompt = service.build_prompt(OCCASIONS["plan_request"])
        assert "already has these fixed" in prompt


class TestTheRewordFilterKnowsItsScope:
    def test_a_block_end_is_not_a_reword_of_its_own_start(self):
        """The two share the block's name because naming it is the point."""
        start = "All Project Sync starts now."
        end = "How did the All Project Sync go?"
        assert is_near_duplicate(end, [start]) is True   # the filter would kill it
        assert "block_end" in HIS_OWN_SCHEDULE           # so it is exempt

    def test_discretionary_occasions_are_still_filtered(self):
        assert "open_stretch" not in HIS_OWN_SCHEDULE
        assert "plan_request" not in HIS_OWN_SCHEDULE
