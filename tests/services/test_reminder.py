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


def test_the_after_school_brief_starts_after_four(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(16, 30))
    assert service.pick_occasion().kind == "daily_brief"


def test_the_daily_brief_does_not_depend_on_a_plan_answer(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(16, 30), tasks=3)
    service._plan.set_blocks([{"start": "19:00", "what": "Math"}])

    assert service.pick_occasion().kind == "daily_brief"


def test_an_explicit_plan_is_verified_material_for_the_brief(tmp_path, monkeypatch):
    from argon.services import agenda

    service, _ = _service(tmp_path, monkeypatch, _at(16, 30), tasks=0)
    service._plan.set_blocks([{"start": "19:00", "what": "Math"}])
    monkeypatch.setattr(agenda, "upcoming", lambda ws: [])
    monkeypatch.setattr(agenda, "schoolwork", lambda ws: [])

    assert service.pick_occasion().kind == "daily_brief"


def test_the_daily_brief_needs_verified_material(tmp_path, monkeypatch):
    from argon.services import agenda

    service, _ = _service(tmp_path, monkeypatch, _at(16, 30), tasks=0)
    monkeypatch.setattr(agenda, "upcoming", lambda ws: [])
    monkeypatch.setattr(agenda, "schoolwork", lambda ws: [])

    assert service.pick_occasion() is None


def test_calendar_items_do_not_get_adopted_as_a_plan(tmp_path, monkeypatch):
    from argon.services import agenda

    service, _ = _service(tmp_path, monkeypatch, _at(16, 30), tasks=0)
    monkeypatch.setattr(agenda, "upcoming", lambda ws: [{
        "id": "dentist", "summary": "Dentist", "start": _at(18), "end": None,
        "kind": "event",
    }])
    monkeypatch.setattr(agenda, "schoolwork", lambda ws: [])

    assert service.pick_occasion().kind == "daily_brief"
    assert service._plan.blocks() == []


def test_evening_does_not_add_an_automatic_wrap_up(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, monkeypatch, _at(20, 30))
    service._plan.set_blocks([{"start": "09:00", "end": "10:00", "what": "Gym"}])
    assert service.pick_occasion() is None


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


async def test_tick_delivers_a_validated_candidate_once(tmp_path, monkeypatch):
    delivered = []

    async def speak(_prompt: str) -> str:
        return "hey, how'd the pset go?"

    async def deliver(text: str, **_kw) -> bool:
        delivered.append(text)
        return True

    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), handler=speak, on_deliver=deliver)

    assert await service.tick() == "hey, how'd the pset go?"
    assert delivered == ["hey, how'd the pset go?"]


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
    assert "write what you would actually send him" in prompt.lower()
    assert "the message itself and nothing else" in prompt.lower()
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


def test_a_free_stretch_does_not_trigger_an_automatic_message(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 30), tasks=3)
    service.ledger.record_fired("daily_brief", clock.now)
    service._plan.set_blocks([{"start": "21:00", "end": "22:00", "what": "Gym"}])
    assert service.pick_occasion() is None


def test_the_brief_does_not_run_when_sources_have_no_verified_material(
    tmp_path, monkeypatch
):
    from argon.services import agenda

    service, _ = _service(tmp_path, monkeypatch, _at(17, 30), tasks=-1)
    monkeypatch.setattr(agenda, "upcoming", lambda ws: [])
    monkeypatch.setattr(agenda, "schoolwork", lambda ws: [])
    assert service.pick_occasion() is None


def test_a_block_end_does_not_interrupt_work(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 0), tasks=0)
    service._plan.set_blocks([{"start": "17:00", "end": "19:00", "what": "SAT prep"}])
    _mode(service, "working")
    clock.advance(120)          # 19:00 — the block he set is over
    assert service.pick_occasion() is None


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


