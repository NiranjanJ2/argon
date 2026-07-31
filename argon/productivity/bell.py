"""Whitney High School bell schedules and current-period resolution.

Each schedule is a list of (label, start_hhmm, end_hhmm) tuples.
Times are 24-hour integers, e.g. 8:30 AM = 830, 1:30 PM = 1330.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

from argon.clock import now as _now_local
from argon.clock import tz as _tz

# ---------------------------------------------------------------------------
# Schedule data
# ---------------------------------------------------------------------------

# Each entry: (label, start HHMM, end HHMM)
ScheduleEntry = tuple[str, int, int]

# Override value meaning "no school this day" — holidays, breaks, summer.
NO_SCHOOL = "none"

SCHEDULES: dict[str, list[ScheduleEntry]] = {
    "regular": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  928),
        ("Period 2",  933, 1030),
        ("Snack",    1030, 1045),
        ("Period 3", 1050, 1147),
        ("Period 4", 1152, 1249),
        ("Lunch",    1249, 1321),
        ("Period 5", 1326, 1423),
        ("HR",       1423, 1434),
        ("Period 6", 1439, 1536),
    ],
    "early_release": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  921),
        ("Period 2",  926, 1016),
        ("Snack",    1016, 1031),
        ("Period 3", 1036, 1126),
        ("Period 4", 1131, 1221),
        ("Lunch",    1221, 1251),
        ("Period 5", 1256, 1346),
        ("Period 6", 1351, 1441),
        ("Meeting",  1450, 1550),
    ],
    "advisement": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  923),
        ("Period 2",  928, 1020),
        ("Snack",    1020, 1035),
        ("Period 3", 1040, 1132),
        ("Period 4", 1137, 1229),
        ("Lunch",    1229, 1301),
        ("Period 5", 1306, 1358),
        ("Adv/HR",   1358, 1439),
        ("Period 6", 1444, 1536),
    ],
    "activity": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  924),
        ("Period 2",  929, 1022),
        ("Snack",    1022, 1037),
        ("Period 3", 1042, 1135),
        ("Period 4", 1140, 1233),
        ("Activity", 1233, 1308),
        ("Lunch",    1308, 1340),
        ("Period 5", 1345, 1438),
        ("Period 6", 1443, 1536),
    ],
    "minimum_day": [
        ("Period 0",  750,  825),
        ("Period 1",  830,  907),
        ("Period 2",  912,  948),
        ("Period 3",  953, 1029),
        ("Snack",    1029, 1049),
        ("Period 4", 1054, 1130),
        ("Period 5", 1135, 1211),
        ("Period 6", 1216, 1252),
    ],
    "special_events": [
        ("Period 0",  730,  825),
        ("Period 1",  830,  921),
        ("Period 2",  926, 1016),
        ("Snack",    1016, 1031),
        ("Period 3", 1036, 1126),
        ("Period 4", 1131, 1221),
        ("Event",    1221, 1314),
        ("Lunch",    1314, 1346),
        ("Period 5", 1351, 1441),
        ("Period 6", 1446, 1536),
    ],
    "comp_1st_qtr": [
        ("Period 0",  730,  825),
        ("1st Comp",  830, 1033),
        ("Snack",    1033, 1048),
        ("2nd Comp", 1053, 1256),
        ("Lunch",    1256, 1328),
        ("3rd Comp", 1333, 1536),
    ],
    "comp_semester": [
        ("Period 0",  730,  825),
        ("1st Comp",  830, 1030),
        ("Snack",    1030, 1055),
        ("2nd Comp", 1100, 1300),
    ],
    "first_day": [
        ("Period 0",   730,  825),
        ("Rally",      830,  900),
        ("Schedule",   900,  915),
        ("Period 1",   920, 1006),
        ("Period 2",  1011, 1057),
        ("Snack",     1057, 1117),
        ("Period 3",  1122, 1208),
        ("Period 4",  1213, 1259),
        ("Lunch",     1259, 1339),
        ("Period 5",  1344, 1430),
        ("Period 6",  1435, 1521),
    ],
}

# Default schedule by weekday (0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri)
DEFAULT_BY_WEEKDAY: dict[int, str] = {
    0: "regular",        # Monday
    1: "early_release",  # Tuesday
    2: "advisement",     # Wednesday
    3: "regular",        # Thursday
    4: "regular",        # Friday (sometimes activity — needs manual override)
}

SCHEDULE_DISPLAY_NAMES: dict[str, str] = {
    "regular":        "Regular (M/Th/F)",
    "early_release":  "Early Release (Tuesday)",
    "advisement":     "Advisement (Wednesday)",
    "activity":       "Activity Friday",
    "minimum_day":    "Minimum Day",
    "special_events": "Special Events",
    "comp_1st_qtr":   "1st Quarter Comps",
    "comp_semester":  "Semester / 3rd Qtr Comps",
    "first_day":      "First Day of School",
}







def _hhmm_to_time(hhmm: int) -> time:
    h, m = divmod(hhmm, 100)
    return time(h, m)



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
        """Override the schedule type for a given date (default: today).

        ``NO_SCHOOL`` marks a holiday or break — without it a weekday could only
        ever be moved to a *different* schedule, never cancelled, so Argon
        announced Period 3 on winter break and all summer.
        """
        if schedule_type != NO_SCHOOL and schedule_type not in SCHEDULES:
            raise ValueError(
                f"Unknown schedule type '{schedule_type}'. "
                f"Valid: {[*SCHEDULES, NO_SCHOOL]}"
            )
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

    def is_school_day(self, for_date: date | None = None) -> bool:
        """Weekends are not school days unless explicitly overridden."""
        return self.get_schedule_type(for_date) is not None

    def get_schedule_type(self, for_date: date | None = None) -> str | None:
        """Schedule name for *for_date*, or None when there is no school."""
        target = for_date or _now_local().date()
        overrides = self._load_overrides()
        if target.isoformat() in overrides:
            override = overrides[target.isoformat()]
            return None if override == NO_SCHOOL else override
        # DEFAULT_BY_WEEKDAY only covers Mon-Fri. Defaulting the miss to
        # "regular" put Argon in Period 3 on a Sunday morning.
        return DEFAULT_BY_WEEKDAY.get(target.weekday())

    def get_schedule(self, for_date: date | None = None) -> list[ScheduleEntry]:
        schedule_type = self.get_schedule_type(for_date)
        return SCHEDULES[schedule_type] if schedule_type else []

    # ------------------------------------------------------------------
    # Current period
    # ------------------------------------------------------------------

    def get_current_period(self) -> dict:
        """Return info about what's happening right now."""
        now = _now_local()
        schedule = self.get_schedule()
        if not schedule:
            return {"status": "no_school", "message": "No school today."}
        now_hhmm = now.hour * 100 + now.minute

        for label, start, end in schedule:
            if start <= now_hhmm < end:
                end_dt = datetime.combine(now.date(), _hhmm_to_time(end), tzinfo=_tz())
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
                start_dt = datetime.combine(now.date(), _hhmm_to_time(start), tzinfo=_tz())
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
        if not schedule_type:
            return {"schedule_type": None, "display_name": "No school", "periods": []}
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
