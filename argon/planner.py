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
from datetime import datetime, time
from typing import Any

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
        "suggestions": suggestions,
    }


def summarise(result: dict[str, Any]) -> str:
    """One line for the daily log, so the day page records what he decided."""
    bits = []
    for label, key in (("done", "completed"), ("carried", "carried"), ("added", "added")):
        if result.get(key):
            bits.append(f"{len(result[key])} {label}")
    return "Planned the afternoon: " + (", ".join(bits) if bits else "nothing to change")
