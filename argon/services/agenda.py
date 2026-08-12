"""Today's remaining calendar events, cached, for anything that runs on a timer.

Argon could read the calendar — ``get_daily_overview`` has always been able to —
but nothing ever *looked* unprompted. The check-in gate decided whether to reach
out from mode, tasks and the clock alone, and its prompt never mentioned the
calendar, so an event Niranjan booked in chat at noon produced silence at 6:45.
The single most useful thing an assistant can say is "you have X in fifteen
minutes", and Argon could not say it.

The gate ticks every ten minutes, so this caches: one Google round-trip per TTL,
and any failure degrades to "no events" rather than muting or crashing the tick.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from argon import clock

#: One fetch per gate tick at most. Events rarely move inside ten minutes.
AGENDA_TTL_S = 540.0

#: How far ahead counts as "starting soon" — enough warning to stop what you
#: are doing and go, not so much that it is forgotten by the time it matters.
SOON_MINUTES = 15

_cache: tuple[float, list[dict[str, Any]]] | None = None
_lock = threading.Lock()


def _parse(stamp: dict[str, Any] | None) -> datetime | None:
    """A Google event start/end block to an aware datetime, or None.

    All-day events carry ``date`` instead of ``dateTime``; they have no start
    time to warn about, so they are skipped rather than pinned to midnight.
    """
    if not stamp or not stamp.get("dateTime"):
        return None
    try:
        moment = datetime.fromisoformat(stamp["dateTime"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=clock.tz())
    return moment.astimezone(clock.tz())


def _fetch(workspace: Path) -> list[dict[str, Any]]:
    from argon.google.service import build_google_service

    svc = build_google_service(workspace, "calendar", "v3", "work")
    now = clock.now()
    items = svc.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=now.replace(hour=23, minute=59, second=59).isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=20,
    ).execute().get("items", [])

    events = []
    for item in items:
        start = _parse(item.get("start"))
        if start is None:
            continue
        events.append({
            "id": item.get("id"),
            "summary": item.get("summary") or "(untitled)",
            "start": start,
            "end": _parse(item.get("end")),
            "location": item.get("location"),
            # An event Argon wrote from a cron reminder. The job delivers its
            # own message at its own time, so this must never earn an early
            # warning — true even after the job has fired and deleted itself.
            "kind": "reminder" if MIRROR_TAG in (item.get("description") or "") else "event",
        })
    return events


def today(workspace: Path, *, fresh: bool = False) -> list[dict[str, Any]]:
    """Events still to come today, soonest first. Empty on any failure."""
    global _cache
    with _lock:
        if _cache and not fresh and (time.monotonic() - _cache[0]) < AGENDA_TTL_S:
            cached = _cache[1]
        else:
            try:
                cached = _fetch(workspace)
                _cache = (time.monotonic(), cached)
            except Exception as exc:  # noqa: BLE001 — a calendar outage must not mute Argon
                logger.warning("Agenda unavailable: {}", exc)
                cached = _cache[1] if _cache else []

    now = clock.now()
    return [e for e in cached if e["start"] >= now - timedelta(minutes=1)]


def starting_soon(
    workspace: Path,
    *,
    within_minutes: int = SOON_MINUTES,
    ignore: Callable[[str], bool] | None = None,
) -> dict[str, Any] | None:
    """The next unignored event inside the warning window, or None.

    ``ignore`` skips events rather than stopping at them. Returning None on the
    first already-announced event would mean a 6:55 meeting permanently masked
    a 7:00 one — the caller would never learn the second existed.
    """
    horizon = clock.now() + timedelta(minutes=within_minutes)
    for event in today(workspace):
        if event["start"] > horizon:
            break  # sorted by start, so nothing later can qualify
        if event.get("kind") == "reminder":
            continue  # a cron job will deliver this one on its own
        if ignore is not None and ignore(event.get("id") or ""):
            continue
        return event
    return None


def reminders() -> list[dict[str, Any]]:
    """One-off cron jobs still due today, shaped like calendar events.

    Told "remind me to start UCLA work at 7", the model reaches for ``cron``,
    not ``create_calendar_event`` — Niranjan prefers one-time cron jobs and
    ``MEMORY.md`` says so. Those are just as much "things he scheduled today",
    and a "coming up" list that omitted them would be a lie by omission.

    They are shown but never trigger an ``upcoming`` check-in: a cron job
    delivers its own message at its own time, so warning about it fifteen
    minutes early would say the same thing twice.
    """
    from argon.paths import get_cron_store

    try:
        import json

        data = json.loads(get_cron_store().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []

    now = clock.now()
    end = now.replace(hour=23, minute=59, second=59)
    out = []
    for job in data.get("jobs") or []:
        at_ms = ((job.get("schedule") or {}).get("atMs")) or 0
        if not job.get("enabled") or not at_ms:
            continue
        start = datetime.fromtimestamp(at_ms / 1000, tz=clock.tz())
        if not (now - timedelta(minutes=1) <= start <= end):
            continue
        out.append({
            "id": "cron:" + str(job.get("id")),
            "summary": (job.get("payload") or {}).get("message") or job.get("name") or "Reminder",
            "start": start,
            "end": None,
            "location": None,
            "kind": "reminder",
        })
    return out


#: Marks a calendar event Argon created from a cron reminder, so the two can be
#: recognised as one thing. Kept out of the summary — he reads these.
MIRROR_TAG = "argon:reminder"


def put_on_calendar(summary: str, at_ms: int, *, minutes: int = 15) -> str:
    """Write a one-off reminder to the work calendar. Returns the event id."""
    from argon.google.service import build_google_service
    from argon.paths import argon_home

    start = datetime.fromtimestamp(at_ms / 1000, tz=clock.tz())
    svc = build_google_service(argon_home(), "calendar", "v3", "work")
    event = svc.events().insert(calendarId="primary", body={
        "summary": summary,
        "description": MIRROR_TAG,
        "start": {"dateTime": _stamp(start), "timeZone": str(clock.tz())},
        "end": {"dateTime": _stamp(start + timedelta(minutes=minutes)),
                "timeZone": str(clock.tz())},
    }).execute()
    _invalidate()
    logger.info("Put '{}' on the calendar at {:%-I:%M %p}", summary, start)
    return event.get("id", "")


def remove_from_calendar(summary: str, at_ms: int) -> bool:
    """Delete the mirrored event for a reminder that was cancelled.

    ``cron add`` wrote an event and ``cron remove`` did not delete one, so
    cancelling "Lock in for school day" removed the job and left the event on
    his calendar — where it then turned up in his week summary as a commitment
    he had explicitly called off. A mirror needs both halves.

    Only ever deletes events Argon created: matched on the MIRROR_TAG it writes
    into the description, never on the summary alone.
    """
    from argon.google.service import build_google_service
    from argon.paths import argon_home

    start = datetime.fromtimestamp(at_ms / 1000, tz=clock.tz())
    svc = build_google_service(argon_home(), "calendar", "v3", "work")
    items = svc.events().list(
        calendarId="primary",
        timeMin=_stamp(start - timedelta(minutes=1)),
        timeMax=_stamp(start + timedelta(minutes=1)),
        singleEvents=True,
        maxResults=10,
    ).execute().get("items", [])

    for item in items:
        if MIRROR_TAG not in (item.get("description") or ""):
            continue  # his own event that happens to sit at this minute
        if (item.get("summary") or "") != summary:
            continue
        svc.events().delete(calendarId="primary", eventId=item["id"]).execute()
        _invalidate()
        logger.info("Removed '{}' from the calendar", summary)
        return True
    return False


def _stamp(moment: datetime) -> str:
    """Second precision — Swift's ISO8601 parser rejects six fractional digits."""
    return moment.replace(microsecond=0).isoformat()


