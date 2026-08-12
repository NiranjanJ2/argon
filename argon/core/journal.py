"""Day-scoped memory, pruned into long-term facts at the end of each day.

The old design had two files: MEMORY.md, which the model was asked to *rewrite
in full* whenever context got tight, and HISTORY.md, an unbounded raw dump. In
practice HISTORY.md reached 8MB of mostly repeated API errors and MEMORY.md
ended up two lines long — a small model handed "return the full updated file"
returns a short one, and every rewrite silently dropped what came before. By
August, Argon remembered one fact about cron jobs and nothing about Niranjan.

So the shape here is different:

* **The journal** is append-only and day-scoped. Writing costs nothing, needs
  no model, and today's page is small enough to put in a prompt verbatim.
* **Consolidation happens once, at the end of the day**, and is *structured*:
  the model proposes facts to carry forward and facts that have gone stale. It
  never rewrites the file, so it cannot erase history by being terse.
* **MEMORY.md is bounded and dated.** Facts expire, and the newest survive a
  cap, so it cannot grow into the context bloat it is meant to prevent.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from argon import clock

#: Long-term memory stays small enough to sit in every system prompt.
MAX_FACTS = 40
MAX_FACT_CHARS = 240

#: Day pages older than this are deleted; MEMORY.md is what survives.
KEEP_DAYS = 45

#: Sentinel for a fact with no recorded learn date.
UNDATED = "0000-00-00"

#: Tool calls worth one journal line, and how to phrase it. Read-only tools are
#: absent on purpose — a day page full of "checked the status" is noise, and the
#: page goes into the check-in prompt verbatim.
#:
#: This exists because the journal used to have exactly one writer: the model
#: choosing to call `remember`. It rarely did, so the day page was usually empty
#: and every check-in re-derived the day from the task list alone — which is how
#: Argon sent three near-identical "SAT prep is due" nudges in one morning.
_TOOL_NOTES: dict[str, Any] = {
    "set_mode": lambda a: "mode -> {}".format(a.get("mode", "?")),
    "add_task": lambda a: "added task: {}".format(a.get("title", "?")),
    "start_task": lambda a: "started: {}".format(a.get("task_id", "?")),
    "complete_task": lambda a: "completed: {}".format(a.get("task_id", "?")),
    "update_task": lambda a: "task changed: {}".format(a.get("task_id", "?")),
    "set_focus_mode": lambda a: "phone -> {}{}".format(
        a.get("mode", "?"),
        " for {}m".format(a["duration_min"]) if a.get("duration_min") else "",
    ),
    "log_note": lambda a: a.get("note") or "",
    "snooze_check_ins": lambda a: (
        "check-ins resumed" if a.get("resume") else "asked for quiet{}{}".format(
            " for {}h".format(a["hours"]) if a.get("hours") else "",
            " ({})".format(a["reason"]) if a.get("reason") else "",
        )
    ),
}


def describe_tool(name: str, args: dict[str, Any]) -> str | None:
    """One journal line for a state-changing tool call, or None to ignore it."""
    make = _TOOL_NOTES.get(name)
    if make is None:
        return None
    try:
        return make(args or {}) or None
    except Exception:  # noqa: BLE001 — journalling must never break a tool call
        return None

_FACT_RE = re.compile(
    r"^-\s*(?P<date>\d{4}-\d{2}-\d{2})\s*·\s*(?P<text>.*?)"
    r"(?:\s*\((?P<standing>standing)\))?"
    r"(?:\s*\(until\s+(?P<until>\d{4}-\d{2}-\d{2})\))?\s*$"
)


@dataclass(frozen=True)
class Fact:
    """One durable thing worth knowing, with the day it was learned."""

    learned: str          # YYYY-MM-DD
    text: str
    until: str | None = None   # YYYY-MM-DD, after which it is dropped
    #: A recurring shape of his life — "school day ends at 3:40", "practice on
    #: Tuesdays" — rather than something that happens once. These never expire
    #: and are never dropped to make room, because they are the facts Argon
    #: needs most and the ones a one-off deadline would otherwise crowd out.
    standing: bool = False

    def line(self) -> str:
        tail = " (standing)" if self.standing else ""
        tail += f" (until {self.until})" if self.until and not self.standing else ""
        # UNDATED facts came from an older build or from hand-editing. Printing
        # the sentinel puts "0000-00-00" in every system prompt, which reads as
        # corruption to anyone looking at it, model included.
        if self.learned == UNDATED:
            return f"- {self.text}{tail}"
        return f"- {self.learned} · {self.text}{tail}"

    def expired(self, today: str) -> bool:
        return bool(not self.standing and self.until and self.until < today)


def parse_facts(text: str) -> list[Fact]:
    """Read MEMORY.md. Unrecognised lines are kept as undated facts."""
    facts: list[Fact] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("-"):
            continue
        match = _FACT_RE.match(line)
        if match:
            facts.append(Fact(
                learned=match["date"],
                text=match["text"].strip(),
                until=match["until"],
                standing=bool(match["standing"]),
            ))
        else:
            # Written by an older build, or by hand. Keep it rather than lose it.
            facts.append(Fact(learned=UNDATED, text=line.lstrip("- ").strip()))
    return facts


def render_facts(facts: list[Fact]) -> str:
    return "# Memory\n\n" + "\n".join(f.line() for f in facts) + "\n"


#: Below this many significant words, two facts are only "the same" if they are
#: literally the same. Word overlap on a short sentence is meaningless — "fact 1"
#: and "fact 28" share every word longer than two characters.
_OVERLAP_FLOOR_WORDS = 5


def _norm(text: str) -> str:
    """Text with punctuation and spacing differences flattened away."""
    return " ".join(re.findall(r"[a-z0-9:]+", text.lower()))


def _key(text: str) -> frozenset[str]:
    """The words that make a fact what it is, for comparing two of them."""
    return frozenset(_norm(text).split())


def _same_fact(a: str, b: str) -> bool:
    """Are these two the same thing said twice?

    Exact text matching let MEMORY.md accumulate:

        Math Analysis summer assignments are due 2026-08-16 20:00 PT.
        Math Analysis summer assignments are due 2026-08-16 20:00 PT

    — a trailing full stop apart, both in every system prompt. The model
    re-states what it already knows on each consolidation, never identically,
    so the comparison has to survive punctuation and small rewordings.

    It must not go further than that. Judging short facts by word overlap
    collapses "fact 1" and "fact 28" into one, so below a handful of words this
    falls back to comparing the normalised text.
    """
    first, second = _key(a), _key(b)
    if _norm(a) == _norm(b):
        return True
    if min(len(first), len(second)) < _OVERLAP_FLOOR_WORDS:
        return False
    # Containment, not symmetric overlap. "School resumes 2026-08-12" and
    # "School resumes 2026-08-12; before then there is no school." are one
    # fact restated, but they score 0.5 on Jaccard because the longer version
    # is penalised for saying more. What matters is whether one is wholly
    # inside the other; the later wording then wins, so nothing is lost.
    shared = len(first & second)
    return shared / min(len(first), len(second)) >= 0.9


def prune(facts: list[Fact], today: str, *, limit: int = MAX_FACTS) -> list[Fact]:
    """Drop expired and duplicate facts, then keep the newest ``limit``."""
    kept: list[Fact] = []
    for fact in facts:
        if fact.expired(today):
            continue
        # Later wins: a restated fact is usually the fresher wording, and its
        # `until` reflects what was known most recently.
        clash = next((k for k in kept if _same_fact(k.text, fact.text)), None)
        if clash is not None:
            kept[kept.index(clash)] = fact
            continue
        kept.append(fact)
    # Newest last so the file reads chronologically, but the cap drops oldest —
    # never a standing fact, which is the kind worth keeping longest.
    kept.sort(key=lambda f: f.learned)
    standing = [f for f in kept if f.standing]
    passing = [f for f in kept if not f.standing]
    room = max(0, limit - len(standing))
    return sorted([*standing, *passing[-room:]], key=lambda f: f.learned)


class Journal:
    """Append-only day pages plus the curated long-term file."""

    def __init__(self, workspace: Path) -> None:
        self.root = workspace / "memory"
        self.days = self.root / "days"
        self.memory_file = self.root / "MEMORY.md"
        self.days.mkdir(parents=True, exist_ok=True)
        self.state_file = self.root / ".consolidated"

    # -- day pages ---------------------------------------------------------

    def day_path(self, day: str | None = None) -> Path:
        return self.days / f"{day or clock.today_key()}.md"

    def note(self, text: str, *, kind: str = "note", day: str | None = None) -> None:
        """Record something worth remembering. Cheap: no model, no rewrite."""
        text = " ".join(text.split())[:MAX_FACT_CHARS]
        if not text:
            return
        path = self.day_path(day)
        stamp = clock.now().strftime("%H:%M")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {stamp} [{kind}] {text}\n")

    def read_day(self, day: str | None = None) -> str:
        path = self.day_path(day)
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def day_has_content(self, day: str | None = None) -> bool:
        return bool(self.read_day(day))

    # -- long-term ---------------------------------------------------------

    def facts(self) -> list[Fact]:
        try:
            return parse_facts(self.memory_file.read_text(encoding="utf-8"))
        except OSError:
            return []

    def write_facts(self, facts: list[Fact]) -> None:
        self.memory_file.parent.mkdir(parents=True, exist_ok=True)
        self.memory_file.write_text(render_facts(facts), encoding="utf-8")

    def add_fact(
        self, text: str, *, until: str | None = None, standing: bool = False
    ) -> Fact:
        """Record a durable fact immediately, without waiting for nightfall."""
        fact = Fact(learned=clock.today_key(), text=" ".join(text.split())[:MAX_FACT_CHARS],
                    until=None if standing else until, standing=standing)
        self.write_facts(prune([*self.facts(), fact], clock.today_key()))
        return fact

    def context(self) -> str:
        """What goes in the system prompt: durable facts + today so far."""
        parts: list[str] = []
        facts = prune(self.facts(), clock.today_key())
        if facts:
            parts.append("\n".join(f.line() for f in facts))
        today = self.read_day()
        if today:
            parts.append(f"## Today ({clock.today_key()})\n\n{today}")
        return "\n\n".join(parts)

    # -- housekeeping ------------------------------------------------------

    def last_consolidated(self) -> str | None:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))["day"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None

    def mark_consolidated(self, day: str) -> None:
        self.state_file.write_text(json.dumps({"day": day}), encoding="utf-8")

    def sweep_old_days(self, keep: int = KEEP_DAYS) -> int:
        """Delete day pages past the window. MEMORY.md is what survives."""
        cutoff = (clock.now() - timedelta(days=keep)).strftime("%Y-%m-%d")
        removed = 0
        for path in self.days.glob("*.md"):
            if path.stem < cutoff:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def pending_day(self) -> str | None:
        """The *oldest* finished day that still needs consolidating.

        This took the newest, then marked it done — so if the gateway was down
        for a day, or two days ran together, the older one was stepped over and
        could never be reached again: ``done`` had already advanced past it.
        Days are folded in the order they happened, one per rollover.
        """
        today = clock.today_key()
        done = self.last_consolidated()
        candidates = sorted(p.stem for p in self.days.glob("*.md") if p.stem < today)
        candidates = [d for d in candidates if done is None or d > done]
        return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# End-of-day consolidation
# ---------------------------------------------------------------------------

_CONSOLIDATE_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "carry_forward",
            "description": "Choose what from today is worth remembering long-term.",
            "parameters": {
                "type": "object",
                "properties": {
                    "keep": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "fact": {
                                    "type": "string",
                                    "description": "One durable fact, written as a full sentence.",
                                },
                                "until": {
                                    "type": "string",
                                    "description": "YYYY-MM-DD after which this stops mattering. Omit if permanent.",
                                },
                                "standing": {
                                    "type": "boolean",
                                    "description": (
                                        "True for a recurring shape of his life — "
                                        "'school days end at 3:40', 'practice on "
                                        "Tuesdays', 'he is free after 4'. These never "
                                        "expire. False for one-off events."
                                    ),
                                },
                            },
                            "required": ["fact"],
                        },
                        "description": "Facts from today worth keeping. Empty is a fine answer.",
                    },
                    "drop": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Existing facts that are now wrong or finished. Quote them exactly.",
                    },
                },
                "required": ["keep"],
            },
        },
    }
]

_PROMPT = """Here is everything Argon recorded on {day}:

