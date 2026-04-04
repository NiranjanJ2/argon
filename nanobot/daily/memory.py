"""Persistent memory — facts, preferences, and notes that survive across days.

File: workspace/memory.md
Never resets. Argon reads this at session start and writes to it when asked.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")


def _now() -> datetime:
    return datetime.now(_TZ)


class PersistentMemory:
    """Append-based persistent memory file."""

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "memory.md"

    def _ensure_file(self) -> None:
        if not self._path.exists():
            self._path.write_text("# Argon Memory\n\n")

    def remember(self, note: str) -> None:
        """Append a timestamped note to memory."""
        self._ensure_file()
        ts = _now().strftime("%Y-%m-%d %H:%M")
        with self._path.open("a") as f:
            f.write(f"\n- [{ts}] {note.strip()}\n")

    def recall(self) -> str:
        """Read all memory."""
        self._ensure_file()
        return self._path.read_text()

    def forget(self, keyword: str) -> int:
        """Remove lines containing keyword. Returns count removed."""
        self._ensure_file()
        lines = self._path.read_text().splitlines(keepends=True)
        keyword_lower = keyword.lower()
        kept = []
        removed = 0
        for line in lines:
            if keyword_lower in line.lower() and line.strip().startswith("-"):
                removed += 1
            else:
                kept.append(line)
        self._path.write_text("".join(kept))
        return removed
