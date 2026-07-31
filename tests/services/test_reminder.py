"""The local policy that decides whether waking the model is worth it.

Every ``True`` here costs a real LLM turn, so the suppressions are the point.
Both the service clock and ``DailyState``'s clock are pinned to the same instant
so that "session N minutes in" is deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from argon.productivity import state as state_mod
from argon.services.reminder import ReminderService

LA = ZoneInfo("America/Los_Angeles")


def _at(hour: int, minute: int = 0) -> datetime:
    """A fixed weekday afternoon in Los Angeles."""
    return datetime(2026, 7, 30, hour, minute, tzinfo=LA)


class _Clock:
    """One instant shared by the service and by DailyState."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


async def _never_called(prompt: str) -> None:
    raise AssertionError("should_check_in must not run a turn")


def _service(tmp_path, monkeypatch, now: datetime) -> tuple[ReminderService, _Clock]:
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    clock = _Clock(now)
    monkeypatch.setattr(state_mod, "_now", clock)
    service = ReminderService(tmp_path, "America/Los_Angeles", _never_called)
    monkeypatch.setattr(service, "_now", clock)
    return service, clock


# ---------------------------------------------------------------------------
# should_check_in
# ---------------------------------------------------------------------------


def test_napping_suppresses(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(15))
    service._state.set_mode("napping")
    assert service.should_check_in() == (False, "napping")


def test_done_for_the_day_suppresses(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(20))
    service._state.set_mode("done")
    service._state.set_home_arrival()
    assert service.should_check_in() == (False, "done for the day")


def test_before_noon_suppresses_even_when_home_and_idle(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(11, 59))
    service._state.set_home_arrival()
    assert service.should_check_in() == (False, "before noon")


def test_working_session_under_25_minutes_suppresses(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13))
    service._state.set_mode("working")
    clock.advance(24)

    allowed, reason = service.should_check_in()

    assert allowed is False
    assert "24m in" in reason


def test_working_session_over_25_minutes_allows(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13))
    service._state.set_mode("working")
    clock.advance(26)

    allowed, reason = service.should_check_in()

    assert allowed is True
    assert "26m in" in reason


def test_lock_in_uses_the_same_25_minute_floor(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13))
    service._state.set_mode("working")  # starts the work-session clock
    service._state.set_mode("lock_in")
    clock.advance(30)

    assert service.should_check_in()[0] is True


def test_speaking_recently_suppresses_for_45_minutes(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13))
    service._state.set_mode("working")
    clock.advance(60)
    service._last_spoke_at = clock.now - timedelta(minutes=44)

    assert service.should_check_in() == (False, "spoke recently")


def test_the_cooldown_expires_after_45_minutes(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13))
    service._state.set_mode("working")
    clock.advance(60)
    service._last_spoke_at = clock.now - timedelta(minutes=46)

    assert service.should_check_in()[0] is True


def test_idle_and_home_allows(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(16))
    service._state.set_home_arrival()
    assert service.should_check_in() == (True, "home and idle")


def test_late_night_idle_suppresses(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(22, 30))
    service._state.set_home_arrival()
    assert service.should_check_in() == (False, "late and idle")


def test_idle_and_not_home_suppresses(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(16))
    allowed, reason = service.should_check_in()
    assert allowed is False
    assert "nothing to act on" in reason


def test_a_fresh_state_file_never_wakes_the_model(tmp_path, monkeypatch):
    """No state written at all — the common case right after a restart."""
    service, _ = _service(tmp_path, monkeypatch, _at(14))
    assert service.should_check_in()[0] is False


# ---------------------------------------------------------------------------
# interval_minutes
# ---------------------------------------------------------------------------


def test_interval_is_tightest_during_a_session(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(16))
    service._state.set_mode("working")
    assert service.interval_minutes() == 15

    service._state.set_mode("lock_in")
    assert service.interval_minutes() == 15


def test_interval_is_loosest_overnight_and_in_the_morning(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(23))
    assert service.interval_minutes() == 30

    clock.now = _at(9)
    assert service.interval_minutes() == 30


def test_interval_tightens_over_the_afternoon(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(17))
    assert service.interval_minutes() == 20