{journal}

Here is what Argon already remembers long-term:

{memory}

Call carry_forward to decide what survives.

Keep a fact only if it will still matter in a week: a commitment, a preference,
a deadline, a change in his life, something he asked you to remember. Do not
keep routine chatter, or anything you are inferring rather than reading.

**Never restate something already in long-term memory above.** Repeating it in
slightly different words does not refresh it; it just puts the same fact in
every prompt twice. If an existing fact needs correcting, drop it and write the
new version.

Mark a fact `standing` when it is a recurring shape of his life rather than a
single event — "school days end at 3:40", "practice on Tuesdays", "he is free
after 4pm". Those are the facts worth most and they never expire. Everything
else is a one-off: write it as a plain sentence and set `until` to the day it
stops mattering. "He is starting math homework at 3pm today" is one-off and
should expire tonight, not in a week.

Drop an existing fact only when today's entries show it is finished or wrong.
Keeping nothing and dropping nothing is a perfectly good answer."""


async def consolidate_day(
    journal: Journal, provider: Any, model: str, day: str,
) -> tuple[int, int]:
    """Fold one finished day into long-term memory. Returns (kept, dropped)."""
    entries = journal.read_day(day)
    if not entries:
        journal.mark_consolidated(day)
        return 0, 0

    existing = prune(journal.facts(), clock.today_key())
    memory_text = "\n".join(f.line() for f in existing) or "(nothing yet)"

    response = await provider.chat_with_retry(
        messages=[
            {"role": "system", "content": "You curate an assistant's long-term memory."},
            {"role": "user", "content": _PROMPT.format(
                day=day, journal=entries, memory=memory_text)},
        ],
        tools=_CONSOLIDATE_TOOL,
        model=model,
    )

    if not response.has_tool_calls:
        logger.warning("Day {} not consolidated: model returned no tool call", day)
        return 0, 0

    args = response.tool_calls[0].arguments or {}
    keep = args.get("keep") or []
    drop = {str(d).strip().lower() for d in (args.get("drop") or [])}

    surviving = [f for f in existing if f.text.strip().lower() not in drop]
    added: list[Fact] = []
    for item in keep:
        text = str(item.get("fact") or "").strip() if isinstance(item, dict) else str(item).strip()
        if not text:
            continue
        until = item.get("until") if isinstance(item, dict) else None
        standing = bool(item.get("standing")) if isinstance(item, dict) else False
        added.append(Fact(learned=day, text=text[:MAX_FACT_CHARS],
                          until=None if standing else (until if _valid_day(until) else None),
                          standing=standing))

    journal.write_facts(prune([*surviving, *added], clock.today_key()))
    journal.mark_consolidated(day)
    dropped = len(existing) - len(surviving)
    logger.info("Consolidated {}: +{} facts, -{} stale", day, len(added), dropped)
    return len(added), dropped


def _valid_day(value: Any) -> bool:
    try:
        date.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


def migrate_legacy(workspace: Path) -> str | None:
    """Move the old unbounded HISTORY.md aside, keeping MEMORY.md's facts.

    It reached 8MB of mostly repeated API-error dumps and was never read back
    usefully. Archived rather than deleted — it is the only record of that era.
    """
    root = workspace / "memory"
    history = root / "HISTORY.md"
    if not history.exists():
        return None
    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / "HISTORY.md"
    history.rename(target)

    journal = Journal(workspace)
    facts = prune(journal.facts(), clock.today_key())
    if facts:
        journal.write_facts(facts)
    return str(target)


def _stamp_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")
