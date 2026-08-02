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

from argon.core.journal import Journal
from argon.tools.base import Tool


class RememberTool(Tool):
    """Write something to today's journal, or straight to long-term memory."""

    def __init__(self, workspace: Path) -> None:
        self._journal = Journal(workspace)

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Write down something Niranjan told you, so you still know it later. "
            "Call this whenever he mentions a plan, a commitment, a deadline, a "
            "preference, or anything about his life — 'today is my last day of "
            "the internship', 'tomorrow is a rest day', 'I hate being asked "
            "twice'. Saying you'll remember does nothing; this is what stores it. "
            "Today's notes are reviewed at the end of the day and the ones that "
            "still matter are kept. Set lasting=true only for facts that will be "
            "true for months."
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
                "lasting": {
                    "type": "boolean",
                    "description": "True to store permanently instead of in today's notes.",
                },
                "until": {
                    "type": "string",
                    "description": "YYYY-MM-DD after which it stops mattering. Only with lasting.",
                },
            },
            "required": ["fact"],
        }

    async def execute(self, **kwargs: Any) -> str:
        fact = str(kwargs.get("fact") or "").strip()
        if not fact:
            return "Error: fact required."
        if kwargs.get("lasting"):
            stored = self._journal.add_fact(fact, until=kwargs.get("until") or None)
            return f"Stored long-term: {stored.text}"
        self._journal.note(fact, kind="said")
        return "Noted for today."


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
