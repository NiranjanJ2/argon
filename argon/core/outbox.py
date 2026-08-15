"""Everything Argon promised to deliver, and whether it actually did.

"I'll remind you at 5" was, until now, a timer plus a queue write. `publish_outbound`
put an object on an in-memory queue and returned; the dispatcher tried to send it
later and swallowed the final failure; cron marked the job `ok` because its
callback had returned without raising. Three separate places therefore reported
success for a message Niranjan never received — and a one-shot whose time passed
while the service was down was *deleted at startup*, so the promise vanished with
no trace at all.

A secretary who says "sent" has to mean it. This module is the record that makes
that sentence true or false:

* one row per promise, keyed by an idempotency key, so a retry after a crash
  cannot deliver twice and a duplicate call is a no-op;
* a real acknowledgement from the channel, not a queue write, decides `sent`;
* a promise that could not be kept ends as `failed` or `missed` and stays
  visible, because a reminder that quietly evaporates is the worst outcome of
  the three.

Lateness has a policy rather than an accident. Within
:data:`RESTART_GRACE_SECONDS` of when it was due, a recovered reminder is still
worth having and is delivered with the delay stated plainly. Past that it is
noise pretending to be a reminder, so it is marked `missed` and reported.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from loguru import logger

from argon import clock
from argon.core.bus import OutboundMessage
from argon.core.store import connect, txn

#: How late a recovered reminder may be and still be worth delivering. A home
#: server restart is minutes; "your 5 PM reminder" arriving at 9 PM is not a
#: reminder, it is a confusing message about the past.
RESTART_GRACE_SECONDS = 2 * 60 * 60

#: How long to wait for a channel to acknowledge before giving up on this
#: attempt. The row stays `pending`, so the sweep or the next startup retries.
ACK_TIMEOUT_SECONDS = 45.0

#: Gap between retries of a pending promise while the process stays up.
RETRY_INTERVAL_SECONDS = 60.0

#: Grace before the first recovery sweep, so the channels have finished
#: connecting. Delivering into a Discord client that is still starting would
#: mark a perfectly recoverable reminder failed.
STARTUP_DELAY_SECONDS = 20.0

#: Metadata key the channel dispatcher looks for to know an ack is expected.
ACK_KEY = "_outbox_key"


@dataclass(frozen=True)
class Delivery:
    """The outcome of trying to keep one promise."""

    key: str
    ok: bool
    state: str            # sent | pending | failed | missed
    error: str | None = None
    duplicate: bool = False   # already delivered earlier; nothing was sent now
    #: What he actually received. Differs from what the caller passed when this
    #: key had already been delivered — the check-in ledger records this rather
    #: than the text it just generated, because the ledger exists so the model
    #: can see what it really said.
    content: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _now() -> float:
    return time.time()


class Outbox:
    """Durable delivery with acknowledgement. One per runtime."""

    def __init__(
        self,
        publish: Callable[[OutboundMessage], Awaitable[None]],
        *,
        ack_timeout: float = ACK_TIMEOUT_SECONDS,
        grace_seconds: float = RESTART_GRACE_SECONDS,
    ) -> None:
        self._publish = publish
        self._ack_timeout = ack_timeout
        self._grace = grace_seconds
        self._waiters: dict[str, asyncio.Future[tuple[bool, str | None]]] = {}
        self._sweeper: asyncio.Task | None = None
        self._running = False

    # -- the promise -------------------------------------------------------

    async def deliver(
        self,
        *,
        key: str,
        channel: str,
        chat_id: str,
        content: str,
        due_at: float | None = None,
        kind: str = "message",
        actions: list[dict[str, Any]] | None = None,
    ) -> Delivery:
        """Record the promise, send it, and wait to be told it arrived.

        Idempotent on *key*: a promise already delivered is never delivered a
        second time, whatever the caller does.
        """
        due = due_at if due_at is not None else _now()
        if not channel or not chat_id or channel == "cli":
            # No reachable destination. This used to be logged and then recorded
            # as "said" anyway, which is how two days of check-ins were reported
            # as delivered while nothing left the machine.
            self._write(key, channel, chat_id, content, due, kind, state="failed",
                        error="no reachable channel")
            logger.warning("Outbox {}: no reachable channel — not delivered", key)
            return Delivery(key, False, "failed", "no reachable channel")

        # Already going out on another task. Joining that attempt rather than
        # starting a second one is the difference between one reminder and two:
        # the row alone cannot dedupe this, because an in-flight promise is
        # legitimately `pending` and a retry of a `pending` row is exactly what
        # recovery does.
        if (inflight := self._waiters.get(key)) is not None:
            try:
                ok, error = await asyncio.wait_for(
                    asyncio.shield(inflight), timeout=self._ack_timeout
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                return Delivery(key, False, "pending", "no acknowledgement", duplicate=True)
            return Delivery(
                key, ok, "sent" if ok else "failed", error, duplicate=True
            )

        claim = self._claim(key, channel, chat_id, content, due, kind)
        if claim.state == "sent":
            return claim

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[tuple[bool, str | None]] = loop.create_future()
        self._waiters[key] = waiter
        try:
            metadata: dict[str, Any] = {ACK_KEY: key}
            if actions:
                # Buttons travel with the promise so a recovered or retried
                # delivery still arrives clickable.
                metadata["_actions"] = actions
            await self._publish(OutboundMessage(
                channel=channel, chat_id=chat_id, content=content,
                metadata=metadata,
            ))
            ok, error = await asyncio.wait_for(waiter, timeout=self._ack_timeout)
        except asyncio.TimeoutError:
            logger.warning("Outbox {}: no acknowledgement in {}s", key, self._ack_timeout)
            self._touch(key, state="pending", error="no acknowledgement")
            return Delivery(key, False, "pending", "no acknowledgement")
        except Exception as exc:  # noqa: BLE001 — publishing itself failed
            self._touch(key, state="pending", error=str(exc))
            return Delivery(key, False, "pending", str(exc))
        finally:
            self._waiters.pop(key, None)

        if ok:
            self._touch(key, state="sent")
            return Delivery(key, True, "sent", content=content)
        self._touch(key, state="failed", error=error)
        logger.error("Outbox {}: channel refused delivery: {}", key, error)
        return Delivery(key, False, "failed", error, content=content)

    def ack(self, key: str, ok: bool, error: str | None = None) -> None:
        """Called by the channel dispatcher once a send has really resolved."""
        waiter = self._waiters.get(key)
        if waiter is not None and not waiter.done():
            waiter.set_result((ok, error))
            return
        # Nobody is waiting (the caller timed out, or this is a sweep retry):
        # the row is still the record, so settle it here.
        self._touch(key, state="sent" if ok else "failed", error=None if ok else error)

    def already_sent(self, key: str) -> bool:
        row = connect().execute("SELECT state FROM outbox WHERE key = ?", (key,)).fetchone()
        return bool(row) and row["state"] == "sent"

    # -- rows --------------------------------------------------------------

    def _claim(
        self, key: str, channel: str, chat_id: str, content: str, due: float, kind: str
    ) -> Delivery:
        """Reserve this promise for an attempt, or report it already kept."""
        now = _now()
        with txn() as conn:
            row = conn.execute(
                "SELECT state, content FROM outbox WHERE key = ?", (key,)
            ).fetchone()
            if row is not None and row["state"] == "sent":
                return Delivery(
                    key, True, "sent", duplicate=True, content=row["content"]
                )
            if row is None:
                conn.execute(
                    "INSERT INTO outbox (key, channel, chat_id, content, kind, due_at, "
                    "state, attempts, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?)",
                    (key, channel, chat_id, content, kind, due, now, now),
                )
            else:
                conn.execute(
                    "UPDATE outbox SET state = 'pending', attempts = attempts + 1, "
                    "channel = ?, chat_id = ?, content = ?, updated_at = ? WHERE key = ?",
                    (channel, chat_id, content, now, key),
                )
        return Delivery(key, False, "pending")

    def _write(
        self, key: str, channel: str, chat_id: str, content: str, due: float,
        kind: str, *, state: str, error: str | None = None,
    ) -> None:
        now = _now()
        with txn() as conn:
            conn.execute(
                "INSERT INTO outbox (key, channel, chat_id, content, kind, due_at, state, "
                "attempts, last_error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
                "last_error = excluded.last_error, attempts = outbox.attempts + 1, "
                "updated_at = excluded.updated_at",
                (key, channel, chat_id, content, kind, due, state, error, now, now),
            )

    def _touch(self, key: str, *, state: str, error: str | None = None) -> None:
        now = _now()
        with txn() as conn:
            conn.execute(
                "UPDATE outbox SET state = ?, last_error = ?, updated_at = ?, "
                "sent_at = CASE WHEN ? = 'sent' THEN ? ELSE sent_at END WHERE key = ?",
                (state, error, now, state, now, key),
            )

    # -- recovery ----------------------------------------------------------

    def due_pending(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Promises still owed, recent enough to be worth keeping.

        Anything older than the grace window is marked ``missed`` on the way
        past — visibly not delivered, rather than silently gone or absurdly late.
        """
        moment = now if now is not None else _now()
        # A row touched moments ago may still be in flight on another task: the
        # caller's own attempt gave up waiting at `ack_timeout` but left the
        # message queued. Re-publishing inside that window is how one reminder
        # became two when the dispatcher was slow.
        settled_before = moment - (self._ack_timeout + RETRY_INTERVAL_SECONDS)
        rows = connect().execute(
            "SELECT * FROM outbox WHERE state = 'pending' AND due_at <= ? "
            "AND updated_at <= ? ORDER BY due_at",
            (moment, settled_before),
        ).fetchall()
        live: list[dict[str, Any]] = []
        for row in rows:
            if moment - row["due_at"] > self._grace:
                self._touch(row["key"], state="missed", error="not delivered within grace period")
                logger.warning(
                    "Outbox {}: missed — due {} and never delivered",
                    row["key"], datetime.fromtimestamp(row["due_at"], clock.tz()),
                )
                continue
            live.append(dict(row))
        return live

    def unsent(self) -> list[dict[str, Any]]:
        """Everything that ended up not delivered. This is what 'visible' means."""
        rows = connect().execute(
            "SELECT * FROM outbox WHERE state IN ('failed', 'missed') ORDER BY due_at DESC LIMIT 50"
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def lateness_note(due_at: float, *, now: float | None = None) -> str:
        """Say the delay out loud instead of pretending the message is timely."""
        moment = now if now is not None else _now()
        minutes = int((moment - due_at) / 60)
        if minutes < 2:
            return ""
        when = datetime.fromtimestamp(due_at, clock.tz())
        return f"\n\n(Late — this was due at {when:%-I:%M %p}; Argon was not reachable then.)"

    async def redeliver_due(self, *, now: float | None = None) -> list[Delivery]:
        """Keep the promises that are still worth keeping. Safe to call twice."""
        results: list[Delivery] = []
        for row in self.due_pending(now=now):
            note = self.lateness_note(row["due_at"], now=now)
            results.append(await self.deliver(
                key=row["key"], channel=row["channel"], chat_id=row["chat_id"],
                content=row["content"] + note, due_at=row["due_at"], kind=row["kind"],
            ))
        return results

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Begin recovering what was owed, then keep retrying while we live.

        Recovery is scheduled rather than awaited: the channels come up on the
        same event loop, and delivering into a Discord client that has not
        finished connecting would mark a perfectly recoverable reminder failed.
        """
        self._running = True
        self._sweeper = asyncio.create_task(self._sweep_loop())

    def stop(self) -> None:
        self._running = False
        if self._sweeper:
            self._sweeper.cancel()
            self._sweeper = None

    async def _sweep_loop(self) -> None:
        first = True
        while self._running:
            try:
                await asyncio.sleep(STARTUP_DELAY_SECONDS if first else RETRY_INTERVAL_SECONDS)
                if not self._running:
                    break
                recovered = await self.redeliver_due()
                if first and recovered:
                    logger.info(
                        "Outbox: recovered {} message(s) owed from before the restart",
                        len(recovered),
                    )
                first = False
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                logger.exception("Outbox sweep failed")


__all__ = [
    "Outbox", "Delivery", "ACK_KEY", "RESTART_GRACE_SECONDS",
    "ACK_TIMEOUT_SECONDS", "RETRY_INTERVAL_SECONDS",
]
