"""One clock for the whole process.

Local time appeared as six independent ``ZoneInfo("America/Los_Angeles")``
constants, so ``agents.defaults.timezone`` governed the model's sense of time
while the day rollover, work-session durations, and bell schedule silently did
not. This is the single knob; ``configure()`` is called once at startup.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Whitney High School / Niranjan. Overridden from config at startup.
_tz = ZoneInfo("America/Los_Angeles")

# The "day" runs 4am-4am: work past midnight belongs to the day it started on.
DAY_START_HOUR = 4


def configure(timezone: str | None) -> None:
    global _tz
    if not timezone:
        return
    try:
        _tz = ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 — a bad tz must not stop startup
        from loguru import logger

        logger.warning("Unknown timezone {!r}; keeping {}", timezone, _tz)


def tz() -> ZoneInfo:
    return _tz


def now() -> datetime:
    return datetime.now(_tz)


def today_key(offset_days: int = 0) -> str:
    """Date string for the logical day, treating midnight-4am as yesterday."""
    current = now()
    if current.hour < DAY_START_HOUR:
        current -= timedelta(days=1)
    return (current + timedelta(days=offset_days)).strftime("%Y-%m-%d")
