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


def _service(tmp_path, monkeypatch, now: datetime, handler=_silent, *, tasks=3, **kwargs):
    """A service whose every clock is pinned to *now*.

    ``tasks`` stubs the pending-task count. It defaults to a non-zero value
    because most tests are about *when* Argon speaks, not whether there is
    anything to speak about — the no-material case is covered explicitly.
    """
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    clock = _Clock(now)
    day_key = lambda *_a, **_k: clock.now.strftime("%Y-%m-%d")  # noqa: E731

    monkeypatch.setattr(state_mod, "_now", clock)
    monkeypatch.setattr(state_mod, "_today_key", day_key)
    monkeypatch.setattr(reminder_mod.clock, "today_key", day_key)
    # snooze_until() reads the process clock, not the service's.
    monkeypatch.setattr(reminder_mod.clock, "now", clock)

    service = ReminderService(tmp_path, "America/Los_Angeles", handler, **kwargs)
    monkeypatch.setattr(service, "_now", clock)
    monkeypatch.setattr(service, "pending_task_count", lambda: tasks)
    return service, clock


def _mode(service: ReminderService, mode: str) -> None:
    service._state.set_mode(mode)


# ---------------------------------------------------------------------------
# Occasions — the point is that ordinary moments now qualify
# ---------------------------------------------------------------------------


def test_an_untouched_afternoon_still_earns_a_check_in(tmp_path, monkeypatch):
    """The old policy needed a home-arrival stamp and stayed mute without one."""
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30))
    assert service.pick_occasion() is not None


def test_the_day_starts_by_asking_what_it_looks_like(tmp_path, monkeypatch):
    """Not in the morning any more: he is at school. The question belongs to
    the part of the day he actually controls."""
    service, _ = _service(tmp_path, monkeypatch, _at(16, 30))
    assert service.pick_occasion().kind == "plan_request"


def test_evening_gets_a_wrap_up(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(20, 30))
    service._plan.set_blocks([{"start": "09:00", "end": "10:00", "what": "Gym"}])
    assert service.pick_occasion().kind == "evening"


def test_being_mid_work_is_not_an_occasion(tmp_path, monkeypatch):
    """There was a `session` occasion here that fired every 45 minutes while he
    worked. On the first day of school it went off six times and four were
    suppressed as rewords of each other, because "you're on APUSH, want to
    switch to Math?" is the only thing it had to say and he had not asked."""
    service, clock = _service(tmp_path, monkeypatch, _at(17, 0))
    _mode(service, "working")
    clock.advance(40)
    assert service.pick_occasion() is None


# ---------------------------------------------------------------------------
# Guards — what keeps "more active" from becoming "naggy"
# ---------------------------------------------------------------------------


def test_quiet_hours_silence_everything(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(23, 30))
    assert service.pick_occasion() is None


def test_napping_silences_everything(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30))
    _mode(service, "napping")
    assert service.pick_occasion() is None


def test_a_short_session_is_not_worth_interrupting(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 0))
    _mode(service, "working")
    clock.advance(10)
    assert service.pick_occasion() is None


def test_mid_session_nothing_but_the_session_may_interrupt(tmp_path, monkeypatch):
    """Deep work must not be broken into by an idle or ambient nudge."""
    service, clock = _service(tmp_path, monkeypatch, _at(17, 0))
    _mode(service, "lock_in")
    clock.advance(5)
    assert service.pick_occasion() is None


def test_an_occasion_does_not_repeat_inside_its_cooldown(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30))
    first = service.pick_occasion()
    service.ledger.record_fired(first.kind, clock.now)
    assert service.pick_occasion() != first


def test_a_silent_check_in_still_starts_the_cooldown(tmp_path, monkeypatch):
    """Otherwise a quiet model is re-asked every tick and burns calls."""
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30))
    service.ledger.record_fired("idle", clock.now)
    assert service.ledger.minutes_since_fired("idle", clock.now) == 0
    assert service.ledger.spoken_count() == 0


def test_the_daily_cap_holds(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30), max_per_day=2)
    for i in range(2):
        service.ledger.record_said("idle", f"message {i}", clock.now - timedelta(hours=2))
    assert service.pick_occasion() is None


def test_messages_are_spaced_out(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30), min_gap_minutes=25)
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
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30))
    # Hours ago, so the min-gap guard still allows a fresh occasion.
    service.ledger.record_said(
        "morning", "chem pset is due tonight", clock.now - timedelta(hours=5)
    )
    prompt = service.build_prompt(service.pick_occasion())
    assert "chem pset is due tonight" in prompt
    assert "repeat" in prompt.lower()


def test_the_ledger_survives_a_restart(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30))
    service.ledger.record_said("idle", "hey", clock.now)

    revived, _ = _service(tmp_path, monkeypatch, _at(17, 30))
    assert revived.ledger.spoken_count() == 1
    assert revived.ledger.said_today() == ["hey"]


