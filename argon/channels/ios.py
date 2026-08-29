"""The iOS app as a delivery channel.

Argon could only start a conversation on Discord. The app is where the day
actually lives now — the brief, the board, the daily form — so an unprompted
message that lands anywhere else is asking him to check a second place.

There is no socket to write to: iOS has no inbound connection Argon can hold,
and APNs is a doorbell, not a transport. Delivery here means a durable write to
`argon.ios.inbox`, which the app collects on `GET /v1/messages` and confirms
with `POST /v1/ios/read`. A message the app never comes for is relayed to
Discord by `MaintenanceService` — see `inbox.stale`.

The doorbell matters more than it sounds. The app polls on a foreground timer,
so a mailbox with nothing ringing it is only read when he happens to open the
app: on 2026-08-28 the brief sat uncollected for ninety minutes and went to
Discord — the fallback working correctly, and the app not being primary at all.
The push carries the text, so the notification *is* the message and opening the
app is optional.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from argon.channels.base import BaseChannel, ChannelSendError
from argon.core.bus import MessageBus, OutboundMessage


class IOSChannel(BaseChannel):
    """Writes to the app's mailbox. No connection to start or stop."""

    name = "ios"
    display_name = "iOS app"

    def __init__(self, config: Any, bus: MessageBus, *, config_root: Any = None) -> None:
        super().__init__(config, bus)
        #: APNsClient wants the whole Config, not this channel's section.
        self.config_root = config_root

    async def start(self) -> None:
        # Inbound arrives over the HTTP API, which the gateway already runs.
        self._running = True
        logger.info("iOS channel ready (mailbox at /v1/messages)")

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        from argon.ios import inbox

        # The idempotency key the caller already computed for the outbox. Two
        # deliveries of one brief is the failure this prevents.
        key = str(msg.metadata.get("key") or "") or None
        try:
            row = inbox.put(msg.content, key=key)
        except Exception as exc:  # noqa: BLE001 - a store fault is a send failure
            raise ChannelSendError(f"could not queue for the app: {exc}") from exc
        if row is None:
            if key:
                logger.debug("iOS: {} was already queued", key)
            return
        await self._ring(msg.content, row)

    async def _ring(self, text: str, row: dict[str, Any]) -> None:
        """Notify the phone that something is waiting. Never fails the send.

        The message is already durably queued by the time this runs, and the
        stale-relay covers a push that never lands — so a doorbell nobody heard
        is a worse notification, not a lost message. Raising here would tell the
        outbox the delivery failed and earn a retry that queues it twice.
        """
        if self.config_root is None:
            return
        from argon.ios.push import APNsClient

        try:
            client = APNsClient(self.config_root)
            if not client.configured:
                return
            result = await client.send(
                "Argon",
                text,
                # The app opens straight to the thread and acks what it finds.
                data={"kind": "message", "message_id": row["id"]},
                # One notification per message, so a retry replaces rather than
                # stacks a second copy on his lock screen.
                collapse_id=row["id"],
            )
            if not result.ok:
                logger.warning("iOS doorbell not delivered: {}", result.reason)
        except Exception as exc:  # noqa: BLE001 — the mailbox already has it
            logger.warning("iOS doorbell failed: {}", exc)
