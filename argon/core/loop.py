"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import AsyncExitStack, nullcontext
from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from argon.config import Config
from argon.core import target
from argon.core.bus import InboundMessage, MessageBus, OutboundMessage
from argon.core.commands import CommandContext, CommandRouter, register_builtin_commands
from argon.core.context import ContextBuilder
from argon.core.hook import AgentHook, AgentHookContext, CompositeHook
from argon.core.memory import MemoryConsolidator
from argon.core.runner import AgentRunner, AgentRunSpec
from argon.core.session import Session, SessionManager
from argon.core.skills import BUILTIN_SKILLS_DIR
from argon.providers.base import LLMProvider
from argon.tools.cron import CronTool
from argon.tools.fs import ReadFileTool
from argon.tools.message import MessageTool
from argon.tools.registry import ToolRegistry
from argon.tools.web import WebFetchTool, WebSearchTool
from argon.utils.helpers import image_placeholder_text, truncate_text
from argon.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

#: Session the reminder runs its own check-in prompts on. Those messages are
#: written by Argon, not by Niranjan, and must never be journalled as his words.
CHECK_IN_SESSION = "heartbeat"

if TYPE_CHECKING:
    from argon.services.cron import CronService


class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
        self,
        agent_loop: AgentLoop,
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> None:
        self._loop = agent_loop
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from argon.utils.helpers import strip_think

        prev_clean = strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean):]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._on_progress:
            if not self._on_stream:
                thought = self._loop._strip_think(
                    context.response.content if context.response else None
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = self._loop._strip_think(self._loop._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
            self._loop._journal_tool(tc.name, tc.arguments)
        self._loop._set_tool_context(self._channel, self._chat_id, self._message_id)

    async def after_iteration(self, context: AgentHookContext) -> None:
        u = context.usage or {}
        logger.debug(
            "LLM usage: prompt={} completion={} cached={}",
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            u.get("cached_tokens", 0),
        )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._loop._strip_think(content)


class _LoopHookChain(AgentHook):
    """Run the core hook before extra hooks."""

    __slots__ = ("_primary", "_extras")

    def __init__(self, primary: AgentHook, extra_hooks: list[AgentHook]) -> None:
        self._primary = primary
        self._extras = CompositeHook(extra_hooks)

    def wants_streaming(self) -> bool:
        return self._primary.wants_streaming() or self._extras.wants_streaming()

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._primary.before_iteration(context)
        await self._extras.before_iteration(context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._primary.on_stream(context, delta)
        await self._extras.on_stream(context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._primary.on_stream_end(context, resuming=resuming)
        await self._extras.on_stream_end(context, resuming=resuming)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._primary.before_execute_tools(context)
        await self._extras.before_execute_tools(context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._primary.after_iteration(context)
        await self._extras.after_iteration(context)

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        content = self._primary.finalize_content(context, content)
        return self._extras.finalize_content(context, content)


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"

    def __init__(
        self,
        config: Config,
        bus: MessageBus,
        provider: LLMProvider,
        *,
        model: str | None = None,
        cron_service: CronService | None = None,
        session_manager: SessionManager | None = None,
        hooks: list[AgentHook] | None = None,
        register_tools: bool = True,
    ):
        """Build a loop from config.

        *model* overrides ``agents.defaults.model`` — the heartbeat runs on a
        cheaper model than interactive chat, and that is the only difference
        between the two loops the gateway creates.
        """
        defaults = config.agents.defaults
        self.config = config
        self.bus = bus
        self.channels_config = config.channels
        self.provider = provider
        self.workspace = config.workspace_path
        self.model = model or defaults.model
        self.max_iterations = defaults.max_tool_iterations
        self.context_window_tokens = defaults.context_window_tokens
        self.context_block_limit = defaults.context_block_limit
        self.max_tool_result_chars = defaults.max_tool_result_chars
        self.provider_retry_mode = defaults.provider_retry_mode
        self.web_config = config.tools.web
        self.exec_config = config.tools.exec
        self.cron_service = cron_service
        self.restrict_to_workspace = config.tools.restrict_to_workspace
        self.google_enabled = config.google.enabled
        self._start_time = time.time()
        self._last_usage: dict[str, int] = {}
        self._extra_hooks: list[AgentHook] = hooks or []

        self.context = ContextBuilder(self.workspace, timezone=defaults.timezone)
        self.sessions = session_manager or SessionManager(self.workspace)
        self.tools = ToolRegistry()
        self.runner = AgentRunner(provider)

        self.idle_session_reset_hours = defaults.idle_session_reset_hours
        self.max_session_messages = defaults.max_session_messages
        self._running = False
        self._mcp_servers = config.tools.mcp_servers
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._background_tasks: list[asyncio.Task] = []
        self._session_locks: dict[str, asyncio.Lock] = {}
        # ARGON_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("ARGON_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        self.memory_consolidator = MemoryConsolidator(
            workspace=self.workspace,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=self.context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            max_completion_tokens=provider.generation.max_tokens,
        )
        if register_tools:
            self._register_default_tools()
        self.commands = CommandRouter()
        register_builtin_commands(self.commands)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        # Read-only filesystem — Argon doesn't need write/edit/exec/spawn
        self.tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
        if self.web_config.enable:
            self.tools.register(WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy))
            self.tools.register(WebFetchTool(proxy=self.web_config.proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )
        self._register_productivity_tools()
        if self.google_enabled:
            self._register_google_tools()

    def _register_productivity_tools(self) -> None:
        """Register schedule, task, status, log, and memory tools."""
        from argon.google.tasks_store import GoogleTasksStore
        from argon.productivity.habits import HabitsTracker
        from argon.productivity.log import DailyLog
        from argon.productivity.state import DailyState
        from argon.tools.bell import ScheduleTool
        from argon.tools.overview import GetDailyOverviewTool
        from argon.tools.status import GetStatusTool, LogNoteTool, ReadLogTool, SetModeTool
        from argon.tools.tasks import (
            AddTaskTool,
            CompleteTaskTool,
            ListTasksTool,
            StartTaskTool,
            UpdateTaskTool,
        )

        store = GoogleTasksStore(self.workspace)
        state = DailyState(self.workspace)
        log = DailyLog(self.workspace)
        habits = HabitsTracker(self.workspace)
        log.refresh_symlink()

        self.tools.register(ScheduleTool(self.workspace))

        # Task tools
        self.tools.register(ListTasksTool(store, state, self.workspace))
        self.tools.register(AddTaskTool(store, log))
        self.tools.register(StartTaskTool(store, state, log))
        self.tools.register(CompleteTaskTool(store, state, log, habits))
        self.tools.register(UpdateTaskTool(store))

        # Status and daily log
        self.tools.register(GetStatusTool(state, self.workspace))
        self.tools.register(SetModeTool(state, log, habits))
        self.tools.register(LogNoteTool(state, log))
        self.tools.register(ReadLogTool(log))

        # Threads: the things with a history, so a project mentioned three
        # weeks later means something. See argon/core/threads.py.
        from argon.tools.threads import ReadThreadTool, TrackThreadTool

        self.tools.register(TrackThreadTool(self.workspace))
        self.tools.register(ReadThreadTool(self.workspace))

        # The day's plan drives when Argon reaches out at all — see
        # argon/productivity/plan.py.
        from argon.productivity.plan import DayPlan
        from argon.tools.plan import GetDayPlanTool, SetDayPlanTool, UpdatePlanBlockTool

        day_plan = DayPlan(self.workspace)
        self.tools.register(SetDayPlanTool(day_plan))
        self.tools.register(UpdatePlanBlockTool(day_plan))
        self.tools.register(GetDayPlanTool(day_plan))

        # Always available — handles missing Google auth gracefully
        self.tools.register(GetDailyOverviewTool(self.workspace))

        # The legacy mail -> SMS -> Shortcut lockdown is deliberately NOT
        # registered. It takes no duration, no reason and no confirmation, and
        # the model reached for it over set_focus_mode: told "today I want to
        # lock in for SAT prep" — a plan, not a command — it locked the phone
        # on the spot at 1:37 AM. The path is also dead, since the `trigger`
        # Google account is intentionally expired. set_focus_mode is the one
        # way in, and it is bounded and reversible.

        # Screen Time via the iOS app. Registered unconditionally: the tool
        # reports when the phone has never checked in, which is more useful to
        # the model than the tool silently not existing.
        from argon.tools.focus import SetFocusModeTool
        self.tools.register(SetFocusModeTool(self.config.ios.default_lock_minutes, state))

        # "Rest day" has to land somewhere the check-in gate reads.
        from argon.tools.quiet import CheckInStatusTool, SnoozeCheckInsTool
        self.tools.register(SnoozeCheckInsTool(self.workspace))
        self.tools.register(CheckInStatusTool(self.workspace))

        # Writing down what Niranjan says, so it need never be invented.
        from argon.tools.memory import RecallTool, RememberTool
        self.tools.register(RememberTool(self.workspace))
        self.tools.register(RecallTool(self.workspace))

    def _register_google_tools(self) -> None:
        """Register Google API tools unconditionally.

        These used to be registered only if ``get_credentials`` succeeded, so an
        expired token made calendar and classroom silently cease to exist —
        Argon answered as though it had never had those features. They went dead
        in April and nobody noticed until July. Now the tools always exist and an
        unauthenticated one explains itself and names the fix.
        """
        from argon.google.service import google_tools

        for tool in google_tools(self.workspace):
            self.tools.register(tool)

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from argon.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        from argon.utils.helpers import strip_think
        return strip_think(text) or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
        *,
        session: Session | None = None,
        channel: str = "cli",
        chat_id: str = "direct",
        message_id: str | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop.

        *on_stream*: called with each content delta during streaming.
        *on_stream_end(resuming)*: called when a streaming session finishes.
        ``resuming=True`` means tool calls follow (spinner should restart);
        ``resuming=False`` means this is the final response.
        """
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
        )
        hook: AgentHook = (
            _LoopHookChain(loop_hook, self._extra_hooks)
            if self._extra_hooks
            else loop_hook
        )

        async def _checkpoint(payload: dict[str, Any]) -> None:
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            checkpoint_callback=_checkpoint,
        ))
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                # Preserve real task cancellation so shutdown can complete cleanly.
                # Only ignore non-task CancelledError signals that may leak from integrations.
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                ctx = CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, loop=self)
                result = await self.commands.dispatch_priority(ctx)
                if result:
                    await self.bus.publish_outbound(result)
                continue
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(msg.session_key, []).append(task)
            task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message: per-session serial, cross-session concurrent."""
        lock = self._session_locks.setdefault(msg.session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()
        async with lock, gate:
            try:
                on_stream = on_stream_end = None
                if msg.metadata.get("_wants_stream"):
                    # Split one answer into distinct stream segments.
                    stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                    stream_segment = 0

                    def _current_stream_id() -> str:
                        return f"{stream_base_id}:{stream_segment}"

                    async def on_stream(delta: str) -> None:
                        meta = dict(msg.metadata or {})
                        meta["_stream_delta"] = True
                        meta["_stream_id"] = _current_stream_id()
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content=delta,
                            metadata=meta,
                        ))

                    async def on_stream_end(*, resuming: bool = False) -> None:
                        nonlocal stream_segment
                        meta = dict(msg.metadata or {})
                        meta["_stream_end"] = True
                        meta["_resuming"] = resuming
                        meta["_stream_id"] = _current_stream_id()
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="",
                            metadata=meta,
                        ))
                        stream_segment += 1

                response = await self._process_message(
                    msg, on_stream=on_stream, on_stream_end=on_stream_end,
                )
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def close_mcp(self) -> None:
        """Drain pending background archives, then close MCP connections."""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def _schedule_background(self, coro) -> None:
        """Schedule a coroutine as a tracked background task (drained on shutdown)."""
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _maybe_reset_idle_session(self, session: Session) -> None:
        """Consolidate and clear session if it has been idle past the threshold.

        Uses the last message timestamp (not session.updated_at, which resets to
        'now' on every disk load and is therefore unreliable for gap detection).
        """
        if self.idle_session_reset_hours <= 0 or not session.messages:
            return
        last_ts_str = session.messages[-1].get("timestamp")
        if not last_ts_str:
            return
        try:
            last_time = datetime.fromisoformat(last_ts_str)
        except (ValueError, TypeError):
            return
        idle_seconds = (datetime.now() - last_time).total_seconds()
        if idle_seconds < self.idle_session_reset_hours * 3600:
            return
        # Snapshot unconsolidated messages, clear session immediately so the
        # new message is not blocked, then archive in the background.
        unconsolidated = session.messages[session.last_consolidated:]
        session.retain_recent_legal_suffix(0)
        self.sessions.save(session)
        logger.info(
            "Conversation boundary: session {} reset after {:.0f}m gap",
            session.key, idle_seconds / 60,
        )
        if unconsolidated:
            self._schedule_background(
                self.memory_consolidator.archive_messages(unconsolidated)
            )

    # -- journalling -------------------------------------------------------
    #
    # The day page is what a check-in reads to know what already happened. It
    # used to have one writer — the model choosing to call `remember` — which it
    # almost never did, so the page was empty and every check-in re-derived the
    # day from the task list alone. That is why Argon sent three near-identical
    # "SAT prep is due" nudges in one morning. Recording is now automatic.

    @property
    def _journal(self) -> Any:
        from argon.core.journal import Journal

        if getattr(self, "_journal_cache", None) is None:
            self._journal_cache = Journal(self.workspace)
        return self._journal_cache

    def _journal_note(self, text: str, *, kind: str) -> None:
        """Append one line to today's page. Never allowed to break a turn."""
        try:
            self._journal.note(text, kind=kind)
        except Exception:  # noqa: BLE001
            logger.debug("Journal write failed", exc_info=True)

    def _journal_tool(self, name: str, arguments: dict[str, Any]) -> None:
        from argon.core.journal import describe_tool

        if line := describe_tool(name, arguments):
            self._journal_note(line, kind="did")

    def _journal_said(self, text: str, key: str) -> None:
        """Record what Niranjan actually said.

        Skips the check-in session: those prompts are written by Argon itself
        ("It's 9:51 PM and free time…"), and journalling them would feed its own
        nudges back as though they were his words.
        """
        if key == CHECK_IN_SESSION or not text.strip() or text.startswith("/"):
            return
        self._journal_note(text, kind="said")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            if self._restore_runtime_checkpoint(session):
                self.sessions.save(session)
            await self.memory_consolidator.maybe_consolidate_by_tokens(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            current_role = "assistant" if msg.sender_id == "subagent" else "user"
            messages = self.context.build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id,
                current_role=current_role,
            )
            final_content, _, all_msgs = await self._run_agent_loop(
                messages, session=session, channel=channel, chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
            )
            self._save_turn(session, all_msgs, 1 + len(history))
            self._clear_runtime_checkpoint(session)
            self.sessions.save(session)
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        # Where to send anything Niranjan did not ask for. Recorded here rather
        # than inferred from session files, which get archived and rotated.
        target.remember(self.workspace, msg.channel, msg.chat_id)

        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        if self._restore_runtime_checkpoint(session):
            self.sessions.save(session)

        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        await self._maybe_reset_idle_session(session)
        self._journal_said(msg.content, key)
        await self.memory_consolidator.maybe_consolidate_by_tokens(session)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        # Any tool that cares about turn boundaries gets told. `message` uses it
        # to avoid double-sending; `set_focus_mode` uses it to force a question
        # out to Niranjan before it can block his phone at night.
        for tool in (self.tools.get(name) for name in self.tools.tool_names):
            start = getattr(tool, "start_turn", None)
            if callable(start):
                start()

        history = session.get_history(max_messages=0)
        status_context: str | None = None
        try:
            from argon.productivity.state import DailyState
            _s = DailyState(self.workspace).get()
            _parts = [f"Mode: {_s.get('mode', 'idle')}"]
            if _s.get("current_task"):
                _parts.append(f"Task: {_s['current_task']}")
            status_context = "[Status] " + " | ".join(_parts)
        except Exception:
            pass
        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id,
            extra_context=status_context,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            session=session,
            channel=msg.channel, chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
        )

        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        # Don't forward raw provider errors (e.g. tool validation failures from
        # model hallucinating bad tool names) to the user as chat messages.
        if final_content and (
            "tool call validation failed" in final_content.lower()
            or "which was not in request.tools" in final_content.lower()
        ):
            logger.warning("Suppressing raw provider error from user output: {}", final_content[:200])
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        self._save_turn(session, all_msgs, 1 + len(history))
        self._clear_runtime_checkpoint(session)
        self.sessions.save(session)
        if self.max_session_messages > 0:
            self._schedule_background(
                self.memory_consolidator.consolidate_and_trim(session, self.max_session_messages)
            )
        else:
            self._schedule_background(self.memory_consolidator.maybe_consolidate_by_tokens(session))

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        if on_stream is not None:
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=meta,
        )

    def _sanitize_persisted_blocks(
        self,
        content: list[dict[str, Any]],
        *,
        do_truncate_text: bool = False,
        drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """Strip volatile multimodal payloads before writing session history."""
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                drop_runtime
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and block["text"].startswith("[Current Time:")
            ):
                continue

            if (
                block.get("type") == "image_url"
                and block.get("image_url", {}).get("url", "").startswith("data:image/")
            ):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if do_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, do_truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith("[Current Time:"):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """Persist the latest in-flight turn state into session metadata."""
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """Materialize an unfinished turn into session history before a new request."""
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "name": name,
                "content": "Error: Task interrupted before this tool finished.",
                "timestamp": datetime.now().isoformat(),
            })

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self._clear_runtime_checkpoint(session)
        return True

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_stream: Callable[[str], Awaitable[None]] | None = None,
        on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """Process a message directly and return the outbound payload."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        return await self._process_message(
            msg, session_key=session_key, on_progress=on_progress,
            on_stream=on_stream, on_stream_end=on_stream_end,
        )
