"""Check-in policy: when Argon decides to start a conversation.

The old policy was a veto chain and these tests asserted the vetoes. It could go
days without speaking, so the policy inverted: local state names an *occasion*,
and only then is a model call spent. What is worth pinning now is the opposite
of before — that ordinary moments do earn a check-in — plus the guards that keep
"more active" from becoming "naggy".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from argon.productivity import state as state_mod
from argon.services import reminder as reminder_mod
from argon.services.reminder import ReminderService

LA = ZoneInfo("America/Los_Angeles")


def _at(hour: int, minute: int = 0) -> datetime:
    """A fixed weekday (Thursday) in Los Angeles."""
    return datetime(2026, 7, 30, hour, minute, tzinfo=LA)


class _Clock:
    """One instant shared by the service, DailyState and the ledger."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += timedelta(minutes=minutes)


async def _silent(_prompt: str) -> str:
    return ""


def _service(tmp_path, monkeypatch, now: datetime, handler=_silent, **kwargs):
    """A service whose every clock is pinned to *now*."""
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    clock = _Clock(now)
    day_key = lambda *_a, **_k: clock.now.strftime("%Y-%m-%d")  # noqa: E731

    monkeypatch.setattr(state_mod, "_now", clock)
    monkeypatch.setattr(state_mod, "_today_key", day_key)
    monkeypatch.setattr(reminder_mod.clock, "today_key", day_key)

    service = ReminderService(tmp_path, "America/Los_Angeles", handler, **kwargs)
    monkeypatch.setattr(service, "_now", clock)
    return service, clock


def _mode(service: ReminderService, mode: str) -> None:
    service._state.set_mode(mode)


# ---------------------------------------------------------------------------
# Occasions — the point is that ordinary moments now qualify
# ---------------------------------------------------------------------------


def test_an_untouched_afternoon_still_earns_a_check_in(tmp_path, monkeypatch):
    """The old policy needed a home-arrival stamp and stayed mute without one."""
    service, _ = _service(tmp_path, monkeypatch, _at(13, 30))
    assert service.pick_occasion() is not None


def test_morning_is_no_longer_dead_time(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(8, 30))
    assert service.pick_occasion().kind == "morning"


def test_evening_gets_a_wrap_up(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(20, 30))
    assert service.pick_occasion().kind == "evening"


def test_a_long_session_is_worth_remarking_on(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 0))
    _mode(service, "working")
    clock.advance(40)
    assert service.pick_occasion().kind == "session"


# ---------------------------------------------------------------------------
# Guards — what keeps "more active" from becoming "naggy"
# ---------------------------------------------------------------------------


def test_quiet_hours_silence_everything(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(23, 30))
    assert service.pick_occasion() is None


def test_napping_silences_everything(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(13, 30))
    _mode(service, "napping")
    assert service.pick_occasion() is None


def test_a_short_session_is_not_worth_interrupting(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 0))
    _mode(service, "working")
    clock.advance(10)
    assert service.pick_occasion() is None


def test_mid_session_nothing_but_the_session_may_interrupt(tmp_path, monkeypatch):
    """Deep work must not be broken into by an idle or ambient nudge."""
    service, clock = _service(tmp_path, monkeypatch, _at(13, 0))
    _mode(service, "lock_in")
    clock.advance(5)
    assert service.pick_occasion() is None


def test_an_occasion_does_not_repeat_inside_its_cooldown(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 30))
    first = service.pick_occasion()
    service.ledger.record_fired(first.kind, clock.now)
    assert service.pick_occasion() != first


def test_a_silent_check_in_still_starts_the_cooldown(tmp_path, monkeypatch):
    """Otherwise a quiet model is re-asked every tick and burns calls."""
    service, clock = _service(tmp_path, monkeypatch, _at(13, 30))
    service.ledger.record_fired("idle", clock.now)
    assert service.ledger.minutes_since_fired("idle", clock.now) == 0
    assert service.ledger.spoken_count() == 0


def test_the_daily_cap_holds(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 30), max_per_day=2)
    for i in range(2):
        service.ledger.record_said("idle", f"message {i}", clock.now - timedelta(hours=2))
    assert service.pick_occasion() is None


