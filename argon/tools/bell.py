"""Schedule tool — lets the agent query and override the bell schedule."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from argon.productivity.bell import NO_SCHOOL, SCHEDULES, ScheduleManager
from argon.tools.base import Tool


class ScheduleTool(Tool):
    """Query Whitney High School bell schedule and manage overrides."""

    def __init__(self, workspace: Path) -> None:
        self._mgr = ScheduleManager(workspace)

    @property
    def name(self) -> str:
        return "get_bell_info"

    @property
    def description(self) -> str:
        return (
            "Query Whitney High School bell schedule. "
            "Actions: current_period (what period is it right now + time remaining), "
            "today_schedule (full today's schedule), "
            "set_schedule_type (override a day's schedule; 'none' cancels school for a "
            "holiday or break, and start_date/end_date cover a range), "
            "list_schedule_types (see all valid schedule type names)."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["current_period", "today_schedule", "set_schedule_type", "list_schedule_types"],
                },
                "schedule_type": {
                    "type": "string",
                    "description": "Schedule type for set_schedule_type (e.g. 'minimum_day', 'activity'). Use 'none' for a holiday or break.",
                },
                "start_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD. Defaults to today. With end_date, covers a range.",
                },
                "end_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD, inclusive. Use for a multi-day break.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs["action"]

        if action == "current_period":
            return json.dumps(self._mgr.get_current_period(), indent=2)

        if action == "today_schedule":
            return json.dumps(self._mgr.get_full_schedule_today(), indent=2)

        if action == "set_schedule_type":
            schedule_type = kwargs.get("schedule_type")
            if not schedule_type:
                return "Error: schedule_type required."
            try:
                start = (
                    date.fromisoformat(kwargs["start_date"])
                    if kwargs.get("start_date")
                    else None
                )
                if kwargs.get("end_date"):
                    count = self._mgr.set_override_range(
                        schedule_type,
                        start or date.today(),
                        date.fromisoformat(kwargs["end_date"]),
                    )
                    return f"Schedule set to '{schedule_type}' for {count} day(s)."
                self._mgr.set_override(schedule_type, start)
                return f"Schedule set to '{schedule_type}' for {start or 'today'}."
            except ValueError as e:
                return f"Error: {e}"

        if action == "list_schedule_types":
            return json.dumps([*SCHEDULES, NO_SCHOOL], indent=2)

        return f"Error: Unknown action '{action}'."
