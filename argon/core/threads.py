"""Threads — the things in Niranjan's life that have a history.

Memory used to be a flat list of sentences with expiry dates. That answers
"what is true about him" and cannot answer "what is the Petoi robot", because a
project is not a fact: it is a name, a status, and a story that accrued over
weeks. Tell Argon about something on the 3rd, mention it on the 24th, and there
was nothing to look it up in — every conversation started from the same four
standing facts, which is exactly why it felt like a fresh chat each time.

The design follows where the open-source memory systems have converged, minus
the machinery this does not need. Letta's tiers, roughly:

* **core** — standing facts and a one-line index of every open thread, always
  in the prompt. Small enough to be free, and it is what gives Argon a sense of
  what exists and how long since it was touched.
* **recall** — today's journal page, verbatim.
* **archival** — the thread files themselves, pulled in when their name comes
  up, or by the `recall` tool.

Retrieval is by *name*, the way mem0 and ai-memory do entity-assisted recall:
exact and alias matching against what he actually said. No embeddings, no
vector store, no graph database. One markdown file per thread, greppable by a
human, and a mention of "petoi" is enough to load the whole history.

Time is always rendered relatively — "last touched 12 days ago" rather than a
date — because the thing he wants back is the *sense* of elapsed time, and
nobody reads 2026-07-30 and feels three weeks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from argon import clock

#: A thread untouched for longer than this is not offered unprompted. It stays
#: on disk and stays findable; it just stops taking up room in every prompt.
DORMANT_DAYS = 45

#: Never put more than this many threads in the always-on index. Sorted by
#: recency, so what falls off is what he has not thought about in weeks.
INDEX_LIMIT = 12

#: Enough of the log to recognise the thread; the rest needs a deliberate read.
LOG_LINES_IN_CONTEXT = 8

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_WORD_RE = re.compile(r"[a-z0-9']+")

STATUSES = ("active", "paused", "done", "dropped")

#: Words that appear in half his projects and so identify none of them. A name
#: made only of these still matches, but only in full.
_GENERIC = frozenset({
    "robot", "prep", "work", "class", "club", "project", "homework", "lab",
    "test", "exam", "quiz", "essay", "paper", "study", "practice", "session",
    "meeting", "assignment", "summer", "school", "team", "with", "week",
    "thing", "stuff", "time", "help", "plan", "note", "notes", "review",
})


def slugify(name: str) -> str:
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")[:60]


def ago(when: str | None, *, now: datetime | None = None) -> str:
    """"12 days ago", "3 weeks ago", "today" — elapsed time as people say it."""
    if not when:
        return "never"
    try:
        then = datetime.fromisoformat(when)
    except (TypeError, ValueError):
        return "unknown"
    now = now or clock.now()
    if then.tzinfo is None and now.tzinfo is not None:
        then = then.replace(tzinfo=now.tzinfo)
    days = (now.date() - then.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


@dataclass
class Thread:
    """One project, class, person or ongoing concern."""

    slug: str
    name: str
    summary: str = ""
    status: str = "active"
    aliases: list[str] = field(default_factory=list)
    first_seen: str = ""
    last_touched: str = ""
    log: list[str] = field(default_factory=list)   # "YYYY-MM-DD — what happened"

    # -- matching ----------------------------------------------------------

    def names(self) -> list[str]:
        return [self.name, *self.aliases]

    def mentioned_in(self, text: str) -> bool:
        """Does this text name the thread?

        Two ways to match, both on whole words. Either the full name is present,
        or one distinctive word of it is — because three weeks later he says
        "the petoi", not "Petoi robot", and making that work must not depend on
        the model having thought to add an alias.

        Distinctive means long enough and not a word every project shares:
        "petoi" identifies a thread, "robot" and "prep" do not. Matching on
        those would pull the wrong history, and wrong retrieved context is worse
        than none.

        Whole words throughout, so "SAT prep" never fires on "saturday" —
        that is one token, not two.
        """
        words = set(_WORD_RE.findall((text or "").lower()))
        if not words:
            return False
        for name in self.names():
            needed = set(_WORD_RE.findall(name.lower()))
            if not needed:
                continue
            if needed <= words:
                return True
            distinctive = {w for w in needed if len(w) >= 4 and w not in _GENERIC}
            if distinctive & words:
                return True
        return False

    # -- rendering ---------------------------------------------------------

    def index_line(self, *, now: datetime | None = None) -> str:
        """The one line that lives in every prompt."""
        summary = f" — {self.summary}" if self.summary else ""
        mark = "" if self.status == "active" else f" [{self.status}]"
        return f"- **{self.name}**{mark}{summary} (last touched {ago(self.last_touched, now=now)})"

    def full(self, *, now: datetime | None = None) -> str:
        """Everything worth knowing, for when he brings it up."""
        head = [f"### {self.name}"]
        if self.aliases:
            head.append(f"*also called: {', '.join(self.aliases)}*")
        if self.summary:
            head.append(self.summary)
        head.append(
            f"Status: {self.status}. Started {ago(self.first_seen, now=now)}, "
            f"last touched {ago(self.last_touched, now=now)}."
        )
        if self.log:
            head.append("")
            head.extend(self.log[-LOG_LINES_IN_CONTEXT:])
        return "\n".join(head)

    def to_markdown(self) -> str:
        lines = [
            f"# {self.name}",
            "",
            f"slug: {self.slug}",
            f"status: {self.status}",
            f"aliases: {', '.join(self.aliases)}",
            f"first_seen: {self.first_seen}",
            f"last_touched: {self.last_touched}",
            "",
            self.summary,
            "",
            "## Log",
            "",
        ]
        lines.extend(self.log)
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def from_markdown(cls, text: str, slug: str) -> "Thread":
        head, _, rest = text.partition("## Log")
        meta: dict[str, str] = {}
        summary_lines: list[str] = []
        for raw in head.splitlines():
            line = raw.strip()
            if line.startswith("# "):
                meta["name"] = line[2:].strip()
            elif ":" in line and line.split(":", 1)[0] in (
                "slug", "status", "aliases", "first_seen", "last_touched"
            ):
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()
            elif line:
                summary_lines.append(line)
        aliases = [a.strip() for a in meta.get("aliases", "").split(",") if a.strip()]
        return cls(
            slug=meta.get("slug") or slug,
            name=meta.get("name") or slug,
            summary=" ".join(summary_lines),
            status=meta.get("status", "active"),
            aliases=aliases,
            first_seen=meta.get("first_seen", ""),
            last_touched=meta.get("last_touched", ""),
            log=[ln.rstrip() for ln in rest.splitlines() if ln.strip().startswith("-")],
        )


class Threads:
    """The set of things with a history. One markdown file each."""

    def __init__(self, workspace: Path) -> None:
        self.dir = workspace / "memory" / "threads"
        self.dir.mkdir(parents=True, exist_ok=True)

    # -- storage -----------------------------------------------------------

    def all(self) -> list[Thread]:
        out: list[Thread] = []
        for path in sorted(self.dir.glob("*.md")):
            try:
                out.append(Thread.from_markdown(path.read_text(encoding="utf-8"), path.stem))
            except Exception:  # noqa: BLE001 — one bad file must not hide the rest
                continue
        out.sort(key=lambda t: t.last_touched, reverse=True)
        return out

    def get(self, name_or_slug: str) -> Thread | None:
        slug = slugify(name_or_slug)
        path = self.dir / f"{slug}.md"
        if path.exists():
            return Thread.from_markdown(path.read_text(encoding="utf-8"), slug)
        for thread in self.all():
            if slugify(thread.name) == slug or slug in [slugify(a) for a in thread.aliases]:
                return thread
        return None

    def save(self, thread: Thread) -> Thread:
        (self.dir / f"{thread.slug}.md").write_text(thread.to_markdown(), encoding="utf-8")
        return thread

    def delete(self, name_or_slug: str) -> bool:
        thread = self.get(name_or_slug)
        if thread is None:
            return False
        (self.dir / f"{thread.slug}.md").unlink(missing_ok=True)
        return True

    # -- writing -----------------------------------------------------------

    def note(
        self,
        name: str,
        entry: str = "",
        *,
        summary: str | None = None,
        status: str | None = None,
        aliases: list[str] | None = None,
        day: str | None = None,
    ) -> Thread:
        """Create or update a thread. Every call counts as touching it."""
        today = day or clock.today_key()
        thread = self.get(name) or Thread(
            slug=slugify(name), name=name.strip(), first_seen=today
        )
        newest = not thread.last_touched or today >= thread.last_touched
        if summary is not None and newest:
            thread.summary = summary.strip()
        if status in STATUSES and newest:
            thread.status = status
        for alias in aliases or []:
            if alias.strip() and alias.strip().lower() != thread.name.lower():
                if alias.strip() not in thread.aliases:
                    thread.aliases.append(alias.strip())
        if entry.strip():
            line = f"- {today} — {entry.strip()}"
            if line not in thread.log:
                thread.log.append(line)
                thread.log.sort(key=lambda item: item[2:12])
        thread.last_touched = max(filter(None, (thread.last_touched, today)))
        thread.first_seen = min(filter(None, (thread.first_seen, today)))
        return self.save(thread)

    # -- retrieval ---------------------------------------------------------

    def index(self, *, now: datetime | None = None, limit: int = INDEX_LIMIT) -> str:
        """The always-in-context list: what exists, and how long since each."""
        now = now or clock.now()
        live = [
            t for t in self.all()
            if t.status in ("active", "paused")
            and _days_since(t.last_touched, now) <= DORMANT_DAYS
        ]
        if not live:
            return ""
        return "\n".join(t.index_line(now=now) for t in live[:limit])

    def mentioned(self, text: str, *, limit: int = 3) -> list[Thread]:
        """Threads this text names, most recently touched first."""
        return [t for t in self.all() if t.mentioned_in(text)][:limit]

    def recall(self, text: str, *, now: datetime | None = None) -> str:
        """Full detail on whatever the text brings up. Empty if nothing."""
        found = self.mentioned(text)
        if not found:
            return ""
        return "\n\n".join(t.full(now=now) for t in found)


def _days_since(when: str, now: datetime) -> int:
    try:
        then = datetime.fromisoformat(when)
    except (TypeError, ValueError):
        return 10_000
    if then.tzinfo is None and now.tzinfo is not None:
        then = then.replace(tzinfo=now.tzinfo)
    return (now.date() - then.date()).days
