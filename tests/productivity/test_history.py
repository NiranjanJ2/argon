"""Days he has already had, and the shapes that repeat in them.

`plan.json` was one file keyed by date, so the moment the day rolled over
yesterday's plan was gone — not archived, replaced. "Same as yesterday" had
nothing to copy and no pattern could be learned from days that were never kept.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from argon.productivity import history as history_mod
from argon.productivity import plan as plan_mod
from argon.productivity.history import PlanHistory
from argon.productivity.plan import DayPlan

LA = ZoneInfo("America/Los_Angeles")
TODAY = datetime(2026, 8, 12, 17, 0, tzinfo=LA)   # a Wednesday


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(history_mod.clock, "now", lambda: TODAY)
    monkeypatch.setattr(history_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    return PlanHistory(tmp_path)


def _write(store, day, blocks):
    store.record(day, [
        {"id": f"b{i}", "start": s, "end": e, "what": w, "status": "pending"}
        for i, (s, e, w) in enumerate(blocks)
    ])


class TestKeepingTheDays:
    def test_a_past_day_can_be_read_back(self, store):
        _write(store, "2026-08-11", [("17:00", "19:00", "Robotics")])
        assert [b.what for b in store.on("2026-08-11")] == ["Robotics"]

    def test_the_most_recent_day_is_what_yesterday_means(self, store):
        """He does not plan every day, so "yesterday" means the last day he did."""
        _write(store, "2026-08-08", [("17:00", "19:00", "Robotics")])
        _write(store, "2026-08-10", [("14:00", "16:00", "SAT prep")])

        day, blocks = store.most_recent(before="2026-08-12")
        assert day == "2026-08-10" and [b.what for b in blocks] == ["SAT prep"]

    def test_today_is_not_its_own_yesterday(self, store):
        _write(store, "2026-08-12", [("17:00", "19:00", "Robotics")])
        assert store.most_recent(before="2026-08-12") == ("", [])


class TestSameAsYesterday:
    def test_it_copies_the_blocks(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-08-11")
        monkeypatch.setattr(history_mod.clock, "today_key", lambda *a, **k: "2026-08-11")
        monkeypatch.setattr(history_mod.clock, "now", lambda: TODAY)
        plan = DayPlan(tmp_path)
        plan.set_blocks([{"start": "5pm", "end": "7pm", "what": "Robotics"}])

        monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
        monkeypatch.setattr(history_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
        assert plan.blocks() == []          # a new day starts empty

        copied = plan.copy_from()
        assert [(b.start, b.end, b.what) for b in copied] == [("17:00", "19:00", "Robotics")]

    def test_statuses_are_not_carried_over(self, tmp_path, monkeypatch):
        """He is doing it again, not remembering having done it."""
        monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-08-11")
        monkeypatch.setattr(history_mod.clock, "today_key", lambda *a, **k: "2026-08-11")
        monkeypatch.setattr(history_mod.clock, "now", lambda: TODAY)
        plan = DayPlan(tmp_path)
        plan.set_blocks([{"start": "5pm", "what": "Robotics"}])
        plan.mark("b0", "done")

        monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
        monkeypatch.setattr(history_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
        assert plan.copy_from()[0].status == "pending"

    def test_nothing_to_copy_is_an_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
        monkeypatch.setattr(history_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
        monkeypatch.setattr(history_mod.clock, "now", lambda: TODAY)
        assert DayPlan(tmp_path).copy_from() == []


class TestLearningTheShapeOfHisWeeks:
    def test_a_weekly_thing_is_recognised(self, store):
        """Mentoring a few Wednesdays running is a pattern, not a coincidence."""
        for day in ("2026-07-15", "2026-07-22", "2026-07-29", "2026-08-05"):
            _write(store, day, [("20:00", None, "Mentoring")])

        found = store.typical("Mentoring")
        assert found.count == 4 and found.weekdays == [2]      # Wednesday
        assert "Wednesday" in found.describe()

    def test_a_consistent_time_is_learned(self, store):
        """"I have robotics" is a complete sentence once the time is known."""
        for day, start in (("2026-07-15", "17:00"), ("2026-07-22", "17:10"),
                           ("2026-07-29", "16:50"), ("2026-08-05", "17:00")):
            _write(store, day, [(start, "19:00", "Robotics")])

        found = store.typical("robotics")
        assert found.start == "17:00" and found.end == "19:00"

    def test_an_outlier_does_not_move_the_pattern(self, store):
        """One 9 AM session must not turn a 5-7 PM club into 5:00-5:12 — the
        end time was averaged over every occurrence, including the outlier."""
        for day in ("2026-07-15", "2026-07-22", "2026-07-29", "2026-08-05"):
            _write(store, day, [("17:00", "19:00", "Robotics")])
        _write(store, "2026-08-03", [("09:00", "10:00", "Robotics")])

        found = store.typical("robotics")
        assert found.start == "17:00" and found.end == "19:00"

    def test_twice_is_not_yet_a_pattern(self, store):
        for day in ("2026-08-05", "2026-08-10"):
            _write(store, day, [("17:00", "19:00", "Robotics")])
        assert store.patterns() == []
        assert len(store.patterns(minimum=2)) == 1

    def test_a_thing_he_keeps_skipping_is_not_a_habit(self, store):
        for day in ("2026-07-15", "2026-07-22", "2026-07-29"):
            store.record(day, [{"id": "b0", "start": "17:00", "end": "19:00",
                                "what": "Robotics", "status": "skipped"}])
        assert store.patterns() == []

    def test_a_scattered_thing_gets_no_time(self, store):
        for day, start in (("2026-07-15", "09:00"), ("2026-07-22", "14:00"),
                           ("2026-07-29", "20:00")):
            _write(store, day, [(start, None, "Reading")])

        found = store.typical("Reading")
        assert found is not None and found.count == 3
        assert found.start is None      # nothing honest to say about when

    def test_loose_naming_still_matches(self, store):
        """"Robotics" and "robotics club" are the same thing to him."""
        for day in ("2026-07-15", "2026-07-22", "2026-07-29"):
            _write(store, day, [("17:00", "19:00", "Robotics club")])
        assert store.typical("robotics club") is not None
