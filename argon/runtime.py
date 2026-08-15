"""Gateway wiring.

The CLI parses arguments; this module owns the object graph. Keeping them apart
is why `argon gateway` is now a dozen lines instead of three hundred.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger

from argon import clock
from argon.channels.manager import ChannelManager
from argon.config import Config
from argon.core import target
from argon.core.bus import MessageBus
from argon.core.loop import AgentLoop
from argon.core.outbox import Outbox
from argon.core.session import SessionManager
from argon.paths import get_cron_store
from argon.providers.base import GenerationSettings, LLMProvider
from argon.providers.openai_compat import OpenAICompatProvider
from argon.providers.registry import find_by_name
from argon.services.cron import CronJob, CronService, JobResult
from argon.services.heartbeat import HeartbeatService
from argon.services.maintenance import MaintenanceService
from argon.services.reminder import ReminderService
from argon.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_provider(config: Config, *, provider_name: str | None = None,
                   model: str | None = None, with_standby: bool = True) -> LLMProvider:
    """Construct the LLM client for a named provider entry.

    When a different provider is configured as the fallback and holds
    credentials, it is attached as a standby: a billing or quota refusal then
    degrades to the slower endpoint instead of taking Argon off the air.
    """
    defaults = config.agents.defaults
    name, p = config.resolve_provider(provider_name)

    standby = None
    wanted = defaults.fallback_provider
    if with_standby and wanted and wanted != name:
        candidate = config.providers.get(wanted)
        if candidate is not None and (candidate.api_key or candidate.api_base):
            standby = build_provider(
                config, provider_name=wanted, model=model, with_standby=False
            )
    provider = OpenAICompatProvider(
        api_key=p.api_key or None,
        api_base=config.api_base_for(name),
        default_model=model or defaults.model,
        fallback_model=defaults.fallback_model or None,
        extra_headers=p.extra_headers or None,
        spec=find_by_name(name),
        standby=standby,
    )
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider


@dataclass
class Runtime:
    """Everything the gateway needs, wired together."""

    config: Config
    bus: MessageBus
    agent: AgentLoop
    heartbeat_agent: AgentLoop
    cron: CronService
    channels: ChannelManager
    heartbeat: HeartbeatService
    reminder: ReminderService
    maintenance: MaintenanceService
    outbox: Outbox
    # Resolves the channel/chat to use for messages Niranjan did not initiate.
    pick_target: Callable[[], tuple[str, str]]
    # Carries out a button press. Exposed because it is the one mutation path
    # that belongs to him rather than to a turn, and the iOS app will want it.
    on_button: Callable[[dict[str, Any]], Any]


def build_runtime(config: Config) -> Runtime:
    clock.configure(config.agents.defaults.timezone)
    bus = MessageBus()
    sessions = SessionManager(config.workspace_path)
    provider = build_provider(config)
    cron = CronService(get_cron_store())
    # Every promise Argon makes to deliver something goes through here, and only
    # a real channel acknowledgement closes one. See argon/core/outbox.py.
    outbox = Outbox(bus.publish_outbound)

    agent = AgentLoop(config, bus, provider, cron_service=cron, session_manager=sessions)

    if config.google.enabled:
        from argon.google.auth import OPTIONAL_ACCOUNTS, GoogleAuth

        stale = {
            account: state
            for account, state in GoogleAuth(config.workspace_path).status().items()
            if state != "ok" and account not in OPTIONAL_ACCOUNTS
        }
        if stale:
            logger.warning(
                "Google accounts needing re-auth ({}): run `argon google-auth <account>`",
                ", ".join(f"{a}: {s}" for a, s in sorted(stale.items())),
            )

    # The heartbeat and check-ins run on a cheaper model so background chatter
    # never competes with interactive messages for rate limit.
    hb_cfg = config.gateway.heartbeat
    hb_provider = (
        build_provider(config, provider_name=hb_cfg.provider, model=hb_cfg.model)
        if hb_cfg.provider or hb_cfg.model
        else provider
    )
    hb_model = hb_cfg.model or config.agents.defaults.model
    heartbeat_agent = AgentLoop(
        config, bus, hb_provider, model=hb_model, session_manager=sessions
    )
    background_turn_lock = asyncio.Lock()

    channels = ChannelManager(config, bus, outbox=outbox)

    def pick_target() -> tuple[str, str]:
        """Where to deliver a message Niranjan did not ask for."""
        enabled = set(channels.enabled_channels)
        if remembered := target.recall(config.workspace_path, enabled):
            return remembered
        # Nothing recorded yet — fall back to the newest live session so a
        # freshly-updated install still delivers before the first message.
        newest = sorted(
            sessions.list_sessions(), key=lambda s: s.get("updated_at") or "", reverse=True
        )
        for item in newest:
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in target.UNREACHABLE or not chat_id:
                continue
            if channel in enabled:
                return channel, chat_id
        return "cli", "direct"

    async def on_cron_job(job: CronJob) -> JobResult:
        """Run one due job. Success means Niranjan actually received it.

        A reminder he asked for takes the deterministic path: his own words,
        delivered verbatim, with no model call and no evaluator entitled to
        veto it. An LLM once decided a scheduled reminder was not worth sending
        and cron recorded that as a successful run.
        """
        due_ms = job.schedule.at_ms or job.state.next_run_at_ms or _now_ms()
        key = f"cron:{job.id}:{int(due_ms)}"

        if job.payload.kind == "reminder":
            result = await outbox.deliver(
                key=key,
                channel=job.payload.channel or "",
                chat_id=job.payload.to or "",
                content=job.payload.message,
                due_at=due_ms / 1000,
                kind="reminder",
            )
            return JobResult(
                status="ok" if result.ok else "error",
                error=result.error,
                delivered=result.ok,
            )

        note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )
        async with background_turn_lock:
            resp = await heartbeat_agent.process_direct(
                note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
                origin="cron",
                background=True,
            )

        response = resp.content if resp else ""
        if not response or response == EMPTY_FINAL_RESPONSE_MESSAGE:
            # The model produced nothing. That is not a completed job, and
            # calling it one is how a job that never says anything looks healthy
            # forever.
            return JobResult(status="skipped", error="no output from the model")

        if not (job.payload.deliver and job.payload.to):
            return JobResult(status="ok")

        result = await outbox.deliver(
            key=key,
            channel=job.payload.channel or "",
            chat_id=job.payload.to,
            content=response,
            due_at=due_ms / 1000,
            kind="job",
        )
        return JobResult(
            status="ok" if result.ok else "error",
            error=result.error,
            delivered=result.ok,
        )

    cron.on_job = on_cron_job

    async def _run_background_turn(prompt: str) -> str:
        """Run a turn that the user did not initiate, on the cheap model."""
        channel, chat_id = pick_target()

        async def _silent(*_a, **_kw):
            pass

        resp = await heartbeat_agent.process_direct(
            prompt, session_key="heartbeat", channel=channel, chat_id=chat_id,
            on_progress=_silent,
            origin="checkin", background=True,
        )
        session = heartbeat_agent.sessions.get_or_create("heartbeat")
        session.retain_recent_legal_suffix(hb_cfg.keep_recent_messages)
        heartbeat_agent.sessions.save(session)
        return resp.content if resp else ""

    async def run_background_turn(prompt: str) -> str:
        """Serialize background work through its shared AgentLoop and tools."""
        async with background_turn_lock:
            return await _run_background_turn(prompt)

    async def on_button(action: dict[str, Any]) -> str:
        """A button was pressed. Do exactly that, with no model involved.

        This is the whole reason buttons are worth building. "yeah in a bit" has
        to be interpreted, and interpretation is where Argon decided he had
        started work he had not started. A press carries a verb chosen by code
        and a task id chosen by code, so the state change is precisely what he
        tapped — and `start_task`/`complete_task` stay interactive-only, because
        this is him acting, not automation acting for him.
        """
        from argon.google.tasks_store import GoogleTasksStore
        from argon.productivity.log import DailyLog
        from argon.productivity.state import DailyState

        verb = str(action.get("action") or "")
        task_id = str(action.get("task_id") or "")
        title = str(action.get("title") or "that")
        workspace = config.workspace_path
        state = DailyState(workspace)

        if verb == "start":
            store = GoogleTasksStore(workspace)
            task = await asyncio.to_thread(store.start_task, task_id)
            if task is None:
                return f"Couldn't find {title} any more — it may have been removed."
            state.start_session(task_id=task_id, title=task.get("title") or title)
            DailyLog(workspace).append(f"Started: {task.get('title') or title}", tag="task")
            return f"Started {task.get('title') or title}."

        if verb == "complete":
            store = GoogleTasksStore(workspace)
            session = state.get_session()
            minutes = session.get("elapsed_min") if session else None
            done = await asyncio.to_thread(store.complete_task, task_id, actual_min=minutes)
            if done is None:
                return f"Couldn't find {title} any more — it may already be done."
            state.end_session_if_task(task_id, title=done.get("title"))
            DailyLog(workspace).append(f"Completed: {done.get('title') or title}", tag="task")
            return f"Done — {done.get('title') or title} is off the list."

        if verb == "defer":
            # Not a state change. He answered, which is all the follow-up
            # wanted; the ledger already recorded that this item was asked
            # about, so nothing chases it again today.
            return f"Fine — leaving {title} for now."

        return f"Don't know how to do '{verb}'."

    async def notify(response: str, *, key: str | None = None, actions: Any = None) -> Any:
        """Deliver something Niranjan did not ask for. Returns whether it landed.

        This used to return None whether it sent, dropped, or failed — and the
        check-in ledger recorded "said" regardless. Two days of check-ins died
        here in silence while the log said "Check-in spoke".
        """
        channel, chat_id = pick_target()
        if channel == "cli":
            logger.warning(
                "No reachable channel — dropping an unprompted message: {}", response[:80]
            )
            return False
        return await outbox.deliver(
            key=key or f"notify:{int(_now_ms())}",
            channel=channel, chat_id=chat_id, content=response, kind="unprompted",
            actions=actions,
        )

    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=hb_provider,
        model=hb_model,
        on_execute=run_background_turn,
        on_notify=notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
        timezone=config.agents.defaults.timezone,
    )

    async def fold_day_into_memory(journal, day: str) -> None:
        """The one part of memory upkeep that needs a model.

        Which day, and whether one is due, belongs to MaintenanceService — and
        that service runs whether or not check-ins do. Consolidation used to
        ride the check-in loop, so turning off unsolicited messages also turned
        off remembering, silently.
        """
        from argon.core.journal import consolidate_day

        await consolidate_day(journal, hb_provider, hb_model, day)

    maintenance = MaintenanceService(config.workspace_path, fold_day_into_memory)

    async def on_check_in(prompt: str) -> str:
        """Generate a check-in candidate; ReminderService owns delivery policy."""
        async with background_turn_lock:
            response = await _run_background_turn(prompt)
            return "" if response == EMPTY_FINAL_RESPONSE_MESSAGE else response

    # A press goes straight to the domain operation, never through a turn.
    if (discord_channel := channels.get_channel("discord")) is not None:
        discord_channel.on_action = on_button

    checkin_cfg = config.gateway.checkins
    reminder = ReminderService(
        workspace=config.workspace_path,
        timezone=config.agents.defaults.timezone,
        on_check_in=on_check_in,
        on_deliver=notify,
        enabled=checkin_cfg.enabled,
        max_per_day=checkin_cfg.max_per_day,
        min_gap_minutes=checkin_cfg.min_gap_minutes,
        quiet_start_hour=checkin_cfg.quiet_start_hour,
        quiet_end_hour=checkin_cfg.quiet_end_hour,
        unprompted_from_hour=checkin_cfg.unprompted_from_hour,
    )

    return Runtime(
        config=config, bus=bus, agent=agent, heartbeat_agent=heartbeat_agent,
        cron=cron, channels=channels, heartbeat=heartbeat, reminder=reminder,
        maintenance=maintenance, outbox=outbox, pick_target=pick_target,
        on_button=on_button,
    )


async def run(rt: Runtime) -> None:
    """Run the gateway until interrupted."""
    try:
        await rt.outbox.start()
        await rt.cron.start()
        await rt.heartbeat.start()
        await rt.reminder.start()
        # Deliberately outside the check-in switch: remembering is not part of
        # the unsolicited-message policy.
        await rt.maintenance.start()
        await asyncio.gather(rt.agent.run(), rt.channels.start_all())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down")
    finally:
        rt.maintenance.stop()
        rt.reminder.stop()
        rt.heartbeat.stop()
        rt.cron.stop()
        rt.outbox.stop()
        closed_agents: set[int] = set()
        for agent in (rt.agent, rt.heartbeat_agent):
            if id(agent) not in closed_agents:
                closed_agents.add(id(agent))
                await agent.close_mcp()
        rt.agent.stop()
        await rt.channels.stop_all()
