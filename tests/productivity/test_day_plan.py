"""The plan is the schedule — Argon reaches out at times he chose.

Before this it reached out on a timer: generic windows plus an `idle` nudge
every two hours whenever any task was open. From the receiving end that is
indistinguishable from random, and the natural response to a message that
arrives for no reason is to stop reading them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from argon.productivity import plan as plan_mod
from argon.productivity.plan import BLOCK_GRACE_MINUTES, DayPlan, normalize_time

LA = ZoneInfo("America/Los_Angeles")


def _at(hour, minute=0):
    return datetime(2026, 7, 30, hour, minute, tzinfo=LA)


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-07-30")
    monkeypatch.setattr(plan_mod.clock, "now", lambda: _at(12))
    return DayPlan(tmp_path)


class TestReadingHisTimes:
    def test_the_forms_he_actually_says(self):
        assert normalize_time("14:00") == "14:00"
        assert normalize_time("2pm") == "14:00"
        assert normalize_time("2:30 PM") == "14:30"
        assert normalize_time("9am") == "09:00"

    def test_midnight_and_noon_are_the_awkward_ones(self):
        assert normalize_time("12am") == "00:00"
        assert normalize_time("12pm") == "12:00"

    def test_nonsense_is_refused_rather_than_guessed(self):
        for bad in ("", "later", "25:00", "13pm", "2:99pm", None):
            assert normalize_time(bad) is None


class TestSettingThePlan:
    def test_blocks_are_stored_in_time_order(self, plan):
        plan.set_blocks([
            {"start": "5pm", "what": "Gym"},
            {"start": "2pm", "end": "4pm", "what": "SAT prep"},
        ])
        assert [b.what for b in plan.blocks()] == ["SAT prep", "Gym"]

    def test_an_unreadable_block_is_dropped_not_guessed(self, plan):
        """A block silently mis-timed is worse than one he can see is missing."""
        stored = plan.set_blocks([
            {"start": "sometime", "what": "Gym"},
            {"start": "2pm", "what": "SAT prep"},
        ])
        assert [b.what for b in stored] == ["SAT prep"]

    def test_setting_a_plan_replaces_the_old_one(self, plan):
        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        plan.set_blocks([{"start": "3pm", "what": "Gym"}])
        assert [b.what for b in plan.blocks()] == ["Gym"]

    def test_a_new_plan_clears_a_refusal(self, plan):
        plan.decline()
        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        assert plan.declined() is False


class TestTheDayBoundary:
    def test_yesterdays_plan_is_not_todays(self, plan, tmp_path, monkeypatch):
        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-07-31")
        assert plan.blocks() == [] and plan.exists() is False

    def test_a_corrupt_file_reads_as_no_plan(self, plan, tmp_path):
        (tmp_path / "daily" / "plan.json").write_text("{not json")
        assert plan.exists() is False


class TestTheMomentsItSpeaks:
    def test_a_block_starting_now(self, plan):
        plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        assert plan.starting_now(_at(14, 2)).what == "SAT prep"

    def test_not_forty_minutes_into_it(self, plan):
        """"Starting now" stops being true; a late nudge is just an interruption."""
        plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        assert plan.starting_now(_at(14, BLOCK_GRACE_MINUTES + 5)) is None

    def test_a_block_that_just_ended(self, plan):
        plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        assert plan.just_ended(_at(16, 5)).what == "SAT prep"

    def test_an_open_ended_block_never_ends(self, plan):
        plan.set_blocks([{"start": "2pm", "what": "Reading"}])
        assert plan.just_ended(_at(16, 5)) is None

    def test_a_finished_block_is_left_alone(self, plan):
        plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        plan.mark("b0", "done")
        assert plan.starting_now(_at(14, 2)) is None
        assert plan.just_ended(_at(16, 5)) is None


class TestFreeTime:
    def test_a_long_unclaimed_stretch_is_offered(self, plan):
        plan.set_blocks([{"start": "5pm", "end": "6pm", "what": "Gym"}])
        gap = plan.open_stretch(_at(13))
        assert gap is not None and gap.minutes == 240

    def test_a_short_one_is_not_worth_asking_about(self, plan):
        plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        assert plan.open_stretch(_at(13, 30)) is None

    def test_inside_a_block_is_not_free_time(self, plan):
        plan.set_blocks([{"start": "2pm", "end": "6pm", "what": "SAT prep"}])
        assert plan.open_stretch(_at(15)) is None

    def test_an_open_ended_block_claims_the_rest_of_the_day(self, plan):
        plan.set_blocks([{"start": "2pm", "what": "Reading"}])
        assert plan.open_stretch(_at(15)) is None

    def test_after_the_last_block_the_evening_is_still_his(self, plan):
        plan.set_blocks([{"start": "9am", "end": "10am", "what": "Gym"}])
        gap = plan.open_stretch(_at(13))
        assert gap is not None and gap.end.hour == 21

    def test_late_at_night_there_is_nothing_left_to_offer(self, plan):
        plan.set_blocks([{"start": "9am", "end": "10am", "what": "Gym"}])
        assert plan.open_stretch(_at(22)) is None


class TestScheduledWorkIsNotOutstanding:
    def test_a_planned_block_reads_as_a_commitment(self, plan):
        """Same rule as a calendar reminder: he already decided when."""
        from argon.tools.tasks import mark_scheduled, unscheduled

        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        tasks = mark_scheduled([{"id": "a", "title": "SAT prep"}], plan.as_entries())

        assert tasks[0]["scheduled_for"] == "2:00 PM"
        assert unscheduled(tasks) == []

    def test_a_finished_block_stops_shielding_the_task(self, plan):
        from argon.tools.tasks import mark_scheduled, unscheduled

        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        plan.mark("b0", "done")
        tasks = mark_scheduled([{"id": "a", "title": "SAT prep"}], plan.as_entries())
        assert len(unscheduled(tasks)) == 1


class TestTheSummaryHeReadsBack:
    def test_it_says_where_he_is_in_the_day(self, plan):
        plan.set_blocks([
            {"start": "9am", "end": "10am", "what": "Gym"},
            {"start": "2pm", "end": "4pm", "what": "SAT prep"},
        ])
        lines = plan.summary(_at(15)).replace("\u2013", "-").splitlines()

        assert lines[0] == "- 9:00 AM-10:00 AM Gym (passed)"
        assert lines[1] == "- 2:00 PM-4:00 PM SAT prep (now)"

    def test_no_plan_says_so_plainly(self, plan):
        assert plan.summary(_at(12)) == "- no plan yet today"