def test_a_new_day_clears_the_ledger(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30))
    service.ledger.record_said("idle", "yesterday", clock.now)

    fresh, _ = _service(tmp_path, monkeypatch, _at(17, 30) + timedelta(days=1))
    assert fresh.ledger.spoken_count() == 0


# ---------------------------------------------------------------------------
# tick()
# ---------------------------------------------------------------------------


async def test_tick_records_what_was_actually_said(tmp_path, monkeypatch):
    prompts: list[str] = []

    async def speak(prompt: str) -> str:
        prompts.append(prompt)
        return "hey, how'd the pset go?"

    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), handler=speak)

    assert await service.tick() == "hey, how'd the pset go?"
    assert service.ledger.said_today() == ["hey, how'd the pset go?"]
    assert len(prompts) == 1


async def test_a_silent_turn_records_nothing(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30))
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

    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), handler=refuse)
    assert await service.tick() == ""
    assert service.ledger.spoken_count() == 0


async def test_a_bare_no_is_never_recorded(tmp_path, monkeypatch):
    async def answer_the_question(_prompt: str) -> str:
        return "No."

    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), handler=answer_the_question)
    assert await service.tick() == ""
    assert service.ledger.spoken_count() == 0


def test_the_prompt_asks_for_a_message_not_a_decision(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30))
    prompt = service.build_prompt(service.pick_occasion())
    assert "WRITE THE TEXT MESSAGE" in prompt
    assert reminder_mod.SKIP_TOKEN in prompt
    # The old phrasing is what produced "No.".
    assert "deciding whether" not in prompt


# ---------------------------------------------------------------------------
# Confabulation guard
#
# Live failure, 2026-08-01: with zero tasks and zero events, `idle` fired five
# times and the model invented "the project due next week" and "How's the UCLA
# lab work going?" — built out of a biography line in SOUL.md — on a day
# Niranjan had explicitly called a rest day.
# ---------------------------------------------------------------------------


def test_declining_to_plan_ends_the_asking(tmp_path, monkeypatch):
    """"I'm not planning today" has to land somewhere the gate reads."""
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), tasks=0)
    service._plan.decline()
    assert service.pick_occasion() is None


def test_a_free_stretch_is_offered_back_not_filled(tmp_path, monkeypatch):
    """With a plan and nothing scheduled now, the question is his to answer."""
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), tasks=3)
    service._plan.set_blocks([{"start": "21:00", "end": "22:00", "what": "Gym"}])
    assert service.pick_occasion().kind == "open_stretch"


def test_a_short_gap_is_not_worth_a_message(tmp_path, monkeypatch):
    """Twenty minutes before the next block is not usable time."""
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), tasks=3)
    service._plan.set_blocks([{"start": "18:00", "end": "20:00", "what": "SAT prep"}])
    occasion = service.pick_occasion()
    assert occasion is None or occasion.kind != "open_stretch"


def test_asking_for_a_plan_does_not_need_the_task_list(tmp_path, monkeypatch):
    """"What's your day look like" is answerable with Google down."""
    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), tasks=-1)
    assert service.pick_occasion().kind == "plan_request"


def test_a_block_boundary_still_interrupts_work(tmp_path, monkeypatch):
    """Removing the mid-work nag must not silence the moments he chose."""
    service, clock = _service(tmp_path, monkeypatch, _at(17, 0), tasks=0)
    service._plan.set_blocks([{"start": "17:00", "end": "19:00", "what": "SAT prep"}])
    _mode(service, "working")
    clock.advance(120)          # 19:00 — the block he set is over
    assert service.pick_occasion().kind == "block_end"


# ---------------------------------------------------------------------------
# Snooze
# ---------------------------------------------------------------------------


def test_a_snooze_silences_every_occasion(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(16, 30), tasks=5)
    assert service.pick_occasion() is not None

    reminder_mod.snooze(tmp_path, hours=24, reason="rest day")
    assert service.pick_occasion() is None


def test_a_snooze_expires(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(16, 30), tasks=5)
    reminder_mod.snooze(tmp_path, hours=2, reason="nap")
    assert service.pick_occasion() is None

    clock.advance(3 * 60)
    assert service.pick_occasion() is not None


def test_a_snooze_can_be_lifted(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(16, 30), tasks=5)
    reminder_mod.snooze(tmp_path, hours=24, reason="rest day")
    reminder_mod.clear_snooze(tmp_path)
    assert service.pick_occasion() is not None


def test_a_corrupt_snooze_file_does_not_mute_argon_forever(tmp_path, monkeypatch):
    (tmp_path / "daily").mkdir(parents=True, exist_ok=True)
    (tmp_path / "daily" / "snooze.json").write_text("{ not json")
    assert reminder_mod.snooze_until(tmp_path) is None


# ---------------------------------------------------------------------------
# Mechanical de-duplication
# ---------------------------------------------------------------------------


