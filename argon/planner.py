"""The afternoon planning moment.

Argon's weakness was an assumption: anything overdue was still outstanding. It
had no way to learn otherwise, so a thing he finished on paper stayed on the
board for days, got counted in every brief, and was asked about again each
evening. Four days of "SAT reading study is overdue" is how a board stops being
believed.

This is the mechanism for asking instead of assuming. Once a day, after school,
he is shown what is carried, what is claimed overdue, and what a class is known
to assign offline — and he answers it in one pass rather than being nagged item
by item.

Deliberately once per day and only after ``OPENS_AFTER``. Twice would be a
nag; before school lets out he does not yet know the answer.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from typing import Any

from loguru import logger

from argon import clock
from argon.core import store

_DOC = "planner"

#: AP Lang posts the day's work as a Material at about 3:36, and school is out.
#: Before this he cannot answer "did Chem assign anything today".
OPENS_AFTER = time(15, 36)

#: What AP Chem costs when it is assigned. He gets it in class every other day
#: and it is never on Classroom, so the only way it reaches the board is here.
CHEM_MINUTES = 60
CHEM_TITLE = "AP Chem homework"



#: Pulls the homework out of an AP Lang daily post. The teacher's format is
#: stable: an "HW:" line, then numbered items, and "None :)" for a free night.
_HW_BLOCK = re.compile(r"\bHW\s*:?\s*\n?(.+)", re.IGNORECASE | re.DOTALL)
_HW_ITEM = re.compile(r"^\s*\d+[.)]\s*(.+?)\s*$", re.MULTILINE)


def _state() -> dict[str, Any]:
    return store.get_doc(_DOC, {"last_planned": None})


def last_planned() -> str | None:
    return _state().get("last_planned")


def mark_planned(day: str | None = None) -> str:
    day = day or clock.today_key()
    with store.edit_doc(_DOC, {"last_planned": None}) as doc:
        doc["last_planned"] = day
    return day


def is_due(now: datetime | None = None) -> bool:
    """Should the planner open? Once a day, after school, not before."""
    now = now or clock.now()
    if now.time() < OPENS_AFTER:
        return False
    return last_planned() != now.strftime("%Y-%m-%d")


def lang_homework(posts: list[dict[str, Any]], today: str | None = None) -> list[str]:
    """Homework lines from today's AP Lang post, or [] if there is none.

    "None :)" is a real answer and must come back empty rather than as an item
    called "None" — the whole point of reading the post is to know which it is.
    """
    today = today or clock.today_key()
    for post in posts:
        if not str(post.get("posted_at", "")).startswith(today):
            continue
        match = _HW_BLOCK.search(post.get("text") or "")
        if not match:
            continue
        items = []
        for line in _HW_ITEM.findall(match.group(1)):
            cleaned = line.strip(" .")
            if not cleaned or cleaned.lower().startswith("none"):
                continue
            items.append(cleaned)
        return items
    return []


def build(board_rows: list[dict[str, Any]], lang_posts: list[dict[str, Any]] | None = None,
          now: datetime | None = None) -> dict[str, Any]:
    """Everything the planning screen needs to render."""
    now = now or clock.now()
    today_key = now.strftime("%Y-%m-%d")
    start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    overdue: list[dict[str, Any]] = []
    today: list[dict[str, Any]] = []
    for row in board_rows:
        if row.get("done"):
            continue
        due = str(row.get("due") or "")[:10]
        entry = {
            "id": row.get("id"),
            "title": row.get("title"),
            "subject": row.get("subject") or row.get("course") or "",
            "due": due or None,
            "source": row.get("source"),
        }
        if due and due < today_key:
            try:
                entry["days_overdue"] = (
                    start_of_today - datetime.fromisoformat(due).replace(tzinfo=now.tzinfo)
                ).days
            except ValueError:
                entry["days_overdue"] = None
            overdue.append(entry)
        elif due == today_key:
            today.append(entry)

    # Everything else still on the list: not overdue, not due today. Work he
    # could pull forward.
    #
    # This was a horizon at first — only things further out than a fortnight —
    # which was an invented rule and wrong. Nearly everything on the list sits
    # a day or three out, so the list was empty every time. "What do you want
    # to work on today" is asked of exactly the work that is not already
    # forced, and a thing due Friday is the main candidate on a Wednesday.
    long_term: list[dict[str, Any]] = []
    for row in board_rows:
        if row.get("done"):
            continue
        due = str(row.get("due") or "")[:10]
        if due and due <= today_key:
            continue  # overdue or due today; both are handled above
        # An undated Classroom item is almost never work. Teachers post notices
        # as coursework, and those have no date at all.
        if not due and row.get("source") == "classroom":
            continue
        long_term.append({
            "id": row.get("id"),
            "title": row.get("title"),
            "subject": row.get("subject") or row.get("course") or "",
            "due": due or None,
        })

    suggestions: list[dict[str, Any]] = [
        {
            "kind": "chem",
            "title": CHEM_TITLE,
            "estimate_min": CHEM_MINUTES,
            "prompt": "Did AP Chem assign anything today?",
            # Never pre-ticked. Chem is invisible to every source Argon has, so
            # a default of "yes" would be inventing work and a default of "no"
            # would be asserting a free night. He is the only one who knows.
            "default": False,
        }
    ]
    for line in lang_homework(lang_posts or [], today_key):
        suggestions.append({
            "kind": "lang",
            "title": line,
            "subject": "AP English Lang",
            "prompt": "From today's AP Lang post",
            "default": True,
        })

    return {
        "needed": is_due(now),
        "opens_after": OPENS_AFTER.strftime("%H:%M"),
        "last_planned": last_planned(),
        "today_key": today_key,
        "overdue": overdue,
        "today": today,
        "long_term": long_term,
        "suggestions": suggestions,
    }


def summarise(result: dict[str, Any]) -> str:
    """One line for the daily log, so the day page records what he decided."""
    bits = []
    for label, key in (("done", "completed"), ("carried", "carried"), ("added", "added")):
        if result.get(key):
            bits.append(f"{len(result[key])} {label}")
    return "Planned the afternoon: " + (", ".join(bits) if bits else "nothing to change")


# ---------------------------------------------------------------------------
# The start time, and the two things that hang off it
# ---------------------------------------------------------------------------

#: How long before the start he gets told. Long enough to finish what he is
#: doing, short enough that he has not forgotten by the time it lands.
WARNING_MINUTES = 30

#: Cron job names. Prefixed so the runtime can recognise them and act
#: deterministically instead of putting the model in the loop — a notification
#: and a Screen Time block are not things a turn should be free to reinterpret.
JOB_PREFIX = "planner"
NOTIFY_JOB = f"{JOB_PREFIX}:notify"
BLOCK_JOB = f"{JOB_PREFIX}:block"

#: The scheduled block is a holding pattern, not the session. It lasts until he
#: actually starts something, at which point start_task replaces it with a
#: task-tagged one that lifts when he marks the work done. This ceiling only
#: matters if he never starts at all — an evening that blocks forever because
#: he went out is the failure worth avoiding.
BLOCK_WINDOW_MIN = 90

#: Tagged so start_task's own block cleanly supersedes it, and so finishing a
#: task never clears a block he did not raise from a task.
BLOCK_SOURCE = "planner"


def start_time() -> str | None:
    """Today's planned start, ``HH:MM``, or None if he has not chosen one."""
    state = _state()
    if state.get("start_for") != clock.today_key():
        return None
    return state.get("start_at")


