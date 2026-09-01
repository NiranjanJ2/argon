"""The evening watch: when it wakes the agent, and when it leaves him alone.

The old service had nine tests, all of them about whether HEARTBEAT.md was
empty. Nothing covered the tick, the decision, or delivery — which is how a
watch that never once woke the agent ran 1,531 times in a month with nobody
noticing.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from argon.services.heartbeat import HeartbeatService

LA = ZoneInfo("America/Los_Angeles")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 1, hour, minute, tzinfo=LA)


@pytest.fixture
def watch(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    from argon.core import store

    store.reset_for_tests()

    spoke: list[tuple[str, str | None]] = []
    ran: list[str] = []

    async def execute(prompt: str) -> str:
        ran.append(prompt)
        return "THINKING: he is idle\nMESSAGE: Locked you in for 45 minutes."

    async def notify(text: str, *, key: str | None = None) -> None:
        spoke.append((text, key))

    service = HeartbeatService(
        workspace=tmp_path, provider=None, model="unused",
        on_execute=execute, on_notify=notify,
        timezone="America/Los_Angeles",
    )
    service.spoke, service.ran = spoke, ran
    yield service
    store.reset_for_tests()


def _now(monkeypatch, when: datetime) -> None:
    from argon import clock

    monkeypatch.setattr(clock, "now", lambda: when)
    monkeypatch.setattr(clock, "today_key", lambda *a, **k: when.strftime("%Y-%m-%d"))


def _situation(watch, monkeypatch, **over):
    base = {"mode": "idle", "current_task": None, "started_today": False,
            "due_now": [{"title": "HW 12", "due_when": "Tue 09/01"}],
            "shielded": False, "phone": "converged", "override": False,
            "before_start": False}
    base.update(over)
    monkeypatch.setattr(watch, "_situation", lambda: base)
    return base


# -- the window -------------------------------------------------------------

@pytest.mark.parametrize("hour,inside", [
    (9, False), (15, False), (16, True), (20, True), (23, True), (0, False), (3, False),
])
def test_the_watch_is_his_evening_only(watch, monkeypatch, hour, inside):
    _now(monkeypatch, _at(hour))
    assert watch._in_window() is inside


@pytest.mark.asyncio
async def test_outside_the_window_nothing_happens(watch, monkeypatch):
    _now(monkeypatch, _at(9))
    _situation(watch, monkeypatch)

    await watch._tick()

    assert watch.ran == [] and watch.spoke == []


# -- the decision -----------------------------------------------------------

@pytest.mark.parametrize("mode", ["working", "lock_in"])
@pytest.mark.asyncio
async def test_being_mid_work_is_never_an_occasion(watch, monkeypatch, mode):
    """The one lesson every deleted nudge in this codebase taught."""
    _now(monkeypatch, _at(20))
    _situation(watch, monkeypatch, mode=mode)

    await watch._tick()

    assert watch.ran == []


@pytest.mark.parametrize("mode", ["napping", "done"])
@pytest.mark.asyncio
async def test_napping_and_done_are_left_alone(watch, monkeypatch, mode):
    _now(monkeypatch, _at(17))
    _situation(watch, monkeypatch, mode=mode)

    await watch._tick()

    assert watch.ran == []


@pytest.mark.asyncio
async def test_an_empty_board_is_not_an_occasion(watch, monkeypatch):
    _now(monkeypatch, _at(20))
    _situation(watch, monkeypatch, due_now=[])

    await watch._tick()

    assert watch.ran == []


@pytest.mark.asyncio
async def test_idle_with_work_due_wakes_the_agent(watch, monkeypatch):
    _now(monkeypatch, _at(20))
    _situation(watch, monkeypatch)

    await watch._tick()

    assert len(watch.ran) == 1
    prompt = watch.ran[0]
    # Handed the state, not told to go and look: the model reliably skips an
    # optional tool call, and this watch cannot afford to guess.
    assert "HW 12" in prompt
    assert "not working on anything" in prompt
    assert watch.spoke[0][0] == "Locked you in for 45 minutes."


# -- delivery ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_it_may_act_and_stay_quiet(watch, monkeypatch):
    """Starting a focus session is the point; narrating it is optional."""
    async def silent(_prompt):
        return "THINKING: already handled\nMESSAGE: SKIP"

    watch.on_execute = silent
    _now(monkeypatch, _at(20))
    _situation(watch, monkeypatch)

    await watch._tick()

    assert watch.spoke == []


@pytest.mark.asyncio
async def test_at_most_one_message_an_hour(watch, monkeypatch):
    """The outbox dedupes on this key. A 30-minute watch must not become the
    twelve-message evening the check-in nudges already were."""
    _situation(watch, monkeypatch)

    _now(monkeypatch, _at(20, 0))
    await watch._tick()
    _now(monkeypatch, _at(20, 30))
    await watch._tick()
    _now(monkeypatch, _at(21, 0))
    await watch._tick()

    keys = [k for _, k in watch.spoke]
    assert keys == ["heartbeat:2026-09-01:20", "heartbeat:2026-09-01:20",
                    "heartbeat:2026-09-01:21"]
    assert len(set(keys)) == 2   # two distinct hours, so two deliveries survive


@pytest.mark.asyncio
async def test_a_reasoning_only_reply_is_never_delivered(watch, monkeypatch):
    async def thinks(_prompt):
        return "THINKING: he seems busy, I will not interrupt"

    watch.on_execute = thinks
    _now(monkeypatch, _at(20))
    _situation(watch, monkeypatch)

    await watch._tick()

    assert watch.spoke == []


@pytest.mark.asyncio
async def test_an_emergency_override_stands_the_watch_down(watch, monkeypatch):
    """"Let me out" has to stick.

    Without this the watch re-locks on the next tick, and pulling the release
    becomes something he has to keep doing — the nagging failure, with a shield
    attached.
    """
    _now(monkeypatch, _at(20))
    _situation(watch, monkeypatch, override=True)

    await watch._tick()

    assert watch.ran == [] and watch.spoke == []


@pytest.mark.asyncio
async def test_it_does_not_push_him_before_his_own_start_time(watch, monkeypatch):
    """He gets home at four and naps. Not working before the time he chose is
    the plan, not a lapse.

    `napping` cannot cover this: the mode has to be set by hand and has never
    once been set in a month of running, so a guard resting on it is dead code.
    """
    _now(monkeypatch, _at(16, 30))
    _situation(watch, monkeypatch, before_start=True)

    await watch._tick()

    assert watch.ran == [] and watch.spoke == []


@pytest.mark.asyncio
async def test_after_his_start_time_it_pushes(watch, monkeypatch):
    _now(monkeypatch, _at(21, 0))
    _situation(watch, monkeypatch, before_start=False)

    await watch._tick()

    assert len(watch.ran) == 1


class TestTheExpensiveReadComesLast:
    """Classroom is cached for 120s and the tick is far longer, so every tick
    that reaches the board pays for a fresh crawl. The local reads get to say
    no first — otherwise halving the interval doubles the Google traffic for
    answers that could not have changed the outcome."""

    def _watch(self, tmp_path, monkeypatch, **state):
        crawls = []
        service = HeartbeatService(workspace=tmp_path, provider=None, model="x")
        monkeypatch.setattr(service, "_due_now",
                            lambda: crawls.append(1) or [{"title": "HW 12"}])
        base = {"mode": "idle", "started_today": False, "override": False,
                "before_start": False}
        base.update(state)
        for k, v in base.items():
            monkeypatch.setattr(type(service), "_probe_" + k, property(lambda s, v=v: v),
                                raising=False)
        return service, crawls, base

    @pytest.mark.parametrize("state", [
        {"before_start": True},
        {"override": True},
        {"mode": "working"},
        {"mode": "lock_in"},
        {"mode": "napping"},
        {"mode": "done"},
    ])
    def test_a_cheap_no_never_touches_the_board(self, tmp_path, monkeypatch, state):
        service, crawls, base = self._watch(tmp_path, monkeypatch, **state)

        # Drive the real _situation with the local reads stubbed out.
        monkeypatch.setattr("argon.productivity.state.DailyState.get",
                            lambda self: {"mode": base["mode"], "current_task": None})
        monkeypatch.setattr("argon.ios.mode.override_status", lambda: (base["override"], None))
        monkeypatch.setattr("argon.ios.mode.get_actual", lambda: {"shielded": False})
        monkeypatch.setattr("argon.ios.mode.convergence", lambda: ("converged", ""))
        monkeypatch.setattr("argon.planner.start_time",
                            lambda: "23:59" if base["before_start"] else "00:01")

        situation = service._situation()

        assert crawls == [], f"{state} should not have crawled the board"
        assert service._decide(situation) is None

    def test_an_evening_that_could_act_does_read_the_board(self, tmp_path, monkeypatch):
        service, crawls, base = self._watch(tmp_path, monkeypatch)

        monkeypatch.setattr("argon.productivity.state.DailyState.get",
                            lambda self: {"mode": "idle", "current_task": None})
        monkeypatch.setattr("argon.ios.mode.override_status", lambda: (False, None))
        monkeypatch.setattr("argon.ios.mode.get_actual", lambda: {"shielded": False})
        monkeypatch.setattr("argon.ios.mode.convergence", lambda: ("converged", ""))
        monkeypatch.setattr("argon.planner.start_time", lambda: "00:01")

        situation = service._situation()

        assert crawls == [1]
        assert service._decide(situation) == "nothing is running and work is due"
