"""Gateway wiring.

The CLI parses arguments; this module owns the object graph. Keeping them apart
is why `argon gateway` is now a dozen lines instead of three hundred.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from loguru import logger

from argon.channels.manager import ChannelManager
from argon.config import Config
from argon.core.bus import MessageBus, OutboundMessage
from argon.core.loop import AgentLoop
from argon.core.session import SessionManager
from argon.paths import get_cron_store
from argon.providers.base import GenerationSettings, LLMProvider
from argon.providers.openai_compat import OpenAICompatProvider
from argon.providers.registry import find_by_name
from argon.services.cron import CronJob, CronService
from argon.services.heartbeat import HeartbeatService
from argon.services.reminder import ReminderService
from argon.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE


def build_provider(config: Config, *, provider_name: str | None = None,
                   model: str | None = None) -> LLMProvider:
    """Construct the LLM client for a named provider entry."""
    defaults = config.agents.defaults
    name, p = config.resolve_provider(provider_name)
    provider = OpenAICompatProvider(
        api_key=p.api_key or None,
        api_base=config.api_base_for(name),
        default_model=model or defaults.model,
        fallback_model=defaults.fallback_model or None,
        extra_headers=p.extra_headers or None,
        spec=find_by_name(name),
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
    # Resolves the channel/chat to use for messages Niranjan did not initiate.
    pick_target: Callable[[], tuple[str, str]]


def build_runtime(config: Config) -> Runtime:
    bus = MessageBus()
    sessions = SessionManager(config.workspace_path)
    provider = build_provider(config)
    cron = CronService(get_cron_store())

    agent = AgentLoop(config, bus, provider, cron_service=cron, session_manager=sessions)

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

    channels = ChannelManager(config, bus)

    def pick_target() -> tuple[str, str]:
        """Most recently used real channel, for unprompted messages."""
        enabled = set(channels.enabled_channels)
        for item in sessions.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system", "heartbeat"} or not chat_id:
                continue
            if channel in enabled:
                return channel, chat_id
        return "cli", "direct"

    async def on_cron_job(job: CronJob) -> str | None:
        from argon.tools.cron import CronTool
        from argon.tools.message import MessageTool
        from argon.utils.evaluator import evaluate_response

        note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )
        cron_tool = agent.tools.get("cron")
        token = cron_tool.set_cron_context(True) if isinstance(cron_tool, CronTool) else None
        try:
            resp = await agent.process_direct(
                note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and token is not None:
                cron_tool.reset_cron_context(token)

        response = resp.content if resp else ""
        if response == EMPTY_FINAL_RESPONSE_MESSAGE:
            return None  # model chose silence; don't leak the placeholder

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            if await evaluate_response(response, job.payload.message, provider, agent.model):
                await bus.publish_outbound(OutboundMessage(
                    channel=job.payload.channel or "cli",
                    chat_id=job.payload.to,
                    content=response,
                ))
        return response

    cron.on_job = on_cron_job

    async def run_background_turn(prompt: str) -> str:
        """Run a turn that the user did not initiate, on the cheap model."""
        channel, chat_id = pick_target()

        async def _silent(*_a, **_kw):
            pass

        resp = await heartbeat_agent.process_direct(
            prompt, session_key="heartbeat", channel=channel, chat_id=chat_id,
            on_progress=_silent,
        )
        session = heartbeat_agent.sessions.get_or_create("heartbeat")
        session.retain_recent_legal_suffix(hb_cfg.keep_recent_messages)
        heartbeat_agent.sessions.save(session)
        return resp.content if resp else ""

    async def notify(response: str) -> None:
        channel, chat_id = pick_target()
        if channel == "cli":
            return  # nowhere to deliver
        await bus.publish_outbound(
            OutboundMessage(channel=channel, chat_id=chat_id, content=response)
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

    async def on_check_in(prompt: str) -> None:
        response = await run_background_turn(prompt)
        if response and response != EMPTY_FINAL_RESPONSE_MESSAGE:
            await notify(response)

    reminder = ReminderService(
        workspace=config.workspace_path,
        timezone=config.agents.defaults.timezone,
        on_check_in=on_check_in,
    )

    return Runtime(
        config=config, bus=bus, agent=agent, heartbeat_agent=heartbeat_agent,
        cron=cron, channels=channels, heartbeat=heartbeat, reminder=reminder,
        pick_target=pick_target,
    )


async def run(rt: Runtime) -> None:
    """Run the gateway until interrupted."""
    try:
        await rt.cron.start()
        await rt.heartbeat.start()
        await rt.reminder.start()
        await asyncio.gather(rt.agent.run(), rt.channels.start_all())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down")
    finally:
        await rt.agent.close_mcp()
        rt.reminder.stop()
        rt.heartbeat.stop()
        rt.cron.stop()
        rt.agent.stop()
        await rt.channels.stop_all()