def _invalidate() -> None:
    """Drop the cache so a just-created event shows up immediately."""
    global _cache
    with _lock:
        _cache = None


def upcoming(workspace: Path) -> list[dict[str, Any]]:
    """Everything still ahead today — calendar events and scheduled reminders.

    A reminder Argon put on the calendar exists twice: as a cron job that will
    fire, and as the event it wrote. They are one commitment, so the cron entry
    wins — it is the one that carries "do not warn early", since the job
    delivers its own message at its own time.
    """
    scheduled = reminders()
    taken = {(e["start"].replace(second=0, microsecond=0), e["summary"]) for e in scheduled}

    merged = [
        {**e, "kind": e.get("kind", "event")}
        for e in today(workspace)
        if (e["start"].replace(second=0, microsecond=0), e["summary"]) not in taken
    ]
    merged.extend(scheduled)
    merged.sort(key=lambda e: e["start"])
    return merged


def describe(event: dict[str, Any]) -> str:
    """One line naming an event and when it starts, for a prompt."""
    # Rounded, not truncated: a meeting 11 minutes and 59 seconds out is "in 12
    # min" to a human, and reading "in 11 min" off a 7:00 event at 6:48 makes
    # Argon look like it cannot do arithmetic.
    minutes = round((event["start"] - clock.now()).total_seconds() / 60)
    when = (
        "now" if minutes <= 0
        else "in {} min".format(minutes) if minutes < 60
        else "at {:%-I:%M %p}".format(event["start"])
    )
    where = " ({})".format(event["location"]) if event.get("location") else ""
    return "{} — {}{}".format(event["summary"], when, where)


