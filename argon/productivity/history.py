"""What his days have actually looked like, and the shapes that repeat.

`plan.json` was a single file keyed by date, so the moment the day rolled over
yesterday's plan was not archived — it was gone. "Same as yesterday" had
nothing to copy, and no pattern could be learned from days that were never
kept. Every day started from zero because every previous day had been thrown
away.

Plans are now written twice: the live one, and a dated copy under
``daily/plans/``. That is enough for the two things he actually asked for —
repeating yesterday, and noticing that robotics keeps landing at five.

The recurrence detection here is deliberately unclever. No clustering
algorithm, no confidence intervals: count how often a block has appeared, see
whether it lands on the same weekday or near the same time, and only speak up
when it has happened enough times to not be a coincidence. A wrong guess about
his schedule is worse than no guess, because he has to notice it and correct
it.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from argon import clock
from argon.productivity.plan import Block, normalize_time

#: How many times a thing must have happened before it is a pattern. Two is a
#: coincidence; three is worth mentioning once.
MIN_OCCURRENCES = 3

#: A time counts as "the same time" within this many minutes. Robotics at 4:50
#: and 5:10 is robotics at five.
SAME_TIME_MINUTES = 45

#: How far back to look. A term's worth — old enough to catch a weekly thing,
#: recent enough that last semester's schedule does not haunt him.
WINDOW_DAYS = 60

#: Fraction of occurrences that must agree before it is stated as a pattern.
AGREEMENT = 0.6

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")


@dataclass
class Pattern:
    """A shape that keeps recurring in his days."""

    name: str
    count: int
    start: str | None = None          # "17:00" when the time is consistent
    end: str | None = None
    weekdays: list[int] | None = None  # 0=Monday, when it clusters on days
    last_seen: str = ""

    def describe(self) -> str:
        when = []
        if self.weekdays:
            days = ", ".join(_WEEKDAYS[d] for d in sorted(self.weekdays))
            when.append(f"on {days}")
        if self.start:
            when.append(f"around {_pretty(self.start)}")
        tail = " ".join(when) if when else "at no particular time"
        return f"{self.name} — {tail} ({self.count}x)"


def _pretty(hhmm: str) -> str:
    hour, minute = (int(p) for p in hhmm.split(":"))
    suffix = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display}{f':{minute:02d}' if minute else ''} {suffix}"


def _minutes(hhmm: str | None) -> int | None:
    if not hhmm:
        return None
    try:
        hour, minute = (int(p) for p in hhmm.split(":"))
    except (AttributeError, ValueError):
        return None
    return hour * 60 + minute


def _to_hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def _key(name: str) -> str:
    """Loose match so "Robotics" and "robotics club" count as the same thing."""
    return " ".join(w for w in name.lower().split() if len(w) > 2)[:40]


class PlanHistory:
    """Every day's plan that has been recorded, and what repeats in them."""

    def __init__(self, workspace: Path) -> None:
        self.dir = workspace / "daily" / "plans"
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- storage -----------------------------------------------------------

    def record(self, day: str, blocks: list[dict[str, Any]]) -> None:
        """Keep a dated copy. Called on every plan write; last write wins."""
        if not blocks:
            return
        (self.dir / f"{day}.json").write_text(
            json.dumps({"date": day, "blocks": blocks}, indent=2), encoding="utf-8"
        )

    def on(self, day: str) -> list[Block]:
        """What he planned on a given day, or nothing."""
        try:
            data = json.loads((self.dir / f"{day}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [Block(**b) for b in data.get("blocks", []) if isinstance(b, dict)]

    def most_recent(self, *, before: str | None = None) -> tuple[str, list[Block]]:
        """The latest day with a plan, and it. Used for "same as yesterday"."""
        before = before or clock.today_key()
        for path in sorted(self.dir.glob("*.json"), reverse=True):
            if path.stem >= before:
                continue
            blocks = self.on(path.stem)
            if blocks:
                return path.stem, blocks
        return "", []

    def days(self, *, window: int = WINDOW_DAYS) -> list[tuple[str, list[Block]]]:
        cutoff = (clock.now() - timedelta(days=window)).strftime("%Y-%m-%d")
        out = []
        for path in sorted(self.dir.glob("*.json")):
            if path.stem < cutoff:
                continue
            blocks = self.on(path.stem)
            if blocks:
                out.append((path.stem, blocks))
        return out

    # -- patterns ----------------------------------------------------------

    def patterns(self, *, minimum: int = MIN_OCCURRENCES) -> list[Pattern]:
        """Shapes that have repeated often enough to be worth stating."""
        seen: dict[str, list[tuple[str, Block]]] = {}
        for day, blocks in self.days():
            for block in blocks:
                if block.status == "skipped":
                    continue  # he planned it and did not do it; not a habit
                seen.setdefault(_key(block.what), []).append((day, block))

        found: list[Pattern] = []
        for occurrences in seen.values():
            if len(occurrences) < minimum:
                continue
            found.append(_summarise(occurrences))
        found.sort(key=lambda p: (-p.count, p.name))
        return found

    def typical(self, name: str) -> Pattern | None:
        """When this usually happens, if it usually happens.

        This is what makes "I have robotics" enough: he named the thing, and
        his own history supplies the time.
        """
        wanted = _key(name)
        if not wanted:
            return None
        for pattern in self.patterns(minimum=2):
            if _key(pattern.name) == wanted:
                return pattern
        return None

    def summary(self) -> str:
        """Patterns as prompt lines. Empty until there is enough history."""
        found = self.patterns()
        if not found:
            return ""
        return "\n".join(f"- {p.describe()}" for p in found[:8])


def _summarise(occurrences: list[tuple[str, Block]]) -> Pattern:
    name = Counter(b.what for _, b in occurrences).most_common(1)[0][0]
    count = len(occurrences)

    weekday_counts = Counter()
    for day, _ in occurrences:
        try:
            weekday_counts[datetime.strptime(day, "%Y-%m-%d").weekday()] += 1
        except ValueError:
            continue
    weekdays = [d for d, n in weekday_counts.items() if n / count >= AGREEMENT]

    timed = [(m, b) for m, b in
             ((_minutes(b.start), b) for _, b in occurrences) if m is not None]
    start = end = None
    if timed:
        # The commonest start, then everything within the window of it. A single
        # 9 AM outlier must not drag a 5 PM club to lunchtime.
        anchor = Counter(m for m, _ in timed).most_common(1)[0][0]
        near = [(m, b) for m, b in timed if abs(m - anchor) <= SAME_TIME_MINUTES]
        if len(near) / len(timed) >= AGREEMENT:
            start = _to_hhmm(sum(m for m, _ in near) // len(near))
            # Ends come from the same occurrences the start came from. Averaging
            # every end regardless turned a 5-7 PM club into "5:00 to 5:12",
            # because the one 9-10 AM outlier still counted.
            ends = [m for m in (_minutes(b.end) for _, b in near) if m is not None]
            if ends:
                end = _to_hhmm(sum(ends) // len(ends))

    return Pattern(
        name=name,
        count=count,
        start=start,
        end=end,
        weekdays=weekdays or None,
        last_seen=max(day for day, _ in occurrences),
    )


def normalise_blocks(blocks: list[Block]) -> list[dict[str, Any]]:
    """Blocks in the shape ``DayPlan.set_blocks`` accepts."""
    return [
        {"start": b.start, "end": b.end, "what": b.what}
        for b in blocks
        if normalize_time(b.start)
    ]
