"""Adaptive reminder cron — checks in on Niranjan based on current state and time of day.

Registered automatically at gateway startup. Not a user-created cron job.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")


def _now() -> datetime:
    return datetime.now(_TZ)


def should_send_reminder(workspace: Path) -> tuple[bool, str]:
    """Returns (should_send, reason). Called by the cron job before sending."""
    from nanobot.daily.state import DailyState

    state = DailyState(workspace)
    data = state.get()
    mode = data.get("mode", "idle")
    now = _now()
    hour = now.hour

    # Never remind before noon or if napping
    if hour < 12 or mode == "napping":
        return False, "napping or before noon"

    # If done for the day, no reminders
    if mode == "done":
        return False, "done for the day"

    # No home arrival yet — don't nag
    if not data.get("home_arrival") and not data.get("onboarding_done"):
        return False, "not home yet"

    # Night wind-down (after 10pm) — only remind if still has urgent tasks
    if hour >= 22:
        from nanobot.daily.todo import DailyTodo
        todo = DailyTodo(workspace)
        high_priority = [t for t in todo.get_pending() if t.get("priority") == "high"]
        if not high_priority:
            return False, "late and no urgent tasks"
        return True, f"{len(high_priority)} high-priority task(s) still pending"

    # Idle after getting home — gentle check-in
    if mode == "idle" and data.get("home_arrival"):
        return True, "idle after arriving home"

    # Active work session
    if mode in ("working", "lock_in"):
        work_min = state.get_work_session_duration_minutes() or 0
        # Check in every 5 minutes during work session (cron runs every 5 min when working)
        return True, f"active work session ({work_min}m in)"

    return False, f"no reminder needed (mode={mode})"


def get_reminder_interval_minutes(workspace: Path) -> int:
    """Return how often the reminder cron should fire based on current state."""
    from nanobot.daily.state import DailyState

    state = DailyState(workspace)
    mode = state.get_mode()
    hour = _now().hour

    if mode in ("working", "lock_in"):
        return 5   # every 5 min during active sessions
    if hour >= 22:
        return 30  # every 30 min late night
    if hour < 15:
        return 30  # sparse during school hours
    return 15      # every 15 min default after school
