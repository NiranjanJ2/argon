"""Who this turn is talking to, and what he actually said.

Routing used to be mutable state on singleton tools: the loop called
``set_context(channel, chat_id)`` on the shared ``MessageTool`` and ``CronTool``
before executing each batch of tool calls. Interactive turns are deliberately
concurrent across sessions, so two overlapping turns — a Discord message and an
iOS webhook — raced on those fields. The later turn's ``set_context`` won, and
the earlier turn's ``message`` call went to the other conversation. The same
race reset ``_sent_in_turn``, so one turn could make another turn believe it had
already replied, and the reply was dropped.

The fix is that the destination is not global state at all. It is a frozen
record carried in a :class:`~contextvars.ContextVar`, and asyncio copies the
context into every task it creates — so each turn, and every tool call inside
it, sees its own. Tools ask ``turn.current()`` instead of remembering.

``said`` is the one mutable part, deliberately: it is per-turn scratch (what the
model already delivered) and it lives with the turn rather than on a tool that
outlives it.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

#: Turns Argon started itself. Nothing here is a user instruction, so nothing
#: here may authorise a mutation, journal as his speech, or satisfy a consent
#: prompt.
AUTOMATED_ORIGINS = frozenset({"cron", "checkin", "heartbeat", "system"})


@dataclass(frozen=True)
class TurnContext:
    """Immutable per-turn routing and provenance."""

    channel: str
    chat_id: str
    session_key: str
    message_id: str | None = None
    #: "user" for something Niranjan sent; see AUTOMATED_ORIGINS otherwise.
    origin: str = "user"
    #: Exactly what he typed this turn. Consent is checked against this, never
    #: against "some message arrived recently".
    user_text: str = ""
    #: Background turns run with read-only authority.
    background: bool = False
    #: Unique to this turn. A consent prompt records the turn it was asked in,
    #: so "he replied yes" cannot be satisfied by the same turn that asked —
    #: the model gets several tool calls per turn, and `user_text` does not
    #: change between them.
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    #: What the `message` tool delivered during this turn. Mutable on purpose.
    said: list[str] = field(default_factory=list)

    @property
    def automated(self) -> bool:
        return self.origin in AUTOMATED_ORIGINS

    @property
    def deliverable(self) -> bool:
        """Is there somewhere real to send to?"""
        return bool(self.channel) and bool(self.chat_id)


_current: ContextVar[TurnContext | None] = ContextVar("argon_turn", default=None)


def current() -> TurnContext | None:
    """The turn in flight on this task, or None outside a turn."""
    return _current.get()


@contextmanager
def use(ctx: TurnContext) -> Iterator[TurnContext]:
    """Run a block as this turn. Restores the previous one on the way out."""
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)


__all__ = ["TurnContext", "AUTOMATED_ORIGINS", "current", "use"]