async def test_a_suppressed_reword_never_reaches_the_delivery_callback(tmp_path, monkeypatch):
    delivered = []

    async def reword(_prompt: str) -> str:
        return "Ready to lock in a session for the project due next week?"

    async def deliver(text: str, **_kw) -> bool:
        delivered.append(text)
        return True

    service, clock = _service(
        tmp_path, monkeypatch, _at(17, 30), handler=reword, tasks=3, on_deliver=deliver,
    )
    service.ledger.record_said(
        "idle",
        "Project due next week—let's lock in a session to start it.",
        clock.now - timedelta(hours=2),
    )

    assert await service.tick() == ""
    assert delivered == []


# ---------------------------------------------------------------------------
# The plan is the schedule — both of these were found by walking a whole
# simulated day through the gate at ten-minute ticks.
# ---------------------------------------------------------------------------


def test_free_stretches_never_earn_automatic_messages(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(16, 30))
    service.ledger.record_fired("daily_brief", clock.now)
    service._plan.set_blocks([{"start": "19:00", "end": "20:00", "what": "Math"}])

    for _ in range(9):  # 16:30 -> 18:00, the whole gap
        assert service.pick_occasion() is None
        clock.advance(10)


def test_the_daily_cap_applies_to_his_own_schedule(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(18, 55), max_per_day=2)
    service._plan.set_blocks([{"start": "19:00", "what": "UCLA lab reading"}])
    for i in range(4):
        service.ledger.record_said("brief", "message {}".format(i), clock.now)

    clock.advance(10)
    assert service.pick_occasion() is None


def test_a_planned_start_respects_the_shared_sixty_minute_gap(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(19, 0))
    service._plan.set_blocks([{"start": "19:00", "what": "UCLA lab reading"}])
    service.ledger.record_said("brief", "message", clock.now - timedelta(minutes=30))

    assert service.pick_occasion() is None


def test_the_cap_still_binds_discretionary_messages(tmp_path, monkeypatch):
    service, clock = _service(tmp_path, monkeypatch, _at(17, 0), max_per_day=2)
    service._plan.set_blocks([{"start": "17:00", "end": "18:00", "what": "Gym"}])
    for i in range(3):
        service.ledger.record_said("brief", "message {}".format(i), clock.now)

    clock.advance(30)
    assert service.pick_occasion() is None


def test_a_plan_block_starting_is_not_a_reason_to_speak(tmp_path, monkeypatch):
    """He works from a list, not a timetable.

    There used to be a `block_start` occasion, and it did not fit how he works.
    A block boundary arriving while he was still asleep announced the start of
    work that was not starting, and he had to correct it. Writing a time down is
    not the same as beginning — starting and finishing are things he says.
    """
    service, clock = _service(tmp_path, monkeypatch, _at(19, 0))
    service._plan.set_blocks([
        {"start": "17:00", "end": "19:00", "what": "SAT prep"},
        {"start": "19:00", "end": "21:00", "what": "Math homework"},
    ])

    seen = []
    for _ in range(3):  # 19:00, 19:10, 19:20 — a block starts inside this window
        if occasion := service.pick_occasion():
            seen.append(occasion.kind)
        clock.advance(10)

    # The after-school brief may still fire here — that is its window and it has
    # material. What must never appear is anything triggered by the block itself.
    assert "block_start" not in seen, "a block boundary is not an occasion"
    assert set(seen) <= {"daily_brief"}
    assert "block_start" not in reminder_mod.OCCASIONS


def test_calendar_commitments_do_not_become_plan_blocks(tmp_path, monkeypatch):
    from argon.services import agenda

    service, _ = _service(tmp_path, monkeypatch, _at(16, 30))
    monkeypatch.setattr(agenda, "upcoming", lambda ws: [
        {"summary": "Start Math homework", "start": _at(19), "end": None,
         "kind": "reminder"},
        {"summary": "Start UCLA work", "start": _at(21), "end": None,
         "kind": "reminder"},
    ])

    assert service.pick_occasion().kind == "daily_brief"
    assert service._plan.blocks() == []
