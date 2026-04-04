"""Google Tasks ↔ Argon todo sync + Google Calendar study block scheduling.

Sync direction:
  Argon todo → Google Tasks  (create/complete)
  Google Tasks → Argon todo  (completion only — GT is a mirror, not the source of truth)

Study blocks:
  After daily plan is built, creates time-blocked Calendar events for each pending task.
  Existing Argon study-block events are deleted first to avoid duplicates.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")
_STUDY_BLOCK_TAG = "[Argon Study Block]"
_DEFAULT_TASKLIST = "@default"


def _now() -> datetime:
    return datetime.now(_TZ)


# ---------------------------------------------------------------------------
# Google Tasks sync
# ---------------------------------------------------------------------------


def sync_tasks(workspace: Path) -> dict[str, int]:
    """Bidirectional sync between Argon todo and Google Tasks.

    Returns counts: {pushed, completed_from_gt, already_synced}
    """
    from nanobot.daily.todo import DailyTodo
    from nanobot.google.auth import GoogleAuth
    from googleapiclient.discovery import build

    auth = GoogleAuth(workspace)
    try:
        creds = auth.get_credentials("work")
    except Exception:
        return {"error": "work account not authenticated"}

    svc = build("tasks", "v1", credentials=creds)
    todo = DailyTodo(workspace)
    tasks = todo.get_all()

    stats = {"pushed": 0, "completed_from_gt": 0, "already_synced": 0}

    # Build a map of google_task_id → local task for fast lookup
    gt_id_map: dict[str, dict] = {
        t["google_task_id"]: t
        for t in tasks
        if t.get("google_task_id")
    }

    # ── Pull completions from Google Tasks ──────────────────────────────────
    try:
        result = svc.tasks().list(
            tasklist=_DEFAULT_TASKLIST,
            showCompleted=True,
            showHidden=True,
        ).execute()
        gt_tasks = result.get("items", [])
    except Exception:
        gt_tasks = []

    for gt in gt_tasks:
        gt_id = gt.get("id")
        if not gt_id or gt_id not in gt_id_map:
            continue
        local = gt_id_map[gt_id]
        if gt.get("status") == "completed" and not local.get("done"):
            todo.complete_task(local["id"])
            stats["completed_from_gt"] += 1

    # ── Push pending Argon tasks that aren't in Google Tasks yet ────────────
    tasks = todo.get_all()  # reload after potential completions above
    for t in tasks:
        if t.get("done"):
            continue
        if t.get("google_task_id"):
            stats["already_synced"] += 1
            continue

        body: dict[str, Any] = {"title": t["title"]}
        if t.get("notes"):
            body["notes"] = t["notes"]
        if t.get("due"):
            # Google Tasks due must be RFC 3339 midnight UTC
            try:
                due_dt = datetime.fromisoformat(t["due"])
                body["due"] = due_dt.strftime("%Y-%m-%dT00:00:00.000Z")
            except Exception:
                pass

        try:
            created = svc.tasks().insert(tasklist=_DEFAULT_TASKLIST, body=body).execute()
            # Store the Google Tasks ID back in the local todo
            _set_google_task_id(todo, t["id"], created["id"])
            stats["pushed"] += 1
        except Exception:
            pass

    return stats


def complete_in_google_tasks(workspace: Path, google_task_id: str) -> bool:
    """Mark a task completed in Google Tasks."""
    from nanobot.google.auth import GoogleAuth
    from googleapiclient.discovery import build

    auth = GoogleAuth(workspace)
    try:
        creds = auth.get_credentials("work")
    except Exception:
        return False

    svc = build("tasks", "v1", credentials=creds)
    try:
        svc.tasks().patch(
            tasklist=_DEFAULT_TASKLIST,
            task=google_task_id,
            body={"status": "completed"},
        ).execute()
        return True
    except Exception:
        return False


def _set_google_task_id(todo: Any, local_id: str, google_task_id: str) -> None:
    """Patch google_task_id into a local todo item."""
    from nanobot.daily.todo import _today_key
    import json as _json

    path = todo._dir / f"todo_{_today_key()}.json"
    if not path.exists():
        return
    tasks = _json.loads(path.read_text())
    for t in tasks:
        if t["id"] == local_id:
            t["google_task_id"] = google_task_id
            break
    path.write_text(_json.dumps(tasks, indent=2))


# ---------------------------------------------------------------------------
# Google Calendar study blocks
# ---------------------------------------------------------------------------


def schedule_study_blocks(workspace: Path, arrival_time: datetime | None = None) -> dict[str, Any]:
    """Create time-blocked Calendar events for today's pending tasks.

    1. Deletes existing Argon study blocks for today.
    2. Gets free/busy for today.
    3. Creates events in available slots starting from arrival_time (or now).

    Returns {created: int, skipped: int, calendar_id: str}
    """
    from nanobot.daily.todo import DailyTodo
    from nanobot.google.auth import GoogleAuth
    from googleapiclient.discovery import build

    auth = GoogleAuth(workspace)
    try:
        creds = auth.get_credentials("work")
    except Exception:
        return {"error": "work account not authenticated"}

    svc = build("calendar", "v3", credentials=creds)
    todo = DailyTodo(workspace)
    pending = todo.get_pending()

    if not pending:
        return {"created": 0, "skipped": 0, "message": "No pending tasks."}

    cal_id = "primary"
    today = _now().date()
    day_start = datetime.combine(today, __import__("datetime").time(0, 0), tzinfo=_TZ)
    day_end = datetime.combine(today, __import__("datetime").time(23, 59), tzinfo=_TZ)

    # ── Delete existing study blocks ─────────────────────────────────────────
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

    # ── Get free/busy ────────────────────────────────────────────────────────
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

    # ── Schedule tasks into free slots ───────────────────────────────────────
    cursor = arrival_time or _now()
    # Don't schedule before 2pm on school days (usually still at school)
    if cursor.hour < 14:
        cursor = cursor.replace(hour=14, minute=0, second=0, microsecond=0)

    created = 0
    skipped = 0
    buffer_minutes = 10  # gap between blocks

    for task in pending:
        estimate = task.get("time_estimate_min") or _default_estimate(task)
        if not estimate:
            skipped += 1
            continue

        # Find next free slot of at least `estimate` minutes
        block_start = _find_free_slot(cursor, estimate, busy, day_end)
        if block_start is None:
            skipped += 1
            continue

        block_end = block_start + timedelta(minutes=estimate)
        subject = task.get("subject", "")
        title = f"{_STUDY_BLOCK_TAG} {task['title']}" + (f" ({subject})" if subject else "")

        try:
            event = svc.events().insert(calendarId=cal_id, body={
                "summary": title,
                "start": {"dateTime": block_start.isoformat()},
                "end": {"dateTime": block_end.isoformat()},
                "colorId": "2",  # sage green
                "description": f"Argon-scheduled study block.\nTask ID: {task['id']}",
            }).execute()

            # Store calendar event ID back in todo
            _set_calendar_event_id(todo, task["id"], event["id"])
            created += 1

            # Mark this slot as busy for subsequent tasks
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
    """Find the next free slot of at least duration_min minutes."""
    cursor = after
    while cursor + timedelta(minutes=duration_min) <= deadline:
        block_end = cursor + timedelta(minutes=duration_min)
        conflict = next(
            (b for b in busy if b[0] < block_end and b[1] > cursor),
            None,
        )
        if conflict is None:
            return cursor
        cursor = conflict[1]  # jump past the conflict
    return None


def _default_estimate(task: dict) -> int | None:
    """Rough default estimate when habit data isn't available."""
    priority = task.get("priority", "medium")
    return {"high": 60, "medium": 45, "low": 30}.get(priority)


def _set_calendar_event_id(todo: Any, local_id: str, event_id: str) -> None:
    from nanobot.daily.todo import _today_key
    import json as _json

    path = todo._dir / f"todo_{_today_key()}.json"
    if not path.exists():
        return
    tasks = _json.loads(path.read_text())
    for t in tasks:
        if t["id"] == local_id:
            t["calendar_event_id"] = event_id
            break
    path.write_text(_json.dumps(tasks, indent=2))
