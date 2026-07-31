"""Message tool — delivers a message (and files) to the current conversation."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from argon.core.bus import OutboundMessage
from argon.tools.base import Tool


class MessageTool(Tool):
    """Send to whoever Argon is currently talking to.

    Upstream let the model pass ``channel``/``chat_id``, which it promptly got
    wrong — it once passed the literal string "Niranjan" as a Discord chat id and
    crashed the send with ``int('Niranjan')``. Argon has exactly one user, so the
    destination is never the model's decision: it is the conversation in flight.
    """

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
    ):
        self._send_callback = send_callback
        self._channel = default_channel
        self._chat_id = default_chat_id
        self._message_id = default_message_id
        self._sent_in_turn: bool = False

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        self._send_callback = callback

    def start_turn(self) -> None:
        self._sent_in_turn = False

    @property
    def sent_in_turn(self) -> bool:
        """Did the model already deliver this turn? Guards double-sends."""
        return self._sent_in_turn

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

        if not self._channel or not self._chat_id:
            return "Error: no active conversation to send to"
        if not self._send_callback:
            return "Error: message sending not configured"

        msg = OutboundMessage(
            channel=self._channel,
            chat_id=self._chat_id,
            content=strip_think(content),
            media=media or [],
            metadata={"message_id": self._message_id} if self._message_id else {},
        )
        try:
            await self._send_callback(msg)
        except Exception as e:
            return f"Error sending message: {e}"
        self._sent_in_turn = True
        return f"Sent{f' with {len(media)} attachment(s)' if media else ''}."
