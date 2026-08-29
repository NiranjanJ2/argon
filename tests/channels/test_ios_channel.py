"""The app channel: a durable write, then a doorbell that cannot break it."""

from __future__ import annotations

import pytest

from argon.channels.ios import IOSChannel
from argon.core.bus import MessageBus, OutboundMessage
from argon.ios import inbox


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    from argon.core import store

    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _msg(text: str, key: str | None = None) -> OutboundMessage:
    return OutboundMessage(
        channel="ios", chat_id="app", content=text,
        metadata={"key": key} if key else {},
    )


class _Push:
    """Stands in for APNsClient."""

    def __init__(self, ok: bool = True, boom: bool = False) -> None:
        self.configured, self.sent, self._ok, self._boom = True, [], ok, boom

    async def send(self, title, body, **kw):
        if self._boom:
            raise RuntimeError("APNs is down")
        self.sent.append((title, body, kw))
        return type("R", (), {"ok": self._ok, "reason": None if self._ok else "BadDeviceToken"})()


def _channel(monkeypatch, push: _Push | None) -> IOSChannel:
    channel = IOSChannel({}, MessageBus(), config_root=object() if push else None)
    if push is not None:
        monkeypatch.setattr("argon.ios.push.APNsClient", lambda _cfg: push)
    return channel


@pytest.mark.asyncio
async def test_the_message_is_queued_and_the_doorbell_rings(monkeypatch):
    push = _Push()
    await _channel(monkeypatch, push).send(_msg("Here is tonight."))

    assert len(inbox.pending()) == 1
    # The push carries the text, so the notification is the message.
    assert push.sent[0][0] == "Argon"
    assert push.sent[0][1] == "Here is tonight."
    assert push.sent[0][2]["collapse_id"] == inbox.pending()[0]["id"]


@pytest.mark.asyncio
async def test_a_dead_doorbell_does_not_fail_the_send(monkeypatch):
    """The mailbox already has it and the stale-relay covers the rest. Raising
    would tell the outbox the delivery failed and earn a retry that queues it
    a second time."""
    await _channel(monkeypatch, _Push(boom=True)).send(_msg("Here is tonight."))

    assert len(inbox.pending()) == 1


@pytest.mark.asyncio
async def test_a_refused_push_is_logged_not_raised(monkeypatch):
    await _channel(monkeypatch, _Push(ok=False)).send(_msg("Here is tonight."))

    assert len(inbox.pending()) == 1


@pytest.mark.asyncio
async def test_a_duplicate_key_neither_queues_nor_rings(monkeypatch):
    """A redelivery after a crash must not buzz him twice for one brief."""
    push = _Push()
    channel = _channel(monkeypatch, push)
    await channel.send(_msg("Here is tonight.", key="brief:1"))
    await channel.send(_msg("Here is tonight.", key="brief:1"))

    assert len(inbox.pending()) == 1
    assert len(push.sent) == 1
