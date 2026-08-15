"""Explicit blocks Niranjan gave for one day.

The plan is optional. Argon records it only when Niranjan supplies one; it does
not derive blocks from calendars, tasks, habits, or an unanswered planning
prompt. Pending blocks may produce a reminder when they start.

Blocks are local times on one date, so this document is date-keyed exactly like
``daily_state`` and cannot leak into tomorrow.

**Identity is stable.** Block ids used to be positions — ``b0``, ``b1`` — handed
out fresh on every write, after sorting. Two things followed from that, and both
happened. Every edit had to resend the whole plan, because the only way to change
one block was to replace all of them, so one block omitted from the model's
rewrite disappeared from his day without anything saying so. And inserting an
earlier block renumbered everything after it: a reminder already announced for
``b1`` now suppressed a *different* block, while the block that had been
announced came back as ``b2`` and was announced a second time. An id is now a
uuid4 prefix minted once, and it survives a move, a retime and a rewording — so
a delta edit is possible at all, and so a reminder can dedupe on something that
means one thing.

Storage is :mod:`argon.core.store` under the key ``day_plan``. The old
``plan.json`` was rewritten in place with no atomic replace, and every reader
caught ``JSONDecodeError`` and returned an empty plan — so a torn write did not
surface as a fault, it surfaced as a day with nothing in it.
:class:`argon.core.store.StoreCorrupt` is deliberately not caught here.
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from argon import clock
from argon.core import store

#: How long after a block's start the moment is still worth marking. A
#: check-in that lands 40 minutes into a session is no longer "starting now".
BLOCK_GRACE_MINUTES = 20

#: Where the plan lives in the operational store.
PLAN_DOC = "day_plan"

#: The only statuses a block may hold.
STATUSES = ("pending", "done", "skipped")

_HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

#: What an id looked like before ids were stable.
_POSITIONAL_ID = re.compile(r"^b\d+$")

#: Namespace for upgrading a positional id. Deriving it rather than minting a
#: random one means two reads of an un-upgraded document agree with each other
#: and with the value that gets persisted on the next write — otherwise the
#: upgrade itself would re-arm every reminder on every read.
_LEGACY_NS = uuid.UUID("a71d0000-0000-4000-8000-000000000000")

#: "leave this field alone", so ``end=None`` can mean "make it open-ended".
_KEEP: Any = object()


@dataclass
class Block:
    """One stretch of the day he said he would spend on something."""

    id: str
    start: str            # "HH:MM", local
    end: str | None       # "HH:MM", local; None means open-ended
    what: str
    status: str = "pending"

    def starts_at(self, day: datetime) -> datetime:
        return _at(day, self.start)

    def ends_at(self, day: datetime) -> datetime | None:
        return _at(day, self.end) if self.end else None

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "start": self.start, "end": self.end,
                "what": self.what, "status": self.status}

    def reminder_key(self) -> str:
        """What a reminder must dedupe on. Format: ``start:<block id>:<HH:MM>``.

        Two failure modes have to be told apart, and only the pair does it.

        The id alone was wrong because ids were positions, so inserting a 3 PM
        block shifted the 5 PM one and the key that had already been announced
        now pointed at something else. Stable ids fix that half.

        The start time alone would be wrong because two blocks can begin at the
        same minute, and because moving a block would then silently inherit
        another's "already said".

        Including the start on top of a stable id gives the one behaviour he
        actually wants from a retime: he moves SAT prep from 2 PM to 5 PM, the
        key changes, and Argon marks the new start — while every other block
        keeps the key it was announced under and stays quiet.

        Consumed by ``argon/services/reminder.py``; the string is written into
        the check-in ledger's ``announced`` list, so changing this format
        re-announces everything once.
        """
        return "start:{}:{}".format(self.id, self.start)


def _at(day: datetime, hhmm: str) -> datetime:
    hour, minute = (int(p) for p in hhmm.split(":"))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _fresh_id(taken: set[str] | frozenset[str] = frozenset()) -> str:
    """A new stable block id. Short, because he reads these back to Argon.

    A candidate that *looks* positional is rejected. One uuid4 prefix in ~270
    is "b" followed by seven digits — "b1234567" — which ``_POSITIONAL_ID``
    matches, so the next read would "upgrade" a perfectly good id to a
    different one. Identity would change underneath a block that nobody
    touched, and its reminder would fire a second time under the new key.
    """
    while True:
        candidate = uuid.uuid4().hex[:8]
        if candidate not in taken and not _POSITIONAL_ID.match(candidate):
            return candidate


def _upgraded_id(old: str, start: str, what: str) -> str:
    """A stable id for a block stored before ids were stable.

    Derived, not random: the same legacy document must upgrade to the same ids
    every time it is read, or a reminder already announced would fire again.
    """
    return uuid.uuid5(_LEGACY_NS, "{}|{}|{}".format(old, start, what)).hex[:8]


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


def _clean(raw: Any) -> dict[str, Any] | None:
    """One stored block, or None if it is not usable.

    This is also where a positional id becomes a stable one, so every read of a
    legacy document agrees and the next write persists the upgrade.
    """
    if not isinstance(raw, dict):
        return None
    start = normalize_time(raw.get("start", ""))
    what = str(raw.get("what") or "").strip()
    if not start or not what:
        return None
    block_id = str(raw.get("id") or "")
    if not block_id or _POSITIONAL_ID.match(block_id):
        block_id = _upgraded_id(block_id, start, what)
    end = raw.get("end")
    status = raw.get("status")
    return {
        "id": block_id,
        "start": start,
        "end": normalize_time(end) if end else None,
        "what": what,
        "status": status if status in STATUSES else "pending",
    }


class DayPlan:
    """Today's blocks. Resets at 4 AM with the rest of the day's state.

    Writes are deltas — :meth:`add_block`, :meth:`update_block`,
    :meth:`remove_block`, :meth:`mark`. :meth:`set_blocks` replaces the whole
    day and is only for an explicit restatement of it.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    # -- storage -----------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        """Today's plan document. Never invents an empty day out of a failure."""
        data = store.get_doc(PLAN_DOC)
        today = clock.today_key()
        if not isinstance(data, dict) or data.get("date") != today:
            return {"date": today, "blocks": []}
        raw = data.get("blocks")
        cleaned = [_clean(b) for b in raw] if isinstance(raw, list) else []
        return {"date": today, "blocks": [b for b in cleaned if b]}

    @contextmanager
    def _edit(self) -> Iterator[dict[str, Any]]:
        """Read-modify-write today's plan in one transaction.

        Every write goes through here, so every write normalises the document:
        yesterday's plan is dropped and positional ids are upgraded before the
        caller's change lands on top.
        """
        today = clock.today_key()
        with store.edit_doc(PLAN_DOC, {"date": today, "blocks": []}) as data:
            if not isinstance(data, dict):
                raise store.StoreCorrupt("day_plan does not hold a plan document")
            raw = data.get("blocks") if data.get("date") == today else []
            cleaned = [_clean(b) for b in raw] if isinstance(raw, list) else []
            # Rebuilt rather than patched, so a document still carrying the dead
            # planner loop's keys (`asked_count`, `declined`, `seeded`) loses
            # them on the next write instead of keeping them alive forever.
            data.clear()
            data["date"] = today
            data["blocks"] = [b for b in cleaned if b]
            yield data
            data["blocks"].sort(key=lambda b: b["start"])
            snapshot = [dict(b) for b in data["blocks"]]
        self._archive(today, snapshot)

    def _archive(self, day: str, blocks: list[dict[str, Any]]) -> None:
        """A dated copy, because the live document is replaced when the day rolls.

        Without it "same as yesterday" has nothing to copy and no pattern can
        ever be learned — every day started from zero because every previous day
        had been thrown away.
        """
        try:
            from argon.productivity.history import PlanHistory

            PlanHistory(self.workspace).record(day, blocks)
        except Exception:  # noqa: BLE001 — archiving must never break planning
            pass

    # -- reading -----------------------------------------------------------

    def blocks(self) -> list[Block]:
        out = [Block(**b) for b in self._load()["blocks"]]
        out.sort(key=lambda b: b.start)
        return out

    def exists(self) -> bool:
        """Is there anything to work from?"""
        return bool(self._load()["blocks"])

    def get(self, block_id: str) -> Block | None:
        """One block by its stable id."""
        return next((b for b in self.blocks() if b.id == block_id), None)

    # -- writing -----------------------------------------------------------

    def set_blocks(self, blocks: list[dict[str, Any]]) -> list[Block]:
        """Replace the whole plan. Returns what was stored; unusable blocks drop.

        This is the explicit-replace path — he restated the whole day, or
        cleared it. Everything gets a new id and a pending status, because these
        are new blocks. For a single change use the delta methods; making an
        edit go through here is what lost blocks the model forgot to resend.
        """
        stored: list[dict[str, Any]] = []
        for raw in blocks or []:
            start = normalize_time(raw.get("start", ""))
            what = str(raw.get("what") or "").strip()
            if not start or not what:
                continue
            stored.append({
                "id": _fresh_id({b["id"] for b in stored}),
                "start": start,
                "end": normalize_time(raw.get("end", "")) if raw.get("end") else None,
                "what": what,
                "status": "pending",
            })
        stored.sort(key=lambda b: b["start"])
        with self._edit() as data:
            data["blocks"] = stored
        return [Block(**b) for b in stored]

    def add_block(self, start: str, what: str, end: str | None = None) -> Block | None:
        """Add one block and leave the rest of the day alone.

        Returns the stored block, or None when the time or the description is
        unusable — a block silently mis-timed is worse than one he can see is
        missing.
        """
        when = normalize_time(start)
        text = str(what or "").strip()
        if not when or not text:
            return None
        record = {
            "id": "",
            "start": when,
            "end": normalize_time(end) if end else None,
            "what": text,
            "status": "pending",
        }
        with self._edit() as data:
            record["id"] = _fresh_id({b["id"] for b in data["blocks"]})
            data["blocks"].append(record)
        return Block(**record)

    def update_block(
        self,
        block_id: str,
        *,
        start: str | None = None,
        end: str | None | Any = _KEEP,
        what: str | None = None,
    ) -> Block | None:
        """Retime or reword one block, keeping its id and its status.

        A move is a retime: pass ``start`` (and ``end``). ``end=None`` makes the
        block open-ended; omitting ``end`` leaves it as it was. Returns the
        block as stored, or None if there is no block with that id. An unusable
        time leaves that field alone rather than guessing at one.
        """
        found: dict[str, Any] | None = None
        with self._edit() as data:
            for raw in data["blocks"]:
                if raw["id"] != block_id:
                    continue
                if start and (when := normalize_time(start)):
                    raw["start"] = when
                if end is not _KEEP:
                    raw["end"] = normalize_time(end) if end else None
                if what and str(what).strip():
                    raw["what"] = str(what).strip()
                found = dict(raw)
                break
        return Block(**found) if found else None

    def remove_block(self, block_id: str) -> Block | None:
        """Drop one block. Returns what was removed, or None if there was no such id."""
        gone: dict[str, Any] | None = None
        with self._edit() as data:
            keep = []
            for raw in data["blocks"]:
                if raw["id"] == block_id and gone is None:
                    gone = dict(raw)
                    continue
                keep.append(raw)
            data["blocks"] = keep
        return Block(**gone) if gone else None

    def mark(self, block_id: str, status: str) -> bool:
        """Record how a block went. Identity and timing are untouched."""
        hit = False
        with self._edit() as data:
            for block in data["blocks"]:
                if block["id"] == block_id:
                    block["status"] = status
                    hit = True
                    break
        return hit

    def copy_from(self, day: str = "") -> list[Block]:
        """Adopt another day's plan as today's. "Same as yesterday."

        Statuses are not carried over — he is doing it again, not remembering
        having done it. Nor are ids: these are new blocks, on a new day.
        """
        from argon.productivity.history import PlanHistory, normalise_blocks

        history = PlanHistory(self.workspace)
        blocks = history.on(day) if day else history.most_recent()[1]
        if not blocks:
            return []
        return self.set_blocks(normalise_blocks(blocks))

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
            return "- no explicit plan"
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
    "Block", "DayPlan", "BLOCK_GRACE_MINUTES", "PLAN_DOC", "STATUSES",
    "normalize_time",
]
