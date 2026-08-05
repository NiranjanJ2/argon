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


def upcoming(workspace: Path) -> list[dict[str, Any]]:
    """Everything still ahead today — calendar events and scheduled reminders."""
    merged = [{**e, "kind": e.get("kind", "event")} for e in today(workspace)]
    merged.extend(reminders())
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
