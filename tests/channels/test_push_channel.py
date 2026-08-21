"""The phone as somewhere Argon can start a conversation."""

import pytest

from argon.channels.base import ChannelSendError
from argon.channels.push import PushChannel, _preview
from argon.core.bus import MessageBus, OutboundMessage


@pytest.fixture
def channel(tmp_path):
    return PushChannel({}, MessageBus(), workspace=tmp_path)


class TestPreview:
    def test_short_text_is_untouched(self):
        assert _preview("Have you started APUSH?") == "Have you started APUSH?"

    def test_long_text_is_trimmed_for_the_lock_screen(self):
        out = _preview("word " * 200)
        assert len(out) <= 220 and out.endswith("…")

    def test_newlines_collapse(self):
        # A lock screen shows one run of text; raw newlines waste the space.
        assert _preview("Two things:\n\n1. APUSH\n2. Calc") == "Two things: 1. APUSH 2. Calc"


class TestSending:
    async def test_a_refused_push_is_a_send_failure(self, channel, monkeypatch):
        """A push Apple refused must not be reported as delivered.

        The check-in ledger records "spoke" from the outbox's answer, so a
        silent success here is how two days of check-ins died while the log
        said they had been sent.
        """
        class Refusing:
            def __init__(self, *a, **k): pass
            async def send(self, *a, **k):
                from argon.ios.push import PushResult
                return PushResult(False, status=400, reason="BadDeviceToken")

        monkeypatch.setattr("argon.ios.push.APNsClient", Refusing)

        with pytest.raises(ChannelSendError):
            await channel.send(OutboundMessage(channel="push", chat_id="phone", content="hi"))

    async def test_the_message_is_kept_even_when_the_push_fails(self, channel, monkeypatch):
        """A notification is gone once dismissed; the chat is what survives.

        Recorded before the push, so a delivery that dies at Apple's end still
        leaves something he can scroll back to.
        """
        class Refusing:
            def __init__(self, *a, **k): pass
            async def send(self, *a, **k):
                from argon.ios.push import PushResult
                return PushResult(False, status=400, reason="BadDeviceToken")

        monkeypatch.setattr("argon.ios.push.APNsClient", Refusing)

        with pytest.raises(ChannelSendError):
            await channel.send(
                OutboundMessage(channel="push", chat_id="phone", content="Brief for today")
            )

        from argon.core.session import SessionManager
        session = SessionManager(channel._workspace).get_or_create("ios")
        assert any("Brief for today" in str(m.get("content")) for m in session.messages)

    async def test_an_empty_message_sends_nothing(self, channel, monkeypatch):
        called = []
        class Counting:
            def __init__(self, *a, **k): pass
            async def send(self, *a, **k):
                called.append(1)
                from argon.ios.push import PushResult
                return PushResult(True)

        monkeypatch.setattr("argon.ios.push.APNsClient", Counting)
        await channel.send(OutboundMessage(channel="push", chat_id="phone", content="   "))

        assert called == []


class TestAvailability:
    def test_no_device_means_not_running(self, channel, monkeypatch):
        monkeypatch.setattr("argon.ios.push.device_token", lambda: (None, "production"))
        assert channel.has_device is False
        assert channel.is_running is False

    def test_a_registered_device_makes_it_available(self, channel, monkeypatch):
        monkeypatch.setattr("argon.ios.push.device_token", lambda: ("abc123", "production"))
        assert channel.has_device is True
