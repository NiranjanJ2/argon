"""Memory tools — persistent facts that survive across sessions."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.daily.memory import PersistentMemory


class RememberTool(Tool):
    """Store a fact in persistent memory."""

    def __init__(self, memory: PersistentMemory) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact or note in Niranjan's persistent memory. "
            "Survives across conversations. Use for preferences, recurring context, important facts."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "Fact or note to remember."},
            },
            "required": ["note"],
        }

    async def execute(self, **kwargs: Any) -> str:
        note = kwargs["note"].strip()
        if not note:
            return "Error: note is empty."
        self._memory.remember(note)
        return f"Remembered: {note}"


class RecallTool(Tool):
    """Read all persistent memory."""

    def __init__(self, memory: PersistentMemory) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "recall"

    @property
    def description(self) -> str:
        return "Read all of Niranjan's stored persistent memory."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return self._memory.recall()


class ForgetTool(Tool):
    """Remove entries from persistent memory."""

    def __init__(self, memory: PersistentMemory) -> None:
        self._memory = memory

    @property
    def name(self) -> str:
        return "forget"

    @property
    def description(self) -> str:
        return "Remove entries from persistent memory that match a keyword."

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Keyword to match against memory entries.",
                },
            },
            "required": ["keyword"],
        }

    async def execute(self, **kwargs: Any) -> str:
        keyword = kwargs["keyword"].strip()
        removed = self._memory.forget(keyword)
        return f"Removed {removed} entry/entries matching '{keyword}'."
