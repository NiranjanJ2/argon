"""Tool for threads — the things in his life that have a history."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argon.core.threads import STATUSES, Threads
from argon.tools.base import Tool


class TrackThreadTool(Tool):
    """Start or update a thread."""

    def __init__(self, workspace: Path) -> None:
        self._threads = Threads(workspace)

    @property
    def name(self) -> str:
        return "track"

    @property
    def description(self) -> str:
        return (
            "Record a material update to an ongoing operational matter Niranjan "
            "is explicitly managing over time and is likely to follow up on, such "
            "as an active project or recurring commitment. Do not create a thread "
            "for an incidental person, ordinary class mention, hypothetical idea, "
            "routine conversation topic, or one-off errand. Add an entry only when "
            "the matter materially changes. Give aliases only for names he actually "
            "uses. Set status to done or dropped when the matter ends."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "What he calls it, e.g. 'Petoi robot'.",
                },
                "entry": {
                    "type": "string",
                    "description": "What happened, in one line. Omit if only updating status or summary.",
                },
                "summary": {
                    "type": "string",
                    "description": "One sentence on what this is. Set it once, revise if it changes.",
                },
                "status": {"type": "string", "enum": list(STATUSES)},
                "aliases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Other names he uses for it — how you will recognise it later.",
                },
            },
            "required": ["name"],
        }

    async def execute(self, **kwargs: Any) -> str:
        name = str(kwargs.get("name") or "").strip()
        if not name:
            return "Error: name required."
        thread = self._threads.note(
            name,
            entry=str(kwargs.get("entry") or ""),
            summary=kwargs.get("summary"),
            status=kwargs.get("status"),
            aliases=kwargs.get("aliases") or [],
        )
        return f"Tracked '{thread.name}' ({thread.status}, {len(thread.log)} entries)."


class ReadThreadTool(Tool):
    """Read a thread's full history."""

    def __init__(self, workspace: Path) -> None:
        self._threads = Threads(workspace)

    @property
    def name(self) -> str:
        return "read_thread"

    @property
    def description(self) -> str:
        return (
            "Read the full history of one thing — every entry, not just the "
            "recent ones already in your context. Use it when he asks what "
            "happened with something, or when you need more than the summary. "
            "With no name, lists everything you are tracking, including "
            "finished and dormant threads."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Which one. Omit to list all."},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        name = str(kwargs.get("name") or "").strip()
        if not name:
            everything = self._threads.all()
            if not everything:
                return "Nothing tracked yet."
            return json.dumps(
                [
                    {"name": t.name, "status": t.status, "summary": t.summary,
                     "last_touched": t.last_touched, "entries": len(t.log)}
                    for t in everything
                ],
                indent=2,
            )
        thread = self._threads.get(name)
        if thread is None:
            known = ", ".join(t.name for t in self._threads.all()) or "nothing yet"
            return f"No thread called '{name}'. Tracking: {known}."
        return thread.full()
