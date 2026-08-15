"""Status and log tools — session state, mode, and daily log."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from argon.productivity.habits import HabitsTracker
from argon.productivity.log import DailyLog
from argon.productivity.state import DailyState
from argon.tools.base import Tool, ToolResult


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
            "and current school period. One call covers everything."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        data = self._state.get()
        session = self._state.get_session()

        # `home_arrival` is deliberately absent. Nothing in production ever set
        # it — the "Neon is home" skill only writes a log note — so it was
        # always null, and a null field reads as "he is not home yet" rather
        # than "this is not tracked". Do not advertise state that cannot become
        # true; the arrival is in today's log if it matters.
        result: dict[str, Any] = {
            "mode": data.get("mode", "idle"),
            "current_task": data.get("current_task"),
            "work_session_minutes": self._state.get_work_session_duration_minutes(),
            "lock_in_minutes": self._state.get_lock_in_duration_minutes(),
            # Stated outright so the model never has to infer it from a start
            # timestamp. It used to read one off a task record that had no day
            # boundary and announce sessions that ended two days earlier.
            "session": (
                {
                    "task": session.get("title"),
                    "kind": session.get("kind"),
                    "minutes": session.get("elapsed_min"),
                }
                if session
                else None
            ),
        }

        # Include current school period if available
        try:
            from argon.productivity.bell import ScheduleManager
            mgr = ScheduleManager(self._workspace)
            result["school_period"] = mgr.get_current_period()
        except Exception:
            pass

        # Screen Time, but only when it is not the boring answer. The model
        # needs to know when a block it asked for never landed on the phone.
        try:
            from argon.ios import mode as ios_mode

            state, detail = ios_mode.convergence()
            desired = ios_mode.get_mode()
            if desired["mode"] != "off" or state != "converged":
                result["phone_focus"] = {
                    "requested": desired["mode"],
                    "status": state,
                    **({"detail": detail} if detail else {}),
                }
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
        was = self._state.get_mode()
        self._state.set_mode(mode)
        self._log.log_mode_change(mode)
        if mode == "working":
            self._habits.record_work_start()

        # Leaving lock_in releases the phone. Without this, "I'm done" ended
        # the session on the server and left the Screen Time shield up, with
        # nothing left in Argon's state to explain why the phone was blocked.
        released = ""
        if was == "lock_in" and mode != "lock_in":
            try:
                from argon.ios import mode as ios_mode

                if ios_mode.get_mode()["mode"] == "lock_in":
                    ios_mode.set_mode("off", reason="lock-in session ended")
                    released = " Screen Time block released."
            except Exception:  # noqa: BLE001 — the mode change itself still stands
                logger.warning("Could not release the phone block on leaving lock_in")

        return ToolResult(f"Mode: {mode}.{released}")


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
            return ToolResult("Error: note is empty.", success=False)
        self._state.add_note(note)
        self._log.log_note(note)
        return ToolResult("Logged.")


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
