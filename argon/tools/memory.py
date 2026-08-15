"""Remembering things Niranjan says.

Everything Argon knew about him lived in SOUL.md, which is biography, not a
record of what he has actually said. With no way to write anything down, the
check-in model was left inventing plausible-sounding work — a "UCLA lab
write-up" assembled out of a background note. Recording the real thing is the
fix for that, and it has to be one obvious tool call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argon.core.journal import Journal, has_relative_day_reference
from argon.tools.base import Tool


class RememberTool(Tool):
    """Write a durable fact to long-term memory.

    It used to default to a day note unless the model passed ``lasting=true``,
    which made "remember this" a coin flip: the ones that landed as day notes
    were handed to the nightly consolidation, and whatever it declined to carry
    forward was gone by morning. Nothing else in the system asks him twice.

    Day-scoped capture already has two owners that need no decision from the
    model — automatic journalling of tool calls, and ``log_note`` — so the only
    thing left for an *explicit* remember to mean is "this outlives today".
    """

    def __init__(self, workspace: Path) -> None:
        self._journal = Journal(workspace)

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a durable fact — this always outlives today. Record operational "
            "facts Niranjan explicitly states that will matter later: commitments, "
            "constraints, preferences, and corrections. Do not store incidental "
            "conversation, tentative ideas, hypotheses, or your own inference; "
            "today's journal already preserves the conversation, and anything that "
            "only matters this evening belongs in log_note instead. Saying you'll "
            "remember does nothing; this is what stores it. A one-off needs an "
            "absolute YYYY-MM-DD date in the sentence, and an until date for when "
            "it stops mattering. Set standing=true only for a recurring shape of "
            "his life such as school hours, when he is free, or a standing "
            "commitment."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": (
                        "What to remember, as a full sentence that will still "
                        "make sense months from now."
                    ),
                },
                "until": {
                    "type": "string",
                    "description": "YYYY-MM-DD after which it stops mattering.",
                },
                "standing": {
                    "type": "boolean",
                    "description": (
                        "True for a recurring shape of his schedule or life — "
                        "'school days end at 3:40', 'free after 4pm', 'practice "
                        "Tuesdays'. Never expires."
                    ),
                },
            },
            "required": ["fact"],
        }

    async def execute(self, **kwargs: Any) -> str:
        fact = str(kwargs.get("fact") or "").strip()
        if not fact:
            return "Error: fact required."
        standing = bool(kwargs.get("standing"))
        # Relative wording is the one thing that cannot be stored durably: read
        # back in a week, "tomorrow" is a different day. log_note is where it
        # belongs, and the day page is already keeping the conversation.
        if has_relative_day_reference(fact):
            return "Error: durable memories must use an absolute YYYY-MM-DD date."
        stored = self._journal.add_fact(
            fact,
            until=None if standing else (kwargs.get("until") or None),
            standing=standing,
        )
        kind = "Stored as a standing fact" if standing else "Stored long-term"
        return f"{kind}: {stored.text}"


class RecallTool(Tool):
    """Read back what is known — long-term facts plus today's notes."""

    def __init__(self, workspace: Path) -> None:
        self._journal = Journal(workspace)

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return (
            "Read back what you know about Niranjan: long-term facts plus what "
            "was noted today. Use it before claiming he told you something — if "
            "it is not here, he did not, and you must not invent it."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "day": {
                    "type": "string",
                    "description": "YYYY-MM-DD to read one past day's notes instead.",
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        if day := kwargs.get("day"):
            entries = self._journal.read_day(str(day))
            return entries or f"Nothing recorded on {day}."
        return self._journal.context() or "Nothing remembered yet."
