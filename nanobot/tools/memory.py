"""Memory tools — long-term and daily persistent memory."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nanobot.agent.tools.base import Tool

_TZ = ZoneInfo("America/Los_Angeles")


def _now_str() -> str:
    return datetime.now(_TZ).strftime("%Y-%m-%d %H:%M")


class RememberTool(Tool):
    """Append a fact to long-term memory (memory/MEMORY.md)."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "remember"

    @property
    def description(self) -> str:
        return (
            "Store a fact in long-term memory. Survives across all sessions. "
            "Use for preferences, recurring context, and things Niranjan explicitly asks you to remember. "
            "For today-only context, use log_note instead."
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
        memory_file = self._workspace / "memory" / "MEMORY.md"
        memory_file.parent.mkdir(parents=True, exist_ok=True)
        with memory_file.open("a", encoding="utf-8") as f:
            f.write(f"\n- [{_now_str()}] {note}\n")
        return f"Remembered: {note}"


class ForgetTool(Tool):
    """Remove entries from long-term memory by keyword."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "forget"

    @property
    def description(self) -> str:
        return "Remove entries from long-term memory that match a keyword."

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
        keyword = kwargs["keyword"].strip().lower()
        memory_file = self._workspace / "memory" / "MEMORY.md"
        if not memory_file.exists():
            return "Memory is empty."
        lines = memory_file.read_text(encoding="utf-8").splitlines(keepends=True)
        kept, removed = [], 0
        for line in lines:
            if keyword in line.lower() and line.strip().startswith("-"):
                removed += 1
            else:
                kept.append(line)
        memory_file.write_text("".join(kept), encoding="utf-8")
        return f"Removed {removed} entry/entries matching '{keyword}'."
