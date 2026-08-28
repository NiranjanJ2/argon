"""The app mailbox, and the rule that a queue write is not a delivery."""

from __future__ import annotations

from datetime import timedelta

import pytest

from argon import clock
from argon.ios import inbox


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    from argon.core import store

    store.reset_for_tests()
    yield
    store.reset_for_tests()


def test_a_queued_message_is_pending_until_the_app_acks():
    row = inbox.put("Here is tonight.")

    assert [m["id"] for m in inbox.pending()] == [row["id"]]

    assert inbox.mark_fetched([row["id"]]) == 1
    assert inbox.pending() == []


def test_the_same_key_is_never_queued_twice():
    """A redelivery after a crash must not show him the brief twice."""
    first = inbox.put("Here is tonight.", key="checkin:2026-08-27:daily_brief")
    again = inbox.put("Here is tonight.", key="checkin:2026-08-27:daily_brief")

    assert first is not None
    assert again is None
    assert len(inbox.pending()) == 1


def test_an_empty_message_is_not_a_message():
    assert inbox.put("   ") is None
    assert inbox.pending() == []


def test_mail_the_app_never_collected_goes_stale(monkeypatch):
    row = inbox.put("Here is tonight.")

    assert inbox.stale(90) == []

    later = clock.now() + timedelta(minutes=91)
    assert [m["id"] for m in inbox.stale(90, now=later)] == [row["id"]]


def test_a_collected_message_never_goes_stale():
    row = inbox.put("Here is tonight.")
    inbox.mark_fetched([row["id"]])

    later = clock.now() + timedelta(hours=6)
    assert inbox.stale(90, now=later) == []


def test_a_relayed_message_is_not_relayed_again():
    row = inbox.put("Here is tonight.")
    later = clock.now() + timedelta(minutes=91)
    assert len(inbox.stale(90, now=later)) == 1

    assert inbox.mark_relayed([row["id"]]) == 1
    assert inbox.stale(90, now=later) == []
    # Still pending: Discord got it, the app has not.
    assert len(inbox.pending()) == 1


def test_reading_the_thread_collects_the_mail():
    """The app calls POST /v1/ios/read; that is the acknowledgement.

    A separate ack endpoint would be a second way to say the same thing, and
    the app was already written against this one.
    """
    row = inbox.put("Here is tonight.")

    assert inbox.mark_fetched([r["id"] for r in inbox.pending()]) == 1
    assert inbox.pending() == []
    assert row["text"] in [m["text"] for m in inbox.recent()]


def test_pending_mail_survives_the_trim():
    """Trimming keeps the mailbox bounded; an unfetched message is a promise."""
    promised = inbox.put("Do not lose me.")
    for i in range(inbox.KEEP_RECENT + 10):
        settled = inbox.put(f"old {i}")
        inbox.mark_fetched([settled["id"]])

    # The trim runs on write, so it only sees what was settled by the last
    # `put`. One more write squares it up.
    inbox.put("and now trim")

    pending = inbox.pending()
    assert promised["id"] in [m["id"] for m in pending]
    settled = [m for m in inbox.recent(999) if m["fetched_at"] or m["relayed_at"]]
    assert len(settled) == inbox.KEEP_RECENT
