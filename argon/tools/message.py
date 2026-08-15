"""Message tool — delivers a message (and files) to the current conversation."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from argon.core import turn
from argon.core.bus import OutboundMessage
from argon.tools.base import Tool


class MessageTool(Tool):
    """Send to whoever Argon is currently talking to.

    Upstream let the model pass ``channel``/``chat_id``, which it promptly got
    wrong — it once passed the literal string "Niranjan" as a Discord chat id and
    crashed the send with ``int('Niranjan')``. Argon has exactly one user, so the
    destination is never the model's decision: it is the conversation in flight.

    "The conversation in flight" was itself stored here, on a tool shared by
    every session, and rewritten before each batch of tool calls. Two concurrent
    turns therefore fought over one destination and one "already replied" flag.
    Both now come from :mod:`argon.core.turn`, which asyncio copies per task, so
    a Discord turn and an iOS turn cannot see each other's.
    """

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._default_message_id = default_message_id

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        self._send_callback = callback

    def _destination(self) -> tuple[str, str, str | None]:
        if (ctx := turn.current()) is not None:
            return ctx.channel, ctx.chat_id, ctx.message_id
        return self._default_channel, self._default_chat_id, self._default_message_id

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Send a message to Niranjan, optionally with file attachments. "
            "This is the ONLY way to deliver files (images, documents, audio, video). "
            "read_file only shows a file to you — it does not send it."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The message to send"},
                "media": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: file paths to attach",
                },
            },
            "required": ["content"],
        }

    async def execute(
        self,
        content: str,
        media: list[str] | None = None,
        **kwargs: Any,
    ) -> str:
        from argon.utils.helpers import strip_think

        channel, chat_id, message_id = self._destination()
        if not channel or not chat_id:
            return "Error: no active conversation to send to"
        if not self._send_callback:
            return "Error: message sending not configured"

        body = strip_think(content)
        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=body,
            media=media or [],
            metadata={"message_id": message_id} if message_id else {},
        )
        try:
            await self._send_callback(msg)
        except Exception as e:
            return f"Error sending message: {e}"
        # Recorded on the turn, not the tool. The check-in ledger needs the real
        # text: storing the placeholder "(sent)" once meant the model was shown
        # "(sent)" as its own history, could not tell it had already asked, and
        # asked what Niranjan's plan was nine times in one day.
        if (ctx := turn.current()) is not None:
            ctx.said.append(body)
        return f"Sent{f' with {len(media)} attachment(s)' if media else ''}."
