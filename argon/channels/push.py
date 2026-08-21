"""The phone, as a place Argon can start a conversation.

Discord was the only channel that could receive an unprompted message, so the
app could answer but never be spoken to first. That is backwards for the thing
he actually carries.

A push is a notification, not a transcript — it is gone once dismissed — so
every message sent here is also written into the app's own conversation, which
is the surface that survives. The notification is how he finds out; the chat is
where it stays.

Delivery is only successful when Apple accepts the push. A dropped
notification that reported success would let the check-in ledger record
"spoke" for a message he never saw, which is the failure the outbox exists to
prevent.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from argon.channels.base import BaseChannel, ChannelSendError
from argon.core.bus import MessageBus, OutboundMessage

#: The session the app reads its conversation from.
IOS_SESSION = "ios"


class PushChannel(BaseChannel):
    """Deliver to the iPhone over APNs, and keep a copy in the app's chat."""

    display_name = "iPhone"

    def __init__(self, config: Any, bus: MessageBus, workspace: Any = None) -> None:
        super().__init__(config, bus)
        self._workspace = workspace

    @property
    def is_running(self) -> bool:
        # Nothing to hold open: APNs is a request per message. The channel is
        # available exactly when a device has registered for notifications.
        return self.has_device

    @property
    def has_device(self) -> bool:
        from argon.ios.push import device_token

        token, _ = device_token()
        return bool(token)

    async def login(self, force: bool = False) -> bool:
        return self.has_device

    async def start(self) -> None:
        if not self.has_device:
            logger.info("Push channel: no device registered yet; it will work once the app runs")

    async def stop(self) -> None:
        return None

    def is_allowed(self, sender_id: str) -> bool:
        # There is one device and it is his. Inbound arrives over the
        # authenticated API, which does its own checking.
        return True

    async def send(self, msg: OutboundMessage) -> None:
        """Push it, and keep a copy where it can be read again."""
        from argon.config import Config
        from argon.ios.push import APNsClient

        text = (msg.content or "").strip()
        if not text:
            return

        # Written first. A notification he taps away is gone; if the copy were
        # only made on success, a delivery that failed at Apple's end would
        # leave nothing at all — not even a record he could scroll back to.
        self._record(text)

        # Counted before sending so the badge and the notification agree, and
        # so a push that fails still leaves the app showing there is something
        # waiting rather than nothing at all.
        from argon.ios import unread

        badge = unread.bump()

        config = getattr(self, "argon_config", None) or Config()
        try:
            result = await APNsClient(config).send(
                "Argon", _preview(text), data={"kind": "message"}, badge=badge
            )
        except Exception as exc:  # noqa: BLE001 - a bad push is a send failure
            raise ChannelSendError(f"push failed: {exc}") from exc

        if not result.ok:
            raise ChannelSendError(f"push refused: {result.reason or result.status}")

    def _record(self, text: str) -> None:
        """Append to the app's conversation so it survives the notification."""
        try:
            from argon.core.session import SessionManager

            workspace = self._workspace
            if workspace is None:
                from argon.paths import argon_home

                workspace = argon_home()
            sessions = SessionManager(workspace)
            session = sessions.get_or_create(IOS_SESSION)
            session.add_message("assistant", text)
            sessions.save(session)
        except Exception as exc:  # noqa: BLE001 - never fail a send over history
            logger.warning("Could not record a pushed message in the app's chat: {}", exc)


#: A lock screen shows roughly this much. Longer text is not truncated in the
#: chat — only in the notification, which is a pointer to it.
PREVIEW_CHARS = 220


def _preview(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= PREVIEW_CHARS:
        return collapsed
    return collapsed[: PREVIEW_CHARS - 1].rstrip() + "…"
