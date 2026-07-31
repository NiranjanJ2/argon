"""Adaptive check-in service.

Replaces the old "Argon Productivity Reminder" cron row, which woke the model
every 5 minutes just to have it decide to stay silent — ~288 LLM calls a day,
almost all of them no-ops. Worse, it lived in the user-editable cron store, so
when it was deleted in May the whole proactive side of Argon silently stopped.

Now it is a plain service: local state decides *whether* it is worth waking the
model at all, and only then does a turn actually run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from loguru import logger

from argon.productivity.state import DailyState

CHECK_IN_PROMPT = (
    "Periodic check-in. Call get_status, then decide whether anything is worth "
    "saying to Niranjan right now. Staying silent is the normal outcome — only "
    "speak if there is a concrete reason."
)


class ReminderService:
    """Wakes the agent only when local state suggests it is worthwhile."""

    def __init__(
        self,
        workspace: Path,
        timezone: str,
        on_check_in: Callable[[str], Awaitable[Any]],
        *,
        enabled: bool = True,
    ) -> None:
        self.workspace = workspace
        self.tz = ZoneInfo(timezone)
        self.on_check_in = on_check_in
        self.enabled = enabled
        self._state = DailyState(workspace)
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_spoke_at: datetime | None = None

    # -- policy ------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    def interval_minutes(self) -> int:
        """How long to wait before the next evaluation."""
        mode = self._state.get_mode()
        hour = self._now().hour
        if mode in ("working", "lock_in"):
            return 15
        if hour >= 22 or hour < 15:
            return 30
        return 20

    def should_check_in(self) -> tuple[bool, str]:
        """Decide locally, without spending a model call."""
        data = self._state.get()
        mode = data.get("mode", "idle")
        now = self._now()

        if mode == "napping":
            return False, "napping"
        if mode == "done":
            return False, "done for the day"
        if now.hour < 12:
            return False, "before noon"

        # Don't nag twice in quick succession, whatever the mode.
        if self._last_spoke_at and (now - self._last_spoke_at).total_seconds() < 45 * 60:
            return False, "spoke recently"

        if mode in ("working", "lock_in"):
            minutes = self._state.get_work_session_duration_minutes() or 0
            if minutes < 25:
                return False, f"session only {minutes}m in"
            return True, f"active session, {minutes}m in"

        if now.hour >= 22:
            return False, "late and idle"
        if data.get("home_arrival"):
            return True, "home and idle"
        return False, f"nothing to act on (mode={mode})"

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Reminder service disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Reminder service started (adaptive interval)")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_minutes() * 60)
                if not self._running:
                    break
                ok, reason = self.should_check_in()
                if not ok:
                    logger.debug("Check-in skipped: {}", reason)
                    continue
                logger.info("Check-in: {}", reason)
                await self.on_check_in(CHECK_IN_PROMPT)
                self._last_spoke_at = self._now()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Check-in failed")
