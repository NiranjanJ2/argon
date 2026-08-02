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

_FACT_RE = re.compile(
    r"^-\s*(?P<date>\d{4}-\d{2}-\d{2})\s*·\s*(?P<text>.*?)"
    r"(?:\s*\(until\s+(?P<until>\d{4}-\d{2}-\d{2})\))?\s*$"
)


@dataclass(frozen=True)
class Fact:
    """One durable thing worth knowing, with the day it was learned."""

    learned: str          # YYYY-MM-DD
    text: str
    until: str | None = None   # YYYY-MM-DD, after which it is dropped

    def line(self) -> str:
        tail = f" (until {self.until})" if self.until else ""
        return f"- {self.learned} · {self.text}{tail}"

    def expired(self, today: str) -> bool:
        return bool(self.until and self.until < today)


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
            ))
        else:
            # Written by an older build, or by hand. Keep it rather than lose it.
            facts.append(Fact(learned="0000-00-00", text=line.lstrip("- ").strip()))
    return facts


def render_facts(facts: list[Fact]) -> str:
    return "# Memory\n\n" + "\n".join(f.line() for f in facts) + "\n"


def prune(facts: list[Fact], today: str, *, limit: int = MAX_FACTS) -> list[Fact]:
    """Drop expired and duplicate facts, then keep the newest ``limit``."""
    seen: set[str] = set()
    kept: list[Fact] = []
    for fact in facts:
        if fact.expired(today):
            continue
        key = fact.text.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        kept.append(fact)
    # Newest last so the file reads chronologically, but the cap drops oldest.
    kept.sort(key=lambda f: f.learned)
    return kept[-limit:]


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

    def add_fact(self, text: str, *, until: str | None = None) -> Fact:
        """Record a durable fact immediately, without waiting for nightfall."""
        fact = Fact(learned=clock.today_key(), text=" ".join(text.split())[:MAX_FACT_CHARS],
                    until=until)
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
        """The most recent finished day that still needs consolidating."""
        today = clock.today_key()
        done = self.last_consolidated()
        candidates = sorted(p.stem for p in self.days.glob("*.md") if p.stem < today)
        candidates = [d for d in candidates if done is None or d > done]
        return candidates[-1] if candidates else None


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
keep routine chatter, anything already in long-term memory, or anything you are
inferring rather than reading. Write each fact as a plain sentence that will
still make sense months from now, and set `until` on anything with a natural
end date.

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
        added.append(Fact(learned=day, text=text[:MAX_FACT_CHARS],
                          until=until if _valid_day(until) else None))

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
