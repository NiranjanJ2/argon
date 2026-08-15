"""Background work must use the restricted background agent, never chat authority."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from argon.config import Config
from argon.core.bus import OutboundMessage
from argon.services.cron import CronJob, CronPayload, CronSchedule


@pytest.mark.asyncio
async def test_cron_and_checkins_run_on_the_background_agent_with_background_authority(
    tmp_path, monkeypatch
):
    from argon import runtime
    from argon.tools.registry import ToolRegistry

    class Loop:
        instances = []

        def __init__(self, _config, _bus, _provider, *, model=None, **_kwargs):
            self.model = model or "interactive"
            self.tools = ToolRegistry()
            self.calls = []
            self.sessions = SimpleNamespace(
                get_or_create=lambda _key: SimpleNamespace(retain_recent_legal_suffix=lambda _count: None),
                save=lambda _session: None,
            )
            Loop.instances.append(self)

        async def process_direct(self, content, **kwargs):
            self.calls.append((content, kwargs))
            return OutboundMessage(
                channel=kwargs.get("channel", "cli"),
                chat_id=kwargs.get("chat_id", "direct"),
                content="background reply",
            )

    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "AgentLoop", Loop)
    monkeypatch.setattr(runtime, "build_provider", lambda *_a, **_kw: object())
    monkeypatch.setattr(runtime, "get_cron_store", lambda: tmp_path / "cron.json")
    rt = runtime.build_runtime(Config(google={"enabled": False}))
    interactive, background = Loop.instances
    job = CronJob(
        id="job",
        name="reminder",
        payload=CronPayload(message="say hi", deliver=False, channel="discord", to="42"),
    )

    await rt.cron.on_job(job)
    await rt.reminder.on_check_in("check in")

    assert interactive.calls == []
    cron_call, checkin_call = background.calls
    assert "[Scheduled Task]" in cron_call[0]
    assert cron_call[1] == {
        "session_key": "cron:job", "channel": "discord", "chat_id": "42",
        "origin": "cron", "background": True,
    }
    assert checkin_call[0] == "check in"
    assert checkin_call[1]["session_key"] == "heartbeat"
    assert checkin_call[1]["origin"] == "checkin"
    assert checkin_call[1]["background"] is True


@pytest.mark.asyncio
async def test_background_turns_are_serialized_across_cron_checkin_and_heartbeat(
    tmp_path, monkeypatch
):
    from argon import runtime
    from argon.tools.registry import ToolRegistry

    entered = asyncio.Event()
    release = asyncio.Event()

    class Loop:
        instances = []

        def __init__(self, _config, _bus, _provider, *, model=None, **_kwargs):
            self.model = model or "interactive"
            self.tools = ToolRegistry()
            self.sessions = SimpleNamespace(
                get_or_create=lambda _key: SimpleNamespace(retain_recent_legal_suffix=lambda _count: None),
                save=lambda _session: None,
            )
            self.in_flight = 0
            self.max_in_flight = 0
            Loop.instances.append(self)

        async def process_direct(self, _content, **kwargs):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            entered.set()
            await release.wait()
            self.in_flight -= 1
            return OutboundMessage(channel=kwargs["channel"], chat_id=kwargs["chat_id"], content="reply")

    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "AgentLoop", Loop)
    monkeypatch.setattr(runtime, "build_provider", lambda *_a, **_kw: object())
    monkeypatch.setattr(runtime, "get_cron_store", lambda: tmp_path / "cron.json")
    rt = runtime.build_runtime(Config(google={"enabled": False}))
    background = Loop.instances[1]
    job = CronJob(id="job", name="reminder", payload=CronPayload(message="say hi", deliver=False))

    turns = asyncio.gather(
        rt.cron.on_job(job), rt.reminder.on_check_in("check in"), rt.heartbeat.on_execute("heartbeat"),
    )
    await entered.wait()
    await asyncio.sleep(0)
    assert background.max_in_flight == 1
    release.set()
    await turns


@pytest.mark.asyncio
async def test_an_explicit_reminder_is_delivered_verbatim_and_no_model_may_veto_it(
    tmp_path, monkeypatch
):
    """A second model used to hold a veto over a reminder Niranjan asked for.

    `on_cron_job` ran the scheduled text past `evaluate_response`, and a "no"
    meant nothing was sent — while cron still recorded the run as `ok`. An
    explicit reminder now takes the deterministic path: his own words, no model
    call, and success only once the channel says it arrived.
    """
    from argon import runtime
    from argon.tools.registry import ToolRegistry

    evaluated = []

    class Loop:
        instances = []

        def __init__(self, _config, _bus, _provider, *, model=None, **_kwargs):
            self.model = model or "interactive"
            self.tools = ToolRegistry()
            self.turns = []
            Loop.instances.append(self)

        async def process_direct(self, content, **kwargs):
            self.turns.append(content)
            return OutboundMessage(
                channel=kwargs["channel"], chat_id=kwargs["chat_id"], content="improvised",
            )

    async def evaluate(*args):
        evaluated.append(args)
        return False   # the veto that used to silence real reminders

    config = Config(google={"enabled": False})
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "AgentLoop", Loop)
    monkeypatch.setattr(runtime, "build_provider", lambda *_a, **_kw: object())
    monkeypatch.setattr(runtime, "get_cron_store", lambda: tmp_path / "cron.json")
    monkeypatch.setattr("argon.utils.evaluator.evaluate_response", evaluate)
    rt = runtime.build_runtime(config)

    delivered = []

    async def fake_deliver(**kwargs):
        from argon.core.outbox import Delivery

        delivered.append(kwargs)
        return Delivery(kwargs["key"], True, "sent")

    monkeypatch.setattr(rt.outbox, "deliver", fake_deliver)

    job = CronJob(id="job", name="reminder", schedule=CronSchedule(kind="at", at_ms=5_000),
                  payload=CronPayload(
                      kind="reminder", message="Sign the syllabus agreement",
                      deliver=True, channel="discord", to="42",
                  ))

    result = await rt.cron.on_job(job)

    assert evaluated == [], "no model gets a vote on an explicit reminder"
    assert Loop.instances[1].turns == [], "and no turn is spent re-improvising it"
    assert delivered[0]["content"] == "Sign the syllabus agreement"
    assert delivered[0]["key"] == "cron:job:5000"
    assert result.status == "ok" and result.delivered is True


@pytest.mark.asyncio
async def test_a_job_is_not_ok_when_the_channel_never_took_it(tmp_path, monkeypatch):
    """`ok` used to mean the callback returned, not that anything arrived."""
    from argon import runtime
    from argon.tools.registry import ToolRegistry

    class Loop:
        instances = []

        def __init__(self, _config, _bus, _provider, *, model=None, **_kwargs):
            self.model = model or "interactive"
            self.tools = ToolRegistry()
            Loop.instances.append(self)

        async def process_direct(self, _content, **kwargs):
            return OutboundMessage(
                channel=kwargs["channel"], chat_id=kwargs["chat_id"], content="the report",
            )

    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "AgentLoop", Loop)
    monkeypatch.setattr(runtime, "build_provider", lambda *_a, **_kw: object())
    monkeypatch.setattr(runtime, "get_cron_store", lambda: tmp_path / "cron.json")
    rt = runtime.build_runtime(Config(google={"enabled": False}))

    async def refuse(**kwargs):
        from argon.core.outbox import Delivery

        return Delivery(kwargs["key"], False, "failed", "discord refused")

    monkeypatch.setattr(rt.outbox, "deliver", refuse)

    job = CronJob(id="job", name="report", payload=CronPayload(
        message="report the build", deliver=True, channel="discord", to="42",
    ))

    result = await rt.cron.on_job(job)

    assert result.status == "error"
    assert result.delivered is False
    assert result.error == "discord refused"


@pytest.mark.asyncio
async def test_a_job_whose_model_said_nothing_is_skipped_not_completed(tmp_path, monkeypatch):
    from argon import runtime
    from argon.tools.registry import ToolRegistry
    from argon.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

    class Loop:
        instances = []

        def __init__(self, _config, _bus, _provider, *, model=None, **_kwargs):
            self.model = model or "interactive"
            self.tools = ToolRegistry()
            Loop.instances.append(self)

        async def process_direct(self, _content, **kwargs):
            return OutboundMessage(
                channel=kwargs["channel"], chat_id=kwargs["chat_id"],
                content=EMPTY_FINAL_RESPONSE_MESSAGE,
            )

    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "AgentLoop", Loop)
    monkeypatch.setattr(runtime, "build_provider", lambda *_a, **_kw: object())
    monkeypatch.setattr(runtime, "get_cron_store", lambda: tmp_path / "cron.json")
    rt = runtime.build_runtime(Config(google={"enabled": False}))

    job = CronJob(id="job", name="report", payload=CronPayload(
        message="report the build", deliver=True, channel="discord", to="42",
    ))

    result = await rt.cron.on_job(job)

    assert result.status == "skipped"
    assert result.delivered is False


@pytest.mark.asyncio
async def test_shutdown_stops_background_services_before_closing_both_agents():
    from argon import runtime

    order = []

    class Agent:
        async def run(self):
            raise asyncio.CancelledError

        async def close_mcp(self):
            order.append("close")

        def stop(self):
            order.append("agent-stop")

    class Service:
        async def start(self):
            pass

        def stop(self):
            order.append("service-stop")

    class Channels:
        async def start_all(self):
            pass

        async def stop_all(self):
            order.append("channels-stop")

    rt = SimpleNamespace(
        agent=Agent(), heartbeat_agent=Agent(), cron=Service(), heartbeat=Service(),
        reminder=Service(), channels=Channels(), outbox=Service(),
        maintenance=Service(),
    )

    await runtime.run(rt)

    assert order[:3] == ["service-stop", "service-stop", "service-stop"]
    assert order.count("close") == 2