def test_messages_are_spaced_out(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 30), min_gap_minutes=25)
    service.ledger.record_said("morning", "hey", clock.now - timedelta(minutes=5))
    assert service.pick_occasion() is None


@pytest.mark.parametrize("hour,quiet", [(2, True), (23, True), (12, False), (7, False)])
def test_quiet_hours_wrap_past_midnight(tmp_path, monkeypatch, hour, quiet):
    service, clock = _service(tmp_path, monkeypatch, _at(hour))
    assert service._in_quiet_hours(clock.now) is quiet


# ---------------------------------------------------------------------------
# The ledger — what makes talking more often safe
# ---------------------------------------------------------------------------


def test_the_prompt_carries_what_was_already_said(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 30))
    # Hours ago, so the min-gap guard still allows a fresh occasion.
    service.ledger.record_said(
        "morning", "chem pset is due tonight", clock.now - timedelta(hours=5)
    )
    prompt = service.build_prompt(service.pick_occasion())
    assert "chem pset is due tonight" in prompt
    assert "repeat" in prompt.lower()


def test_the_ledger_survives_a_restart(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 30))
    service.ledger.record_said("idle", "hey", clock.now)

    revived, _ = _service(tmp_path, monkeypatch, _at(13, 30))
    assert revived.ledger.spoken_count() == 1
    assert revived.ledger.said_today() == ["hey"]


def test_a_new_day_clears_the_ledger(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(13, 30))
    service.ledger.record_said("idle", "yesterday", clock.now)

    fresh, _ = _service(tmp_path, monkeypatch, _at(13, 30) + timedelta(days=1))
    assert fresh.ledger.spoken_count() == 0


# ---------------------------------------------------------------------------
# tick()
# ---------------------------------------------------------------------------


async def test_tick_records_what_was_actually_said(tmp_path, monkeypatch):
    prompts: list[str] = []

    async def speak(prompt: str) -> str:
        prompts.append(prompt)
        return "hey, how'd the pset go?"

    service, _ = _service(tmp_path, monkeypatch, _at(13, 30), handler=speak)

    assert await service.tick() == "hey, how'd the pset go?"
    assert service.ledger.said_today() == ["hey, how'd the pset go?"]
    assert len(prompts) == 1


async def test_a_silent_turn_records_nothing(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(13, 30))
    assert await service.tick() == ""
    assert service.ledger.spoken_count() == 0


# ---------------------------------------------------------------------------
# Silence detection
#
# gpt-oss-20b answered the *decision* ("No.") when the prompt asked it to
# decide, and that string would have been delivered as the check-in.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reply", [
    "SKIP", "skip", "Skip.", "No.", "no", "none", "Nothing to say", "",
    "   ", '"SKIP"', "SKIP — nothing worth saying", "pass", "N/A",
])
def test_refusals_are_recognised_as_silence(reply):
    assert reminder_mod.is_silence(reply) is True


@pytest.mark.parametrize("reply", [
    "hey, how'd the chem pset go?",
    "you've been at it 40 minutes — worth a break?",
    "No pressure, but that essay is due tonight.",
])
def test_real_messages_are_not_silence(reply):
    assert reminder_mod.is_silence(reply) is False


async def test_a_refusal_is_never_recorded_as_something_said(tmp_path, monkeypatch):
    async def refuse(_prompt: str) -> str:
        return "SKIP"

    service, _ = _service(tmp_path, monkeypatch, _at(13, 30), handler=refuse)
    assert await service.tick() == ""
    assert service.ledger.spoken_count() == 0


async def test_a_bare_no_is_never_recorded(tmp_path, monkeypatch):
    async def answer_the_question(_prompt: str) -> str:
        return "No."

    service, _ = _service(tmp_path, monkeypatch, _at(13, 30), handler=answer_the_question)
    assert await service.tick() == ""
    assert service.ledger.spoken_count() == 0


def test_the_prompt_asks_for_a_message_not_a_decision(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(13, 30))
    prompt = service.build_prompt(service.pick_occasion())
    assert "WRITE THE TEXT MESSAGE" in prompt
    assert reminder_mod.SKIP_TOKEN in prompt
    # The old phrasing is what produced "No.".
    assert "deciding whether" not in prompt
