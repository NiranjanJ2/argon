"""Status and log tools — session state, mode, and daily log."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.daily.state import DailyState
from nanobot.daily.log import DailyLog
from nanobot.daily.habits import HabitsTracker


class GetStatusTool(Tool):
    """Get current session status in one call."""

    def __init__(self, state: DailyState, workspace: Path) -> None:
        self._state = state
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "get_status"

    @property
    def description(self) -> str:
        return (
            "Get Niranjan's current session status: mode, active task, work duration, "
            "home arrival, and current school period. One call covers everything."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        data = self._state.get()
        work_min = self._state.get_work_session_duration_minutes()
        lock_min = self._state.get_lock_in_duration_minutes()

        result: dict[str, Any] = {
            "mode": data.get("mode", "idle"),
            "current_task": data.get("current_task"),
            "home_arrival": data.get("home_arrival"),
            "work_session_minutes": work_min,
            "lock_in_minutes": lock_min,
        }

        # Include current school period if available
        try:
            from nanobot.schedule.manager import ScheduleManager
            mgr = ScheduleManager(self._workspace)
            result["school_period"] = mgr.get_current_period()
        except Exception:
            pass

        return json.dumps(result, indent=2)


class SetModeTool(Tool):
    """Set the current session mode."""

    def __init__(self, state: DailyState, log: DailyLog, habits: HabitsTracker) -> None:
        self._state = state
        self._log = log
        self._habits = habits

    @property
    def name(self) -> str:
        return "set_mode"

    @property
    def description(self) -> str:
        return (
            "Set Niranjan's current session mode. "
            "Modes: idle (free), working (focused session), napping, lock_in (no distractions), done (day over)."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["idle", "working", "napping", "lock_in", "done"],
                },
            },
            "required": ["mode"],
        }

    async def execute(self, **kwargs: Any) -> str:
        mode = kwargs["mode"]
        self._state.set_mode(mode)
        self._log.log_mode_change(mode)
        if mode == "working":
            self._habits.record_work_start()
        try:
            from nanobot.dashboard.app import push_update
            push_update("state")
        except Exception:
            pass
        return f"Mode: {mode}"


class LogNoteTool(Tool):
    """Append a note to today's daily log."""

    def __init__(self, state: DailyState, log: DailyLog) -> None:
        self._state = state
        self._log = log

    @property
    def name(self) -> str:
        return "log_note"

    @property
    def description(self) -> str:
        return "Append a timestamped note to Niranjan's daily log."

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "Note to log."},
            },
            "required": ["note"],
        }

    async def execute(self, **kwargs: Any) -> str:
        note = kwargs["note"].strip()
        if not note:
            return "Error: note is empty."
        self._state.add_note(note)
        self._log.log_note(note)
        return "Logged."


class ReadLogTool(Tool):
    """Read today's daily log."""

    def __init__(self, log: DailyLog) -> None:
        self._log = log

    @property
    def name(self) -> str:
        return "read_log"

    @property
    def description(self) -> str:
        return "Read today's daily log — everything Argon has recorded since midnight."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return self._log.read()
