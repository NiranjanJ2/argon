"""What "I sent it" has to mean.

`publish_outbound` put a message on an in-memory queue and returned. The
dispatcher tried to send it later and swallowed the final failure. Cron marked
the job `ok` because its callback had returned without raising, and the check-in
ledger recorded "said" because it had reached the end of its function. Three
places reported success for messages Niranjan never received, and a one-shot
whose time passed during downtime was deleted at startup — the promise gone with
no trace.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from argon.core import store
from argon.core.outbox import ACK_KEY, Outbox


class FakeDispatcher:
    """Stands in for ChannelManager: the only thing that knows a send resolved."""

    def __init__(self, *, fails: bool = False, silent: bool = False) -> None:
        self.sent: list = []
        self.fails = fails
        self.silent = silent      # never acknowledges — a wedged channel
        self.outbox: Outbox | None = None

    async def publish(self, msg) -> None:
        self.sent.append(msg)
        key = (msg.metadata or {}).get(ACK_KEY)
        if not key or self.silent or self.outbox is None:
            return
        # A real channel goes out to the network before it can acknowledge, so
        # the ack lands on a later turn of the event loop. Acknowledging inline
        # would hide every interleaving bug this file is meant to catch.
        await asyncio.sleep(0)
        self.outbox.ack(key, not self.fails, "discord 500" if self.fails else None)


def _outbox(dispatcher: FakeDispatcher, **kw) -> Outbox:
    outbox = Outbox(dispatcher.publish, **kw)
    dispatcher.outbox = outbox
    return outbox


def _row(key: str) -> dict:
    row = store.connect().execute("SELECT * FROM outbox WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else {}


async def test_a_delivery_is_only_sent_once_however_often_it_is_retried():
    """Exactly-once. A retry after a crash must not text him twice."""
    dispatcher = FakeDispatcher()
    outbox = _outbox(dispatcher)

    first = await outbox.deliver(
        key="cron:job:5000", channel="discord", chat_id="1", content="Sign the syllabus",
    )
    second = await outbox.deliver(
        key="cron:job:5000", channel="discord", chat_id="1", content="Sign the syllabus",
    )

    assert first.ok and first.state == "sent" and first.duplicate is False
    assert second.ok and second.duplicate is True
    assert len(dispatcher.sent) == 1, "the second call must not reach the channel"


async def test_a_terminal_channel_failure_is_reported_not_swallowed():
    dispatcher = FakeDispatcher(fails=True)
    outbox = _outbox(dispatcher)

    result = await outbox.deliver(
        key="cron:job:1", channel="discord", chat_id="1", content="start math homework",
    )

    assert result.ok is False
    assert result.state == "failed"
    assert result.error == "discord 500"
    assert [r["key"] for r in outbox.unsent()] == ["cron:job:1"]


async def test_no_reachable_channel_is_a_failure_not_a_silent_drop():
    """`notify` used to log a warning and the ledger recorded "said" anyway."""
    dispatcher = FakeDispatcher()
    outbox = _outbox(dispatcher)

    result = await outbox.deliver(
        key="checkin:2026-08-14:daily_brief", channel="cli", chat_id="direct",
        content="the brief",
    )

    assert result.ok is False
    assert dispatcher.sent == []
    assert _row("checkin:2026-08-14:daily_brief")["state"] == "failed"


async def test_a_channel_that_never_acknowledges_leaves_the_promise_owed():
    """Pending, not sent: the sweep and the next startup both retry it."""
    dispatcher = FakeDispatcher(silent=True)
    outbox = _outbox(dispatcher, ack_timeout=0.05)

    result = await outbox.deliver(
        key="cron:job:2", channel="discord", chat_id="1", content="reminder",
    )

    assert result.ok is False
    assert result.state == "pending"
    assert _row("cron:job:2")["state"] == "pending"


async def test_a_reminder_owed_from_before_a_restart_is_delivered_late_and_says_so():
    dispatcher = FakeDispatcher()
    outbox = _outbox(dispatcher)
    due = time.time() - 20 * 60

    # A promise recorded before the process died.
    with store.txn() as conn:
        conn.execute(
            "INSERT INTO outbox (key, channel, chat_id, content, kind, due_at, state, "
            "attempts, created_at, updated_at) "
            "VALUES ('cron:j:1', 'discord', '1', 'Start the pset', 'reminder', ?, "
            "'pending', 1, ?, ?)",
            (due, due, due),
        )

    results = await outbox.redeliver_due()

    assert [r.ok for r in results] == [True]
    assert len(dispatcher.sent) == 1
    body = dispatcher.sent[0].content
    assert body.startswith("Start the pset"), "his words, unaltered"
    assert "Late —" in body and "Argon was not reachable" in body
    assert _row("cron:j:1")["state"] == "sent"


async def test_a_reminder_too_old_to_be_useful_is_marked_missed_not_delivered():
    """Absurdly late is worse than nothing — but it must still be visible."""
    dispatcher = FakeDispatcher()
    outbox = _outbox(dispatcher)
    due = time.time() - 6 * 60 * 60

    with store.txn() as conn:
        conn.execute(
            "INSERT INTO outbox (key, channel, chat_id, content, kind, due_at, state, "
            "attempts, created_at, updated_at) "
            "VALUES ('cron:j:2', 'discord', '1', 'Leave for practice', 'reminder', ?, "
            "'pending', 1, ?, ?)",
            (due, due, due),
        )

    results = await outbox.redeliver_due()

    assert results == []
    assert dispatcher.sent == [], "six hours late is not a reminder"
    assert _row("cron:j:2")["state"] == "missed"
    assert [r["key"] for r in outbox.unsent()] == ["cron:j:2"]


async def test_recovery_does_not_redeliver_something_already_sent():
    dispatcher = FakeDispatcher()
    outbox = _outbox(dispatcher)
    due = time.time() - 5 * 60

    await outbox.deliver(
        key="cron:j:3", channel="discord", chat_id="1", content="done already",
        due_at=due,
    )
    await outbox.redeliver_due()

    assert len(dispatcher.sent) == 1


async def test_a_failed_attempt_can_be_retried_and_then_succeed():
    """A provider or channel outage must not permanently consume the promise."""
    dispatcher = FakeDispatcher(fails=True)
    outbox = _outbox(dispatcher)

    first = await outbox.deliver(
        key="cron:j:4", channel="discord", chat_id="1", content="reminder",
    )
    assert first.ok is False

    dispatcher.fails = False
    second = await outbox.deliver(
        key="cron:j:4", channel="discord", chat_id="1", content="reminder",
    )

    assert second.ok is True
    assert _row("cron:j:4")["state"] == "sent"
    assert _row("cron:j:4")["attempts"] == 2
    assert outbox.unsent() == []


async def test_health_reports_what_was_never_delivered():
    dispatcher = FakeDispatcher(fails=True)
    outbox = _outbox(dispatcher)
    await outbox.deliver(key="k1", channel="discord", chat_id="1", content="x")

    health = store.health()

    assert health["ok"] is True
    assert health["outbox_unsent"] == 1


@pytest.mark.parametrize("concurrency", [2, 4, 8])
async def test_concurrent_delivery_of_one_key_still_sends_once(concurrency):
    """The startup sweep and a cron tick can reach for the same promise at once.

    The row cannot dedupe this on its own: an in-flight promise is legitimately
    `pending`, and retrying a `pending` row is precisely what recovery does.
    Without an in-flight join, four callers sent four copies — and three of them
    orphaned their waiters and blocked for the full acknowledgement timeout.
    """
    dispatcher = FakeDispatcher()
    outbox = _outbox(dispatcher, ack_timeout=2.0)

    results = await asyncio.gather(*[
        outbox.deliver(key="same", channel="discord", chat_id="1", content="once")
        for _ in range(concurrency)
    ])

    assert len(dispatcher.sent) == 1
    assert all(r.ok for r in results), "every caller learns it was delivered"
    assert sum(1 for r in results if r.duplicate) == concurrency - 1