#: Classroom is slower and changes less often than the calendar; one fetch per
#: half hour is plenty for a readout that only really matters once a day.
SCHOOLWORK_TTL_S = 1800.0

_schoolwork: tuple[float, list[dict[str, Any]]] | None = None
_schoolwork_lock = threading.Lock()


def _fetch_schoolwork(workspace: Path, days_ahead: int) -> list[dict[str, Any]]:
    from argon.google.classroom import upcoming_assignments
    from argon.google.service import build_google_service
    from argon.utils.helpers import when_label

    svc = build_google_service(workspace, "classroom", "v1", "school")
    assignments, unreadable = upcoming_assignments(svc, days_ahead=days_ahead)
    if unreadable:
        logger.warning("Classroom courses unreadable: {}", unreadable)

    out = []
    for item in assignments:
        due = item.get("due")
        try:
            when = datetime.fromisoformat(str(due)) if due else None
        except (TypeError, ValueError):
            when = None
        out.append({
            "title": item.get("title") or "(untitled)",
            "course": item.get("course_name") or "",
            "due": due,
            "due_when": when_label(due),
            "days_left": (when.date() - clock.now().date()).days if when else None,
        })
    out.sort(key=lambda a: a["due"] or "9999")
    return _one_per_thing(out)


def _one_per_thing(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the same assignment posted twice in one course.

    His Math Analysis work appears as both "Math Analysis Summer Assignment"
    and "Math An Summer Assignment", same course, same 8 PM deadline — one
    thing his teacher posted twice. Reading both back makes the brief look
    careless, and the brief is meant to be the message he trusts.

    Titles are compared loosely — "Math An" against "Math Analysis" — which is
    only safe because the course and the deadline have already had to match
    exactly. Two genuinely different assignments in one course, due the same
    minute, sharing most of their title words, is not a real case.
    """
    kept: list[dict[str, Any]] = []
    for item in items:
        twin = next(
            (k for k in kept
             if k["course"] == item["course"] and k["due"] == item["due"]
             and _similar_title(k["title"], item["title"])),
            None,
        )
        if twin is None:
            kept.append(item)
        elif len(item["title"]) > len(twin["title"]):
            kept[kept.index(twin)] = item  # the fuller title reads better
    return kept


def _similar_title(a: str, b: str) -> bool:
    import re as _re

    first = {w for w in _re.findall(r"[a-z0-9]+", a.lower())}
    second = {w for w in _re.findall(r"[a-z0-9]+", b.lower())}
    if not first or not second:
        return False
    return len(first & second) / len(first | second) >= 0.6


def schoolwork(
    workspace: Path, *, days_ahead: int = 10, fresh: bool = False
) -> list[dict[str, Any]]:
    """Classroom assignments due soon. Empty on any failure.

    Argon has always been able to read Classroom, and never did so unprompted:
    the check-in prompt suggested calling a tool and the model mostly did not
    bother. He gets home at four and that is the moment the homework matters,
    so the brief fetches it rather than hoping.
    """
    global _schoolwork
    with _schoolwork_lock:
        cached = _schoolwork
        if cached and not fresh and (time.monotonic() - cached[0]) < SCHOOLWORK_TTL_S:
            return cached[1]
        try:
            found = _fetch_schoolwork(workspace, days_ahead)
        except Exception as exc:  # noqa: BLE001 — school auth must not mute the brief
            logger.warning("Classroom unavailable: {}", exc)
            return cached[1] if cached else []
        _schoolwork = (time.monotonic(), found)
    return found


def describe_assignment(item: dict[str, Any]) -> str:
    """One line for an assignment, with how much runway is left."""
    days = item.get("days_left")
    if days is None:
        runway = ""
    elif days <= 0:
        runway = " — due today"
    elif days == 1:
        runway = " — due tomorrow"
    else:
        runway = " — {} days".format(days)
    course = " ({})".format(item["course"]) if item.get("course") else ""
    return "{}{}{}{}".format(
        item.get("title", "?"), course,
        " {}".format(item["due_when"]) if item.get("due_when") else "", runway,
    )
