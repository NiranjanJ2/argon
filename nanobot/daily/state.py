"""Daily session state — tracks mode, current task, home arrival, etc.

State file: workspace/daily/state.json
Resets at 4:00 AM.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")

Mode = Literal["idle", "working", "napping", "lock_in", "done"]


def _now() -> datetime:
    return datetime.now(_TZ)


def _today_key() -> str:
    """Returns date string, but treats midnight–4am as previous day."""
    now = _now()
    if now.hour < 4:
        from datetime import timedelta
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


class DailyState:
    """Persisted daily session state."""

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "daily" / "state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            # Reset if it's a new day
            if data.get("date") != _today_key():
                return self._defaults()
            return data
        return self._defaults()

    def _defaults(self) -> dict[str, Any]:
        return {
            "date": _today_key(),
            "mode": "idle",
            "home_arrival": None,
            "current_task": None,
            "work_session_start": None,
            "nap_start": None,
            "lock_in_start": None,
            "onboarding_done": False,
            "ready_to_work_times": [],  # list of ISO timestamps when user said ready
            "notes": [],
        }

    def _save(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get(self) -> dict[str, Any]:
        return self._load()

    def set_mode(self, mode: Mode) -> None:
        data = self._load()
        data["mode"] = mode
        now_iso = _now().isoformat()
        if mode == "working":
            data["work_session_start"] = now_iso
            if not data.get("ready_to_work_times"):
                data["ready_to_work_times"] = []
            data["ready_to_work_times"].append(now_iso)
        elif mode == "napping":
            data["nap_start"] = now_iso
            data["work_session_start"] = None
        elif mode == "lock_in":
            data["lock_in_start"] = now_iso
        elif mode == "idle":
            data["work_session_start"] = None
            data["nap_start"] = None
            data["lock_in_start"] = None
        self._save(data)

    def set_home_arrival(self) -> None:
        data = self._load()
        data["home_arrival"] = _now().isoformat()
        self._save(data)

    def set_current_task(self, task: str | None) -> None:
        data = self._load()
        data["current_task"] = task
        self._save(data)

    def mark_onboarding_done(self) -> None:
        data = self._load()
        data["onboarding_done"] = True
        self._save(data)

    def add_note(self, note: str) -> None:
        data = self._load()
        data.setdefault("notes", []).append({
            "time": _now().isoformat(),
            "text": note,
        })
        self._save(data)

    def get_mode(self) -> Mode:
        return self._load().get("mode", "idle")

    def get_current_task(self) -> str | None:
        return self._load().get("current_task")

    def get_work_session_duration_minutes(self) -> int | None:
        data = self._load()
        start = data.get("work_session_start")
        if not start:
            return None
        delta = _now() - datetime.fromisoformat(start)
        return int(delta.total_seconds() / 60)

    def get_lock_in_duration_minutes(self) -> int | None:
        data = self._load()
        start = data.get("lock_in_start")
        if not start:
            return None
        delta = _now() - datetime.fromisoformat(start)
        return int(delta.total_seconds() / 60)
