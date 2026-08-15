"""A channel that cannot deliver has to say so.

Every `send` implementation used to log its failures and return normally — a
Discord client still connecting, a non-numeric chat id, a bridge returning 500.
Once cron and the check-in ledger were wired to "did the send resolve", that
made `sent` mean "we called a function that returned". The reminder Argon
recorded as delivered at 5 PM had been dropped at the gateway with one warning
line, and the one-shot job was then deleted as completed.
"""

from __future__ import annotations

import asyncio

import pytest

from argon.channels.base import ChannelSendError
from argon.channels.manager import ChannelManager
from argon.core.bus import MessageBus, OutboundMessage
from argon.core.outbox import Outbox


class _Channel:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage) -> None:
        if self.error:
            raise self.error
        self.sent.append(msg)

    async def send_delta(self, *_a, **_kw) -> None:
        pass


def _manager(channel: _Channel, outbox: Outbox | None = None) -> ChannelManager:
    from argon.config import Config

    manager = ChannelManager.__new__(ChannelManager)   # skip real channel discovery
    manager.config = Config(google={"enabled": False})
    manager.bus = MessageBus()
    manager.outbox = outbox
    manager.channels = {"discord": channel}
    manager._dispatch_task = None
    return manager


async def test_a_channel_that_raises_is_reported_as_not_sent():
    manager = _manager(_Channel(ChannelSendError("Discord client is not connected")))

    ok, error = await manager._send_with_retry(
        manager.channels["discord"], OutboundMessage("discord", "1", "hi")
    )

    assert ok is False
    assert "not connected" in error


async def test_a_successful_send_is_reported_as_sent():
    manager = _manager(_Channel())

    ok, error = await manager._send_with_retry(
        manager.channels["discord"], OutboundMessage("discord", "1", "hi")
    )

    assert (ok, error) == (True, None)


async def test_a_dropped_reminder_is_never_recorded_as_delivered():
    """The end-to-end version: outbox -> dispatcher -> dead channel -> outbox."""
    channel = _Channel(ChannelSendError("Discord client is not connected"))
    bus = MessageBus()
    # Long enough for the dispatcher's three attempts (1s + 2s backoff) to
    # finish, so this asserts the terminal outcome rather than the timeout.
    outbox = Outbox(bus.publish_outbound, ack_timeout=20.0)
    manager = _manager(channel, outbox)
    manager.bus = bus
    outbox._publish = bus.publish_outbound

    dispatcher = asyncio.create_task(manager._dispatch_outbound())
    try:
        result = await outbox.deliver(
            key="cron:job:1", channel="discord", chat_id="1",
            content="Sign the syllabus agreement",
        )
    finally:
        dispatcher.cancel()

    assert result.ok is False, "the reminder did not arrive, so it is not sent"
    assert result.state == "failed"
    assert channel.sent == []
    assert [r["key"] for r in outbox.unsent()] == ["cron:job:1"]


async def test_an_unknown_channel_is_a_delivery_failure_not_a_shrug():
    bus = MessageBus()
    outbox = Outbox(bus.publish_outbound, ack_timeout=2.0)
    manager = _manager(_Channel(), outbox)
    manager.bus = bus
    manager.channels = {}          # nothing is configured for "discord"
    outbox._publish = bus.publish_outbound

    dispatcher = asyncio.create_task(manager._dispatch_outbound())
    try:
        result = await outbox.deliver(
            key="cron:job:2", channel="discord", chat_id="1", content="reminder",
        )
    finally:
        dispatcher.cancel()

    assert result.ok is False
    assert "unknown channel" in (result.error or "")


class TestTheChannelsThemselves:
    async def test_discord_raises_when_the_client_is_not_connected(self):
        from argon.channels.discord import DiscordChannel

        channel = DiscordChannel.__new__(DiscordChannel)
        channel._client = None

        with pytest.raises(ChannelSendError, match="not connected"):
            await channel.send(OutboundMessage("discord", "1", "hi"))

    async def test_whatsapp_raises_when_the_bridge_is_down(self):
        from argon.channels.whatsapp import WhatsAppChannel

        channel = WhatsAppChannel.__new__(WhatsAppChannel)
        channel._http = None

        with pytest.raises(ChannelSendError, match="not running"):
            await channel.send(OutboundMessage("whatsapp", "1@c.us", "hi"))

    async def test_whatsapp_raises_on_a_bridge_error_status(self):
        from argon.channels.whatsapp import WhatsAppChannel

        class _Resp:
            status_code = 500
            text = "bridge exploded"

        class _Http:
            async def post(self, *_a, **_kw):
                return _Resp()

        channel = WhatsAppChannel.__new__(WhatsAppChannel)
        channel._http = _Http()
        channel.config = type("C", (), {"bridge_port": 3996})()

        with pytest.raises(ChannelSendError, match="500"):
            await channel.send(OutboundMessage("whatsapp", "1@c.us", "hi"))
