"""Google Calendar study block scheduling for Argon tasks.

Reads pending tasks from GoogleTasksStore (the canonical source of truth)
and creates time-blocked Calendar events in available free slots.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")
_STUDY_BLOCK_TAG = "[Argon Study Block]"


def _now() -> datetime:
    return datetime.now(_TZ)


def schedule_study_blocks(workspace: Path, arrival_time: datetime | None = None) -> dict[str, Any]:
    """Create time-blocked Calendar events for today's pending tasks.

    1. Deletes existing Argon study blocks for today.
    2. Gets free/busy for today.
    3. Creates events in available slots starting from arrival_time (or now).

    Returns {created: int, skipped: int, calendar_id: str}
    """
    from nanobot.google.tasks_store import GoogleTasksStore
    from nanobot.google.base import build_google_service

    try:
        svc = build_google_service(workspace, "calendar", "v3", "work")
    except Exception:
        return {"error": "work account not authenticated"}

    store = GoogleTasksStore(workspace)
    try:
        pending = store.get_pending()
    except Exception as e:
        return {"error": f"Could not load tasks: {e}"}

    if not pending:
        return {"created": 0, "skipped": 0, "message": "No pending tasks."}

    cal_id = "primary"
    today = _now().date()
    day_start = datetime.combine(today, __import__("datetime").time(0, 0), tzinfo=_TZ)
    day_end = datetime.combine(today, __import__("datetime").time(23, 59), tzinfo=_TZ)

    # Delete existing study blocks
    existing = svc.events().list(
        calendarId=cal_id,
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        q=_STUDY_BLOCK_TAG,
        singleEvents=True,
    ).execute()
    for ev in existing.get("items", []):
        try:
            svc.events().delete(calendarId=cal_id, eventId=ev["id"]).execute()
        except Exception:
            pass

    # Get free/busy
    fb_result = svc.freebusy().query(body={
        "timeMin": day_start.isoformat(),
        "timeMax": day_end.isoformat(),
        "items": [{"id": cal_id}],
    }).execute()
    busy_slots = fb_result.get("calendars", {}).get(cal_id, {}).get("busy", [])
    busy: list[tuple[datetime, datetime]] = []
    for slot in busy_slots:
        try:
            busy.append((
                datetime.fromisoformat(slot["start"]),
                datetime.fromisoformat(slot["end"]),
            ))
        except Exception:
            pass

    # Schedule tasks into free slots
    cursor = arrival_time or _now()
    if cursor.hour < 14:
        cursor = cursor.replace(hour=14, minute=0, second=0, microsecond=0)

    created = 0
    skipped = 0
    buffer_minutes = 10

    for task in pending:
        estimate = task.get("time_estimate_min") or _default_estimate(task)
        if not estimate:
            skipped += 1
            continue

        block_start = _find_free_slot(cursor, estimate, busy, day_end)
        if block_start is None:
            skipped += 1
            continue

        block_end = block_start + timedelta(minutes=estimate)
        subject = task.get("subject", "")
        title = f"{_STUDY_BLOCK_TAG} {task['title']}" + (f" ({subject})" if subject else "")

        try:
            svc.events().insert(calendarId=cal_id, body={
                "summary": title,
                "start": {"dateTime": block_start.isoformat()},
                "end": {"dateTime": block_end.isoformat()},
                "colorId": "2",  # sage green
                "description": f"Argon-scheduled study block.\nTask ID: {task['id']}",
            }).execute()
            created += 1
            busy.append((block_start, block_end))
            busy.sort(key=lambda x: x[0])
            cursor = block_end + timedelta(minutes=buffer_minutes)
        except Exception:
            skipped += 1

    return {"created": created, "skipped": skipped, "calendar_id": cal_id}


def _find_free_slot(
    after: datetime,
    duration_min: int,
    busy: list[tuple[datetime, datetime]],
    deadline: datetime,
) -> datetime | None:
    cursor = after
    while cursor + timedelta(minutes=duration_min) <= deadline:
        block_end = cursor + timedelta(minutes=duration_min)
        conflict = next((b for b in busy if b[0] < block_end and b[1] > cursor), None)
        if conflict is None:
            return cursor
        cursor = conflict[1]
    return None


def _default_estimate(task: dict) -> int | None:
    return {"high": 60, "medium": 45, "low": 30}.get(task.get("priority", "medium"))
