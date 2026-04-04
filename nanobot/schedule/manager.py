"""Schedule manager — determines current period and time remaining."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nanobot.schedule.schedules import (
    DEFAULT_BY_WEEKDAY,
    SCHEDULE_DISPLAY_NAMES,
    SCHEDULES,
    ScheduleEntry,
)

_TZ = ZoneInfo("America/Los_Angeles")  # Whitney High School timezone


def _hhmm_to_time(hhmm: int) -> time:
    h, m = divmod(hhmm, 100)
    return time(h, m)


def _now_local() -> datetime:
    return datetime.now(_TZ)


class ScheduleManager:
    """Manages Whitney bell schedules with per-day overrides."""

    def __init__(self, workspace: Path) -> None:
        self._overrides_path = workspace / "schedule" / "overrides.json"
        self._overrides_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Override persistence
    # ------------------------------------------------------------------

    def _load_overrides(self) -> dict[str, str]:
        if self._overrides_path.exists():
            return json.loads(self._overrides_path.read_text())
        return {}

    def _save_overrides(self, overrides: dict[str, str]) -> None:
        self._overrides_path.write_text(json.dumps(overrides, indent=2))

    def set_override(self, schedule_type: str, for_date: date | None = None) -> None:
        """Override the schedule type for a given date (default: today)."""
        if schedule_type not in SCHEDULES:
            raise ValueError(f"Unknown schedule type '{schedule_type}'. Valid: {list(SCHEDULES)}")
        target = for_date or _now_local().date()
        overrides = self._load_overrides()
        overrides[target.isoformat()] = schedule_type
        self._save_overrides(overrides)

    def clear_override(self, for_date: date | None = None) -> None:
        target = for_date or _now_local().date()
        overrides = self._load_overrides()
        overrides.pop(target.isoformat(), None)
        self._save_overrides(overrides)

    # ------------------------------------------------------------------
    # Schedule resolution
    # ------------------------------------------------------------------

    def get_schedule_type(self, for_date: date | None = None) -> str:
        target = for_date or _now_local().date()
        overrides = self._load_overrides()
        if target.isoformat() in overrides:
            return overrides[target.isoformat()]
        weekday = target.weekday()
        return DEFAULT_BY_WEEKDAY.get(weekday, "regular")

    def get_schedule(self, for_date: date | None = None) -> list[ScheduleEntry]:
        return SCHEDULES[self.get_schedule_type(for_date)]

    # ------------------------------------------------------------------
    # Current period
    # ------------------------------------------------------------------

    def get_current_period(self) -> dict:
        """Return info about what's happening right now."""
        now = _now_local()
        schedule = self.get_schedule()
        now_hhmm = now.hour * 100 + now.minute

        for label, start, end in schedule:
            if start <= now_hhmm < end:
                end_dt = datetime.combine(now.date(), _hhmm_to_time(end), tzinfo=_TZ)
                remaining = int((end_dt - now).total_seconds() / 60)
                return {
                    "status": "in_period",
                    "period": label,
                    "ends_at": f"{end // 100}:{end % 100:02d}",
                    "minutes_remaining": remaining,
                }

        # Between periods — find next
        for label, start, end in schedule:
            if now_hhmm < start:
                start_dt = datetime.combine(now.date(), _hhmm_to_time(start), tzinfo=_TZ)
                until = int((start_dt - now).total_seconds() / 60)
                return {
                    "status": "between_periods",
                    "next_period": label,
                    "starts_at": f"{start // 100}:{start % 100:02d}",
                    "minutes_until": until,
                }

        # School day over
        return {"status": "school_over", "message": "School day is over."}

    def get_full_schedule_today(self) -> dict:
        schedule_type = self.get_schedule_type()
        entries = self.get_schedule()
        return {
            "schedule_type": schedule_type,
            "display_name": SCHEDULE_DISPLAY_NAMES.get(schedule_type, schedule_type),
            "periods": [
                {
                    "label": label,
                    "start": f"{start // 100}:{start % 100:02d}",
                    "end": f"{end // 100}:{end % 100:02d}",
                }
                for label, start, end in entries
            ],
        }

    def get_all_schedule_types(self) -> list[str]:
        return list(SCHEDULES.keys())
