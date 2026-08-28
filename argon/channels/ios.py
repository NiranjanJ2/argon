"""The iOS app as a delivery channel.

Argon could only start a conversation on Discord. The app is where the day
actually lives now — the brief, the board, the daily form — so an unprompted
message that lands anywhere else is asking him to check a second place.

There is no socket to write to: iOS has no inbound connection Argon can hold,
and APNs is a doorbell, not a transport. Delivery here means a durable write to
`argon.ios.inbox`, which the app collects on `GET /v1/messages` and confirms
with `POST /v1/messages/ack`. A message the app never comes for is relayed to
Discord by `MaintenanceService` — see `inbox.stale`.
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

    def __init__(self, config: Any, bus: MessageBus) -> None:
        super().__init__(config, bus)

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
        if row is None and key:
            logger.debug("iOS: {} was already queued", key)
