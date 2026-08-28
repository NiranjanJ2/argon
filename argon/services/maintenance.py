"""Upkeep that must not sit behind the check-in switch.

Consolidation used to ride the check-in loop: `ReminderService` was handed an
`on_day_rollover` callback and called it once per tick. But `ReminderService.
start()` returns early when check-ins are disabled, so the one switch a person
reaches for when Argon is talking too much — *stop starting conversations* —
also stopped yesterday's journal from ever reaching MEMORY.md and stopped old
day pages from being swept. Quiet became amnesiac, silently, with nothing in
the logs to say so.

Remembering is not part of the unsolicited-message policy, so it does not live
behind its switch. This lifecycle runs whenever the gateway runs.

The app mailbox relay is here for the same reason and no other. Argon delivers
unprompted messages to the iOS app now, but a mailbox cannot push: if he has not
opened the app, a brief sitting in it is the same as no brief. Anything the app
has not collected after `RELAY_AFTER_MINUTES` goes to Discord instead, once.

Idempotence is the journal's, not this module's: `pending_day()` names the
oldest finished day nobody has folded in yet, and `consolidate_day` marks it
done. Ticking again finds nothing pending and costs a directory listing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from argon.core.journal import Journal

#: How often the gate is evaluated. Cheap when nothing is pending — a glob and
#: a string compare, no model call. Matches the cadence consolidation had while
#: it rode the check-in loop, so a backlog drains at the same rate.
TICK_MINUTES = 10

#: How long the app gets to collect its own mail before Discord is used. Long
#: enough that opening the app after dinner keeps the evening in one place;
#: short enough that a 4 PM brief is still about tonight when it lands.
RELAY_AFTER_MINUTES = 90


class MaintenanceService:
    """Folds finished days into long-term memory and sweeps the old pages.

    ``on_consolidate`` is the only part that needs a model, so it is injected:
    the service owns *which* day and *whether* one is due, the caller owns the
    provider. It is handed this service's own ``Journal`` so there is exactly
    one view of the files on disk.
    """

    def __init__(
        self,
        workspace: Path,
        on_consolidate: Callable[[Journal, str], Awaitable[Any]],
        *,
        interval_minutes: int = TICK_MINUTES,
        on_relay: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self.workspace = workspace
        self.on_consolidate = on_consolidate
        #: Delivers a message the app never collected. Injected for the same
        #: reason as `on_consolidate`: this service owns *which* messages are
        #: overdue, the caller owns how to send one.
        self.on_relay = on_relay
        self.interval_minutes = interval_minutes
        self.journal = Journal(workspace)
        self._running = False
        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            logger.warning("Memory maintenance already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Memory maintenance started (every {}m)", self.interval_minutes)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        # Ticks first, then waits: a gateway that was down overnight has a day
        # waiting, and it should not sit unconsolidated for another interval.
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — one bad tick must not end upkeep
                logger.exception("Memory maintenance tick failed")
            try:
                await asyncio.sleep(self.interval_minutes * 60)
            except asyncio.CancelledError:
                break

    # -- work --------------------------------------------------------------

    async def _relay_uncollected_mail(self) -> int:
        """Send on anything the app has not come for. Never raises.

        Marked relayed *after* the send is acknowledged, and only then, so a
        failed relay is retried next tick instead of being recorded as sent.
        That is the whole distinction the outbox exists to keep.
        """
        if self.on_relay is None:
            return 0
        from argon.ios import inbox

        try:
            overdue = inbox.stale(RELAY_AFTER_MINUTES)
        except Exception:  # noqa: BLE001 - a store fault must not end upkeep
            logger.exception("Could not read the app mailbox")
            return 0

        relayed = 0
        for row in overdue:
            try:
                result = await self.on_relay(row["text"], key=f"relay:{row['id']}")
            except Exception:  # noqa: BLE001 - next tick tries again
                logger.exception("Relay of app message {} failed", row["id"])
                continue
            if result is False or getattr(result, "ok", True) is False:
                logger.warning("Relay of app message {} was not delivered", row["id"])
                continue
            inbox.mark_relayed([row["id"]])
            relayed += 1
        if relayed:
            logger.info("Relayed {} message(s) the app did not collect", relayed)
        return relayed

    async def tick(self) -> str | None:
        """Fold at most one finished day in, then sweep. Returns the day, if any.

        One day per tick, oldest first, which is what keeps a two-day outage
        from stepping over the older day entirely. A failed fold leaves the day
        unmarked so the next tick retries it, and says so in the log rather
        than losing the day quietly.
        """
        await self._relay_uncollected_mail()

        day = self.journal.pending_day()
        if day is not None:
            try:
                await self.on_consolidate(self.journal, day)
            except Exception:  # noqa: BLE001 — retried next tick, never silent
                logger.exception("End-of-day consolidation failed for {}", day)
                day = None

        # Independent of the fold above: a day that failed to consolidate must
        # not also stop the sweep, or the pages pile up unbounded.
        removed = self.journal.sweep_old_days()
        if removed:
            logger.info("Swept {} journal day page(s) past the window", removed)
        return day
