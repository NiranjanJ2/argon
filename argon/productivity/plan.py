"""The day's plan — the schedule Argon's check-ins hang off.

Before this, Argon reached out on a timer: generic windows (morning, evening)
plus an ``idle`` nudge every two hours whenever any task was open. From the
receiving end that is indistinguishable from random, and the natural response
to a message that arrives for no reason is to stop reading them.

So the times are his, not a constant. In the morning Argon asks what the day
looks like; whatever he says becomes blocks, and the blocks *are* the check-in
schedule — one word as each starts, one as it ends, and an offer during any
long stretch he left empty. No plan yet means one job only: ask for one, and
keep asking until there is something to work from.

Blocks are local times on one date, so this file is date-keyed exactly like
``state.json`` and cannot leak into tomorrow.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from argon import clock

#: A gap at least this long is worth offering to fill. Shorter than this and
#: there is no point starting anything, so asking is just noise.
OPEN_STRETCH_MINUTES = 75

#: How long after a block's start or end the moment is still worth marking. A
#: check-in that lands 40 minutes into a session is no longer "starting now".
BLOCK_GRACE_MINUTES = 20

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass
class Block:
    """One stretch of the day he said he would spend on something."""

    id: str
    start: str            # "HH:MM", local
    end: str | None       # "HH:MM", local; None means open-ended
    what: str
    status: str = "pending"   # pending | done | skipped

    def starts_at(self, day: datetime) -> datetime:
        return _at(day, self.start)

    def ends_at(self, day: datetime) -> datetime | None:
        return _at(day, self.end) if self.end else None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "start": self.start, "end": self.end,
                "what": self.what, "status": self.status}


def _at(day: datetime, hhmm: str) -> datetime:
    hour, minute = (int(p) for p in hhmm.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def normalize_time(value: str) -> str | None:
    """Accept "14:00", "2pm", "2:30 PM" — return "HH:MM", or None if unusable.

    The model writes whatever the conversation used, and a plan that silently
    drops a block because it said "2pm" is worse than no plan: he would think
    Argon had it.
    """
    text = str(value or "").strip().lower().replace(".", "")
    if _HHMM.match(text):
        hour, minute = text.split(":")
        return "{:02d}:{}".format(int(hour), minute)
    match = re.match(r"^(\d{1,2})(?::([0-5]\d))?\s*(am|pm)$", text)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), match.group(2) or "00", match.group(3)
    if not 1 <= hour <= 12:
        return None
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    return "{:02d}:{}".format(hour, minute)


@dataclass
class Gap:
    """A stretch he left unclaimed."""

    start: datetime
    end: datetime | None
    minutes: int = field(default=0)


class DayPlan:
    """Today's blocks. Resets at 4 AM with the rest of the day's state."""

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "daily" / "plan.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- storage -----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(data, dict) or data.get("date") != clock.today_key():
            return self._empty()
        data.setdefault("blocks", [])
        data.setdefault("asked_count", 0)
        return data

    def _empty(self) -> dict[str, Any]:
        return {"date": clock.today_key(), "blocks": [], "asked_count": 0,
                "declined": False}

    def _save(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # -- reading -----------------------------------------------------------

    def blocks(self) -> list[Block]:
        out = [Block(**b) for b in self._load()["blocks"]]
        out.sort(key=lambda b: b.start)
        return out

    def exists(self) -> bool:
        """Is there anything to work from?"""
        return bool(self._load()["blocks"])

    def declined(self) -> bool:
        """Has he said today that he does not want to plan? Then stop asking."""
        return bool(self._load().get("declined"))

    def times_asked(self) -> int:
        return int(self._load().get("asked_count", 0))

    # -- writing -----------------------------------------------------------

    def set_blocks(self, blocks: list[dict[str, Any]]) -> list[Block]:
        """Replace the plan. Returns what was stored — unparseable blocks are dropped."""
        stored: list[dict[str, Any]] = []
        for index, raw in enumerate(blocks or []):
            start = normalize_time(raw.get("start", ""))
            if not start:
                continue
            what = str(raw.get("what") or "").strip()
            if not what:
                continue
            stored.append({
                "id": "b{}".format(index),
                "start": start,
                "end": normalize_time(raw.get("end", "")) if raw.get("end") else None,
                "what": what,
                "status": "pending",
            })
        stored.sort(key=lambda b: b["start"])
        # Renumber after sorting so ids follow the order he will see them in.
        for position, block in enumerate(stored):
            block["id"] = "b{}".format(position)

        data = self._load()
        data["blocks"] = stored
        data["declined"] = False
        self._save(data)
        return [Block(**b) for b in stored]

    def mark(self, block_id: str, status: str) -> bool:
        data = self._load()
        for block in data["blocks"]:
            if block["id"] == block_id:
                block["status"] = status
                self._save(data)
                return True
        return False

    def record_asked(self) -> None:
        data = self._load()
        data["asked_count"] = int(data.get("asked_count", 0)) + 1
        self._save(data)

    def decline(self) -> None:
        """He does not want a plan today. Stop asking; that is the whole point."""
        data = self._load()
        data["declined"] = True
        self._save(data)

    # -- the schedule ------------------------------------------------------

    def starting_now(self, now: datetime | None = None) -> Block | None:
        """A pending block whose start has just passed."""
        now = now or clock.now()
        window = timedelta(minutes=BLOCK_GRACE_MINUTES)
        for block in self.blocks():
            if block.status != "pending":
                continue
            begins = block.starts_at(now)
            if begins <= now < begins + window:
                return block
        return None

    def just_ended(self, now: datetime | None = None) -> Block | None:
        """A pending block whose end has just passed — time to ask how it went."""
        now = now or clock.now()
        window = timedelta(minutes=BLOCK_GRACE_MINUTES)
        for block in self.blocks():
            if block.status != "pending":
                continue
            finishes = block.ends_at(now)
            if finishes and finishes <= now < finishes + window:
                return block
        return None

    def open_stretch(self, now: datetime | None = None) -> Gap | None:
        """An unclaimed stretch starting about now, long enough to use.

        This is what turns "nothing scheduled" into a question worth asking:
        not "you have tasks outstanding" on a two-hour timer, but "you have
        until 4 — work or rest?" at the moment the free time actually starts.
        """
        now = now or clock.now()
        upcoming = [b for b in self.blocks() if b.starts_at(now) > now]

        # Inside a block? Then this is not free time.
        for block in self.blocks():
            begins, finishes = block.starts_at(now), block.ends_at(now)
            if begins <= now and (finishes is None or now < finishes):
                return None

        end = upcoming[0].starts_at(now) if upcoming else None
        if end is None:
            # Nothing left today: free until the evening winds down.
            end = now.replace(hour=21, minute=0, second=0, microsecond=0)
            if end <= now:
                return None
        minutes = int((end - now).total_seconds() / 60)
        if minutes < OPEN_STRETCH_MINUTES:
            return None
        return Gap(start=now, end=end, minutes=minutes)

    def as_entries(self) -> list[dict[str, Any]]:
        """Blocks shaped like agenda entries, so scheduled work reads as scheduled.

        A task he has given a slot in the plan is not outstanding work, for
        exactly the same reason a task with a 3 PM reminder is not.
        """
        day = clock.now()
        return [
            {"summary": b.what, "start": b.starts_at(day), "kind": "plan"}
            for b in self.blocks()
            if b.status == "pending"
        ]

    def summary(self, now: datetime | None = None) -> str:
        """The plan as prompt lines, with where he is in it."""
        now = now or clock.now()
        blocks = self.blocks()
        if not blocks:
            return "- no plan yet today"
        lines = []
        for block in blocks:
            begins = block.starts_at(now)
            span = "{:%-I:%M %p}".format(begins)
            if block.end:
                span += "–{:%-I:%M %p}".format(block.ends_at(now))
            marker = {"done": " (done)", "skipped": " (skipped)"}.get(block.status, "")
            if block.status == "pending" and begins <= now:
                marker = " (now)" if not block.end or now < block.ends_at(now) else " (passed)"
            lines.append("- {} {}{}".format(span, block.what, marker))
        return "\n".join(lines)


__all__ = [
    "Block", "DayPlan", "Gap", "OPEN_STRETCH_MINUTES", "BLOCK_GRACE_MINUTES",
    "normalize_time",
]
