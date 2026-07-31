"""Bell schedule resolution: which period is it, and per-day overrides.

Dates are chosen for their weekday, since that is what picks the default
schedule: 2026-08-03 is a Monday (regular), 2026-08-04 a Tuesday (early
release), 2026-08-05 a Wednesday (advisement).
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from argon.productivity import bell as bell_mod
from argon.productivity.bell import ScheduleManager

LA = ZoneInfo("America/Los_Angeles")
MONDAY = date(2026, 8, 3)
TUESDAY = date(2026, 8, 4)
WEDNESDAY = date(2026, 8, 5)


def _manager(tmp_path, monkeypatch, day: date, hour: int, minute: int = 0) -> ScheduleManager:
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(
        bell_mod,
        "_now_local",
        lambda: datetime(day.year, day.month, day.day, hour, minute, tzinfo=LA),
    )
    return ScheduleManager(tmp_path)


# ---------------------------------------------------------------------------
# get_current_period
# ---------------------------------------------------------------------------


def test_inside_a_period(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 9, 0)  # regular Period 1 is 8:30–9:28
    assert mgr.get_current_period() == {
        "status": "in_period",
        "period": "Period 1",
        "ends_at": "9:28",
        "minutes_remaining": 28,
    }


def test_period_end_is_exclusive(tmp_path, monkeypatch):
    """9:28 is the end of Period 1 and before Period 2 — a passing period."""
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 9, 28)
    result = mgr.get_current_period()
    assert result["status"] == "between_periods"
    assert result["next_period"] == "Period 2"


def test_between_periods(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 9, 30)  # Period 2 starts 9:33
    assert mgr.get_current_period() == {
        "status": "between_periods",
        "next_period": "Period 2",
        "starts_at": "9:33",
        "minutes_until": 3,
    }


def test_before_the_first_period(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 6, 0)
    result = mgr.get_current_period()
    assert result["status"] == "between_periods"
    assert result["next_period"] == "Period 0"
    assert result["minutes_until"] == 90


def test_after_the_last_period(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 20, 0)
    assert mgr.get_current_period() == {
        "status": "school_over",
        "message": "School day is over.",
    }


def test_lunch_is_reported_like_any_other_period(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 13, 0)  # regular Lunch 12:49–13:21
    result = mgr.get_current_period()
    assert result["period"] == "Lunch"
    assert result["minutes_remaining"] == 21


# ---------------------------------------------------------------------------
# Default schedule by weekday
# ---------------------------------------------------------------------------


def test_weekday_picks_the_default_schedule(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 12)
    assert mgr.get_schedule_type() == "regular"
    assert mgr.get_schedule_type(TUESDAY) == "early_release"
    assert mgr.get_schedule_type(WEDNESDAY) == "advisement"


def test_tuesday_resolves_the_early_release_periods(tmp_path, monkeypatch):
    # 11:00 is Period 3 on a regular day (10:50–11:47) but Period 3 on early
    # release runs 10:36–11:26 — same label, different end.
    mgr = _manager(tmp_path, monkeypatch, TUESDAY, 11, 0)
    result = mgr.get_current_period()
    assert result["period"] == "Period 3"
    assert result["ends_at"] == "11:26"


# ---------------------------------------------------------------------------
# set_override
# ---------------------------------------------------------------------------


def test_override_changes_the_resolved_schedule_type(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    assert mgr.get_current_period()["period"] == "Period 3"  # regular 10:50–11:47

    mgr.set_override("minimum_day")

    assert mgr.get_schedule_type() == "minimum_day"
    # Minimum day at 11:00 is Period 4 (10:54–11:30).
    result = mgr.get_current_period()
    assert result["period"] == "Period 4"
    assert result["ends_at"] == "11:30"


def test_override_survives_a_new_manager_instance(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    mgr.set_override("activity")
    assert ScheduleManager(tmp_path).get_schedule_type() == "activity"


def test_override_only_applies_to_its_own_date(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    mgr.set_override("minimum_day", TUESDAY)
    assert mgr.get_schedule_type() == "regular"
    assert mgr.get_schedule_type(TUESDAY) == "minimum_day"


def test_clear_override_restores_the_weekday_default(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    mgr.set_override("minimum_day")
    mgr.clear_override()
    assert mgr.get_schedule_type() == "regular"


def test_unknown_schedule_type_is_rejected(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    with pytest.raises(ValueError):
        mgr.set_override("half_day")
    assert mgr.get_schedule_type() == "regular"


def test_no_school_override_cancels_a_weekday(tmp_path, monkeypatch):
    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    mgr.set_override("none")
    assert mgr.get_schedule_type() is None
    assert mgr.is_school_day() is False
    assert mgr.get_current_period()["status"] == "no_school"


def test_a_range_override_covers_every_day(tmp_path, monkeypatch):
    """Breaks are weeks long; without this the weekday default claimed summer."""
    from datetime import date

    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    count = mgr.set_override_range("none", date(2026, 7, 31), date(2026, 8, 11))

    assert count == 12
    assert mgr.is_school_day(date(2026, 8, 3)) is False
    assert mgr.is_school_day(date(2026, 8, 11)) is False
    # The day after the range is untouched — school resumes on its own.
    assert mgr.is_school_day(date(2026, 8, 12)) is True


def test_a_backwards_range_is_rejected(tmp_path, monkeypatch):
    from datetime import date

    mgr = _manager(tmp_path, monkeypatch, MONDAY, 11, 0)
    with pytest.raises(ValueError):
        mgr.set_override_range("none", date(2026, 8, 11), date(2026, 7, 31))
