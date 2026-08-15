"""Back off — the tool Argon needs when Niranjan says he is not working."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argon.services.reminder import clear_snooze, snooze, snooze_until
from argon.tools.base import Tool, ToolResult


class SnoozeCheckInsTool(Tool):
    """Stop starting conversations for a while."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "snooze_check_ins"

    @property
    def description(self) -> str:
        return (
            "Stop sending Niranjan unprompted check-ins for a while. Call this "
            "whenever he says he is resting, taking a rest day, busy, away, done "
            "for the day, or asks you to stop messaging — acknowledging it in "
            "chat is not enough, nothing changes unless you call this. Use "
            "hours=24 for 'today is a rest day', hours=12 for an evening off. "
            "Call with resume=true if he says he is ready to work again."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "number",
                    "description": "How long to stay quiet. Default 12.",
                },
                "reason": {
                    "type": "string",
                    "description": "What he said, e.g. 'rest day'.",
                },
                "resume": {
                    "type": "boolean",
                    "description": "True to end an active snooze and resume check-ins.",
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        if kwargs.get("resume"):
            clear_snooze(self._workspace)
            return ToolResult("Check-ins resumed.")

        hours = float(kwargs.get("hours") or 12)
        reason = str(kwargs.get("reason") or "")
        until = snooze(self._workspace, hours, reason)
        return ToolResult(f"Staying quiet until {until:%a %-I:%M %p}. I won't message first before then.")


class CheckInStatusTool(Tool):
    """Report whether check-ins are currently silenced."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "check_in_status"

    @property
    def description(self) -> str:
        return "Report whether unprompted check-ins are snoozed, and until when."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        until = snooze_until(self._workspace)
        if until is None:
            return "Check-ins are active."
        return f"Check-ins are snoozed until {until:%a %-I:%M %p}."