def set_start_time(hhmm: str | None, day: str | None = None) -> str | None:
    """Record when he means to begin. ``None`` clears it."""
    day = day or clock.today_key()
    with store.edit_doc(_DOC, {"last_planned": None}) as doc:
        doc["start_at"] = hhmm
        doc["start_for"] = day if hhmm else None
    return hhmm


def start_datetime(hhmm: str | None = None, now: datetime | None = None) -> datetime | None:
    """Today's start as an absolute moment, or None if it has already passed."""
    now = now or clock.now()
    hhmm = hhmm or start_time()
    if not hhmm:
        return None
    try:
        hour, minute = (int(part) for part in hhmm.split(":"))
    except (ValueError, TypeError):
        return None
    when = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return when if when > now else None


def schedule_start(cron: Any, hhmm: str | None, now: datetime | None = None) -> dict[str, Any]:
    """Arm the warning and the block for *hhmm*, replacing any earlier pair.

    Cron sleeps until the next job rather than polling, so these fire on the
    minute and survive a restart — which matters, because a block that starts
    ten minutes late is not a start time, and one that never starts because the
    service bounced is worse.
    """
    removed = 0
    # list_jobs(), not a .jobs attribute — CronService has no such attribute, so
    # getattr(cron, "jobs", []) quietly returned nothing and every earlier pair
    # stayed armed. Moving 5pm to 6pm blocked the phone at both.
    for job in list(cron.list_jobs(include_disabled=True)):
        if str(job.name).startswith(JOB_PREFIX):
            try:
                cron.remove_job(job.id)
                removed += 1
            except Exception as exc:  # noqa: BLE001 — a stale job is not fatal
                logger.warning("Could not clear planner job {}: {}", job.id, exc)

    set_start_time(hhmm)
    if not hhmm:
        return {"start_at": None, "scheduled": [], "cleared": removed}

    begins = start_datetime(hhmm, now=now)
    if begins is None:
        # Already past. The time is still recorded — it is what he intended —
        # but arming a job for a moment that has gone would fire immediately.
        return {"start_at": hhmm, "scheduled": [], "cleared": removed, "note": "already passed"}

    from argon.services.cron import CronSchedule

    scheduled = []
    warn_at = begins - timedelta(minutes=WARNING_MINUTES)
    if warn_at > (now or clock.now()):
        cron.add_job(
            name=NOTIFY_JOB,
            schedule=CronSchedule(kind="at", at_ms=int(warn_at.timestamp() * 1000)),
            message=hhmm,
            kind="system_event",
            delete_after_run=True,
        )
        scheduled.append({"job": NOTIFY_JOB, "at": warn_at.isoformat()})

    cron.add_job(
        name=BLOCK_JOB,
        schedule=CronSchedule(kind="at", at_ms=int(begins.timestamp() * 1000)),
        message=hhmm,
        kind="system_event",
        delete_after_run=True,
    )
    scheduled.append({"job": BLOCK_JOB, "at": begins.isoformat()})
    return {"start_at": hhmm, "scheduled": scheduled, "cleared": removed}
