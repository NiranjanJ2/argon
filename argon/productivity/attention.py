"""What the phone reports about where his attention went.

iOS will not say which app is in the foreground — `DeviceActivityReport` renders
usage inside a sealed extension whose values no code of ours can read. That is a
privacy boundary, not a gap, so there is no "current app" to store.

What the phone *can* report is a threshold being crossed: a
`DeviceActivityEvent` fires in the monitor extension once a chosen set of apps
has been used for N minutes. Two thresholds give two different facts:

* a one-minute threshold is effectively **"he opened one of these"** — and it is
  better than a true open event would be, because tapping an app and closing it
  five seconds later should not summon anything;
* larger thresholds are **"he has spent N minutes on these today"**.

Both arrive after the fact and neither can be polled, so this module stores what
was reported rather than answering questions about now. `since_minutes` exists
because a threshold crossed at four o'clock says nothing about nine.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from argon import clock
from argon.core import store

_DOC = "attention"

#: Kept per day, and only today's is ever read. A week is plenty to look back on
#: without the document growing without bound.
KEEP_DAYS = 7


def _blank() -> dict[str, Any]:
    return {"days": {}}


def record(kind: str, *, label: str = "", minutes: int = 0) -> dict[str, Any]:
    """Store one reported threshold.

    ``kind`` is ``opened`` (the low threshold) or ``spent`` (a budget crossed).
    ``label`` is whatever the phone chose to call the group — the app's own name
    for the selection, never an app identity, which iOS does not expose.
    """
    now = clock.now()
    row = {
        "kind": str(kind),
        "label": str(label)[:60],
        "minutes": max(0, int(minutes or 0)),
        "at": now.isoformat(),
    }
    day = clock.today_key()
    with store.edit_doc(_DOC, _blank()) as doc:
        days = doc.setdefault("days", {})
        days.setdefault(day, []).append(row)
        for old in sorted(days)[:-KEEP_DAYS]:
            days.pop(old, None)
    return row


def today() -> list[dict[str, Any]]:
    days = store.get_doc(_DOC, _blank()).get("days", {})
    return list(days.get(clock.today_key(), []))


def since_minutes(minutes: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Reports from the last *minutes*. A crossing at four says nothing at nine."""
    now = now or clock.now()
    cutoff = now - timedelta(minutes=minutes)
    out = []
    for row in today():
        try:
            when = datetime.fromisoformat(row["at"])
        except (KeyError, ValueError):
            continue
        if when >= cutoff:
            out.append(row)
    return out


def minutes_spent_today(label: str = "") -> int:
    """The largest 'spent' threshold reported today.

    Thresholds are cumulative and fire in order, so the largest one crossed is
    the running total — summing them would count the first fifteen minutes four
    times over.
    """
    best = 0
    for row in today():
        if row["kind"] != "spent":
            continue
        if label and row["label"] != label:
            continue
        best = max(best, row["minutes"])
    return best


def describe() -> str:
    """One line for a prompt, or empty when the phone has reported nothing."""
    spent = minutes_spent_today()
    recent = [r for r in since_minutes(30) if r["kind"] == "opened"]
    parts = []
    if spent:
        parts.append(f"{spent}+ minutes on distracting apps today")
    if recent:
        names = sorted({r["label"] for r in recent if r["label"]}) or ["a distracting app"]
        parts.append("just opened " + ", ".join(names))
    return "; ".join(parts)