def test_the_actual_reworded_pair_from_the_incident_is_caught():
    first = "Project due next week—let's lock in a session to start it."
    second = "Ready to lock in a session for the project due next week?"
    assert reminder_mod.is_near_duplicate(second, [first]) is True


def test_a_genuinely_different_message_passes():
    first = "Project due next week—let's lock in a session to start it."
    second = "How'd the chem pset go?"
    assert reminder_mod.is_near_duplicate(second, [first]) is False


async def test_a_reworded_check_in_is_never_delivered(tmp_path, monkeypatch):
    async def reword(_prompt: str) -> str:
        return "Ready to lock in a session for the project due next week?"

    service, clock = _service(tmp_path, monkeypatch, _at(17, 30), handler=reword, tasks=3)
    service.ledger.record_said(
        "idle",
        "Project due next week—let's lock in a session to start it.",
        clock.now - timedelta(hours=2),
    )

    assert await service.tick() == ""
    assert service.ledger.spoken_count() == 1  # still only the original


# ---------------------------------------------------------------------------
# The plan is the schedule — both of these were found by walking a whole
# simulated day through the gate at ten-minute ticks.
# ---------------------------------------------------------------------------


def test_one_free_stretch_earns_one_message(tmp_path, monkeypatch):
    """Keyed on when it was noticed, one gap spoke at 12:30, 13:00 and 13:30.

    Three messages about the same free afternoon is precisely the "random
    messages throughout the day" this whole design exists to stop.
    """
    service, clock = _service(tmp_path, monkeypatch, _at(16, 30))
    service._plan.set_blocks([{"start": "19:00", "end": "20:00", "what": "Math"}])

    spoke = []
    for _ in range(9):  # 16:30 -> 18:00, the whole gap
        occasion = service.pick_occasion()
        if occasion and occasion.kind == "open_stretch":
            spoke.append(clock.now)
            service.ledger.record_announced(reminder_mod._gap_key(service._pending_gap))
            service.ledger.record_said(occasion.kind, "offer", clock.now)
        clock.advance(10)

    assert len(spoke) == 1


def test_the_daily_cap_cannot_swallow_his_own_schedule(tmp_path, monkeypatch):
    """A day of discretionary offers used to exhaust the budget by five and
    silently drop the 7 PM block he had actually asked to be reminded about."""
    service, clock = _service(tmp_path, monkeypatch, _at(18, 55), max_per_day=2)
    service._plan.set_blocks([{"start": "19:00", "what": "UCLA lab reading"}])
    for i in range(4):
        service.ledger.record_said("open_stretch", "offer {}".format(i), clock.now)

    clock.advance(10)
    assert service.pick_occasion().kind == "block_start"


def test_the_cap_still_binds_discretionary_messages(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 0), max_per_day=2)
    service._plan.set_blocks([{"start": "17:00", "end": "18:00", "what": "Gym"}])
    for i in range(3):
        service.ledger.record_said("open_stretch", "offer {}".format(i), clock.now)

    clock.advance(30)
    assert service.pick_occasion() is None


def test_back_to_back_blocks_both_get_a_word(tmp_path, monkeypatch):
    """A 2-4 block followed by a 4-6 block: the end of one and the start of the
    next are due at the same instant. The full 25-minute floor let the second
    one's 20-minute grace window close first, so it was never mentioned."""
    service, clock = _service(tmp_path, monkeypatch, _at(19, 0))
    service._plan.set_blocks([
        {"start": "17:00", "end": "19:00", "what": "SAT prep"},
        {"start": "19:00", "end": "21:00", "what": "Math homework"},
    ])

    seen = []
    for _ in range(3):  # 19:00, 19:10, 19:20
        occasion = service.pick_occasion()
        if occasion:
            seen.append((occasion.kind, service._pending_block.what))
            key = ("end:" if occasion.kind == "block_end" else "start:") + service._pending_block.id
            service.ledger.record_announced(key)
            service.ledger.record_said(occasion.kind, "x", clock.now)
        clock.advance(10)

    assert ("block_end", "SAT prep") in seen
    assert ("block_start", "Math homework") in seen


def test_the_gate_does_not_ask_about_a_day_he_already_planned(tmp_path, monkeypatch):
    """He had a 3 PM and a 7 PM reminder and had said so twice in chat."""
    from argon.services import agenda

    service, clock = _service(tmp_path, monkeypatch, _at(16, 30))
    # Reminders he scheduled himself — "I told you I'll start the math homework
    # at 3" is an answer, even though he never used the word plan.
    monkeypatch.setattr(agenda, "upcoming", lambda ws: [
        {"summary": "Start Math homework", "start": _at(19), "end": None,
         "kind": "reminder"},
        {"summary": "Start UCLA work", "start": _at(21), "end": None,
         "kind": "reminder"},
    ])

    occasion = service.pick_occasion()

    assert occasion is None or occasion.kind != "plan_request"
    assert [b.what for b in service._plan.blocks()] == [
        "Start Math homework", "Start UCLA work",
    ]
