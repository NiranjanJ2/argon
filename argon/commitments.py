"""One reconciled commitment board — the only thing that answers "what's due".

Reconciliation used to live inside ``get_daily_overview``'s presentation code:
it was the only consumer that knew a Classroom assignment had been turned in or
ignored. Every other consumer — ``list_tasks``, the after-school brief's overdue
lines, the check-in gate's pending count, the iOS dashboard — read raw Google
Tasks. So an assignment he had submitted vanished from the board and was still
announced as pending at 4 PM, and an assignment he had explicitly told Argon to
ignore came back as an overdue task. Two parts of the system believed different
things about the same fact; this module is the one that owns it.

It joins three sources on a durable composite identity, ``course_id:coursework_id``:

* **Google Classroom** coursework, with the student submission state
  (``argon/google/classroom.py``).
* **Google Tasks** (``argon/google/tasks_store.py``).
* **Durable dispositions** — his ignore/restore decisions
  (``argon/google/classroom_dispositions.py``).

Overlay, not projection
-----------------------
An old Google Task carrying ``source=classroom`` is a **personal overlay**, not
a duplicate row and not a projection of the assignment. Classroom owns the
title, the official deadline and the submission state; the Google Task owns one
fact Classroom cannot know — the **personal work-by date** he set for himself,
which is usually earlier than the deadline. So a task that reconciles to an
assignment contributes its work-by date (and its priority, estimate and notes)
to that assignment and emits **no row of its own**: one assignment is always one
commitment. If the assignment is turned in, returned, or ignored, the overlay
disappears with it — it is not independent work.

Two consequences follow, and both are deliberate:

* ``official_due`` and ``work_by`` are separate fields, never merged. They are
  different facts: one is the school's deadline, one is his plan. ``due`` is a
  derived convenience (work-by if he set one, otherwise the deadline) so the
  existing task-shaped consumers keep working.
* Classroom assignments are **never bulk-projected into Google Tasks**. Copying
  them creates a second owner of the deadline that immediately goes stale;
  ``bulk_add_from_classroom`` remains available for a task he wants to schedule,
  but nothing here writes.

A task that does *not* reconcile to any assignment stays visible on its own.
That includes a Classroom-derived task whose assignment has fallen outside the
lookahead window, and every task while Classroom is unreachable — losing work
silently is the failure this module exists to prevent.

Partial failure is data
-----------------------
Each source degrades on its own and says so. ``Board.sources`` carries the
health of both, ``Board.complete`` is false whenever anything is missing, and
``Board.health_lines()`` is the human sentence every consumer must show. A board
built with Classroom down reports "Classroom unavailable", never "nothing due".
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

__all__ = [
    "Board",
    "Commitment",
    "SourceHealth",
    "SourceSnapshot",
    "build_board",
    "classroom_snapshot",
    "invalidate",
    "load_board",
    "tasks_snapshot",
]

#: Classroom is a multi-request fetch (courses, then coursework, then one
#: submission lookup per assignment) and the desktop widgets poll ``/v1/tasks``
#: every few seconds, so it cannot be read every time somebody asks.
#:
#: Two minutes is the compromise. Submission state is the thing that goes stale
#: in a way he notices — he turns something in on Classroom and wants it off his
#: list, not still being announced as due — and five minutes of that is long
#: enough to look broken. A pull-to-refresh bypasses this entirely.
#:
#: Google Tasks is a single cheap request and is *not* cached here: the API
#: server owns that cache, and a task he just ticked off has to vanish at once.
CLASSROOM_TTL_S = 120.0

#: Submission states that mean the work is done and must not be announced again.
DONE_SUBMISSION_STATES = frozenset({"TURNED_IN", "RETURNED"})

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
_LABELS = {"classroom": "Classroom", "tasks": "Tasks"}

_classroom_cache: dict[tuple[str, int], tuple[float, "SourceSnapshot"]] = {}
_classroom_lock = threading.Lock()

#: Cache keys with a background re-crawl already in flight. Without this, the
#: twenty-fifth poll of a stale cache would start a twenty-fifth crawl.
_classroom_refreshing: set[tuple] = set()


def _when(value: Any) -> str | None:
    from argon.utils.helpers import when_label

    return when_label(value)


def _normalized(value: Any) -> str:
    """Conservative legacy reconciliation key: words only, case-insensitive."""
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _day(value: Any) -> str | None:
    """The date half of a Google Tasks due stamp, without timezone conversion."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    return value[:10]


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceHealth:
    """Whether one source answered, and what it could not tell us."""

    name: str
    ok: bool
    error: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return _LABELS.get(self.name, self.name.title())

    @property
    def complete(self) -> bool:
        return self.ok and not self.warnings

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "label": self.label,
            "ok": self.ok,
            "complete": self.complete,
            "error": self.error,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class SourceSnapshot:
    """What one source returned this time round, failures included."""

    name: str
    items: tuple[dict[str, Any], ...] = ()
    error: str | None = None
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.error is None

    def health(self) -> SourceHealth:
        return SourceHealth(self.name, self.ok, self.error, self.warnings)


@dataclass(frozen=True)
class Commitment:
    """One thing he owes, whoever it came from.

    ``official_due`` is the school's deadline and ``work_by`` is the date he
    chose for himself. They are kept apart on purpose: collapsing them loses the
    difference between "this is late" and "I meant to start it earlier".
    """

    key: str
    title: str
    origin: str = "task"          # "classroom" | "task"
    subject: str | None = None
    official_due: str | None = None
    official_due_when: str | None = None
    work_by: str | None = None
    work_by_when: str | None = None
    priority: str = "medium"
    source: str = "manual"
    days_open: int | None = None
    days_overdue: int | None = None
    time_estimate_min: int | None = None
    notes: str | None = None
    link: str | None = None
    google_task_id: str | None = None
    classroom_key: str | None = None
    submission_state: str | None = None
    submission_error: str | None = None
    suppressed_reason: str | None = None

    @property
    def due(self) -> str | None:
        """The date to act on: his own work-by if he set one, else the deadline."""
        return self.work_by or self.official_due

    @property
    def due_when(self) -> str | None:
        return self.work_by_when or self.official_due_when

    def as_dict(self) -> dict[str, Any]:
        """Task-shaped so the iOS app and the scheduling stamps keep working."""
        return {
            "id": self.google_task_id or self.key,
            "key": self.key,
            "title": self.title,
            "origin": self.origin,
            "source": self.source,
            "subject": self.subject,
            "priority": self.priority,
            "due": self.due,
            "due_when": self.due_when,
            "official_due": self.official_due,
            "official_due_when": self.official_due_when,
            "work_by": self.work_by,
            "work_by_when": self.work_by_when,
            "days_open": self.days_open,
            "days_overdue": self.days_overdue,
            "time_estimate_min": self.time_estimate_min,
            "notes": self.notes,
            "link": self.link,
            "google_task_id": self.google_task_id,
            "classroom_key": self.classroom_key,
            "submission_state": self.submission_state,
            "submission_error": self.submission_error,
        }


@dataclass(frozen=True)
class Board:
    """The canonical list, what was hidden from it, and what it could not see."""

    commitments: tuple[Commitment, ...] = ()
    suppressed: tuple[Commitment, ...] = field(default=())
    sources: tuple[SourceHealth, ...] = ()

    def source(self, name: str) -> SourceHealth:
        for health in self.sources:
            if health.name == name:
                return health
        return SourceHealth(name, ok=False, error="not consulted")

    @property
    def complete(self) -> bool:
        """False whenever anything is missing — a short board may be a lie."""
        return all(health.complete for health in self.sources)

    def health_lines(self) -> list[str]:
        """The sentences a consumer must show so a short board is not read as empty."""
        lines: list[str] = []
        for health in self.sources:
            if health.error:
                lines.append(f"Unavailable: {health.label} — {health.error}")
            for warning in health.warnings:
                lines.append(f"{health.label} incomplete: {warning}")
        return lines

    def as_dicts(self) -> list[dict[str, Any]]:
        return [c.as_dict() for c in self.commitments]

    def health_as_dicts(self) -> list[dict[str, Any]]:
        return [health.as_dict() for health in self.sources]


# ---------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------


def _assignment_key(item: dict[str, Any]) -> str:
    key = item.get("classroom_key")
    if key:
        return str(key)
    return "{}:{}".format(item.get("course_id") or "?", item.get("id") or "?")


def _match(
    task: dict[str, Any],
    assignments: list[dict[str, Any]],
    by_key: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """The assignment this task is a personal overlay of, or None.

    Composite identity first, because it is the only one that cannot be wrong.
    Tasks copied before that key existed carry the course-local coursework id
    plus the course name, then nothing but a title — and a title is only
    accepted when exactly one assignment answers to it. Guessing here would
    silently hide real work behind an unrelated assignment.
    """
    key = task.get("classroom_key")
    if key:
        return by_key.get(str(key))
    if (task.get("source") or "manual") != "classroom":
        return None

    coursework_id = task.get("classroom_id")
    subject = task.get("subject")
    if coursework_id:
        hits = [
            a for a in assignments
            if str(a.get("id")) == str(coursework_id)
            and (not subject or not a.get("course_name") or subject == a.get("course_name"))
        ]
        if len(hits) == 1:
            return hits[0]

    normalized = _normalized(task.get("title"))
    if not normalized:
        return None
    hits = [a for a in assignments if _normalized(a.get("title")) == normalized]
    return hits[0] if len(hits) == 1 else None


#: Classes whose real deadline is earlier than the date Classroom shows, in
#: days. Machado's Math Analysis/Calc A is collected the lesson *before* the
#: date on the assignment, so an item Classroom calls Thursday is actually due
#: Wednesday — and the board showed it under Today on the day it was already
#: late.
#:
#: This is a work-by date, which is exactly the field's purpose: Classroom owns
#: the official deadline and it stays untouched, while work_by carries when he
#: actually has to have it done.
CLASS_DUE_OFFSETS_DAYS: dict[str, int] = {
    "math analysis": 1,
    "calc a": 1,
}


def _class_offset_days(subject: Any) -> int:
    """How many days early this class really wants the work."""
    if not isinstance(subject, str):
        return 0
    lowered = subject.lower()
    for needle, days in CLASS_DUE_OFFSETS_DAYS.items():
        if needle in lowered:
            return days
    return 0


def _shifted_work_by(subject: Any, official_due: Any) -> str | None:
    """The official deadline pulled forward for classes that collect early."""
    days = _class_offset_days(subject)
    if not days:
        return None
    day = _day(official_due)
    if not day:
        return None
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(day) - timedelta(days=days)).isoformat()
    except ValueError:
        return None


def _from_assignment(
    item: dict[str, Any], overlay: dict[str, Any] | None
) -> Commitment:
    over = overlay or {}
    subject = item.get("course_name") or item.get("course") or over.get("subject")
    # A date he set for himself always wins; the class rule only fills the gap.
    work_by = (
        over.get("work_by")
        or _day(over.get("due"))
        or _shifted_work_by(subject, item.get("due"))
    )
    return Commitment(
        key=_assignment_key(item),
        title=item.get("title") or "(untitled)",
        origin="classroom",
        subject=subject,
        official_due=item.get("due"),
        official_due_when=item.get("due_when") or _when(item.get("due")),
        work_by=work_by,
        work_by_when=over.get("due_when") or _when(work_by),
        priority=over.get("priority") or "medium",
        source="classroom",
        days_open=over.get("days_open"),
        days_overdue=over.get("days_overdue"),
        time_estimate_min=over.get("time_estimate_min"),
        notes=over.get("notes"),
        link=item.get("link"),
        google_task_id=over.get("google_task_id") or over.get("id"),
        classroom_key=item.get("classroom_key") or _assignment_key(item),
        submission_state=item.get("submission_state"),
        submission_error=item.get("submission_error"),
        suppressed_reason=item.get("suppressed_reason"),
    )


def _from_task(task: dict[str, Any]) -> Commitment:
    task_id = task.get("google_task_id") or task.get("id") or ""
    work_by = task.get("work_by") or _day(task.get("due"))
    return Commitment(
        key=f"task:{task_id}",
        title=task.get("title") or "(untitled)",
        origin="task",
        subject=task.get("subject"),
        official_due=task.get("official_due"),
        official_due_when=_when(task.get("official_due")),
        work_by=work_by,
        work_by_when=task.get("due_when") or _when(work_by),
        priority=task.get("priority") or "medium",
        source=task.get("source") or "manual",
        days_open=task.get("days_open"),
        days_overdue=task.get("days_overdue"),
        time_estimate_min=task.get("time_estimate_min"),
        notes=task.get("notes"),
        google_task_id=str(task_id) or None,
        classroom_key=task.get("classroom_key"),
    )


def _order(commitment: Commitment) -> tuple[int, str, str]:
    return (
        _PRIORITY_RANK.get(commitment.priority, 1),
        commitment.due or "9999",
        commitment.title,
    )


def build_board(classroom: SourceSnapshot, tasks: SourceSnapshot) -> Board:
    """Join two snapshots into one list of commitments. Pure; never fetches."""
    assignments = list(classroom.items)
    by_key: dict[str, dict[str, Any]] = {}
    for item in assignments:
        by_key.setdefault(_assignment_key(item), item)

    overlays: dict[int, dict[str, Any]] = {}
    loose: list[dict[str, Any]] = []
    for task in tasks.items:
        match = _match(task, assignments, by_key)
        if match is None:
            loose.append(task)
            continue
        # A second copy of the same assignment is still that assignment. It
        # contributes nothing new and must not become a second row.
        overlays.setdefault(id(match), task)

    active: list[Commitment] = []
    hidden: list[Commitment] = []
    for item in assignments:
        commitment = _from_assignment(item, overlays.get(id(item)))
        (hidden if commitment.suppressed_reason else active).append(commitment)
    active.extend(_from_task(task) for task in loose)
    active.sort(key=_order)
    hidden.sort(key=_order)

    # A submission lookup that failed is already stated on the assignment
    # itself; repeating it as a course-level warning double-counts one problem.
    shown_errors = {
        str(item["submission_error"]) for item in assignments
        if item.get("submission_error")
    }
    classroom_health = SourceHealth(
        classroom.name,
        classroom.ok,
        classroom.error,
        tuple(
            warning for warning in classroom.warnings
            if not any(error in warning for error in shown_errors)
        ),
    )
    return Board(
        commitments=tuple(active),
        suppressed=tuple(hidden),
        sources=(classroom_health, tasks.health()),
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def _degraded(name: str, exc: BaseException, account: str) -> SourceSnapshot:
    from argon.google.service import google_error_message

    message = google_error_message(exc, account) or f"{type(exc).__name__}: {exc}"
    logger.warning("Commitment source {} unavailable: {}", name, exc)
    return SourceSnapshot(name, (), message, ())


def classroom_snapshot(
    workspace: Path, *, days_ahead: int = 7, fresh: bool = False
) -> SourceSnapshot:
    """Classroom coursework with submission state and his ignore decisions applied.

    Suppressed items come back too, carrying ``suppressed_reason`` — the board
    needs them to recognise the personal overlays that must disappear with them.
    """
    cache_key = (str(workspace), days_ahead)
    with _classroom_lock:
        cached = _classroom_cache.get(cache_key)
        if cached and not fresh:
            if (time.monotonic() - cached[0]) < CLASSROOM_TTL_S:
                return cached[1]
            # Stale. Answer from the cache anyway and refresh behind the
            # request: the crawl is one submissions call per assignment and
            # takes around eight seconds, so making a poll wait for it stalled
            # every reader on the same two-minute cycle — the desktop readout
            # timed out and "fixed itself", and the phone silently blocked for
            # eight seconds. Slightly old coursework beats a readout that
            # periodically fails.
            if cache_key not in _classroom_refreshing:
                _classroom_refreshing.add(cache_key)
                threading.Thread(
                    target=_refresh_classroom,
                    args=(workspace, days_ahead, cache_key),
                    daemon=True,
                ).start()
            return cached[1]

    return _crawl_classroom(workspace, days_ahead, cache_key)


def _refresh_classroom(workspace: Path, days_ahead: int, cache_key: tuple) -> None:
    """Re-crawl in the background, then let the next refresh be scheduled."""
    try:
        _crawl_classroom(workspace, days_ahead, cache_key)
    finally:
        with _classroom_lock:
            _classroom_refreshing.discard(cache_key)


def _crawl_classroom(
    workspace: Path, days_ahead: int, cache_key: tuple
) -> SourceSnapshot:
    """The actual Classroom read. Slow; never call it holding the lock."""
    try:
        from argon.google.classroom import upcoming_assignments
        from argon.google.classroom_dispositions import ClassroomDispositionStore
        from argon.google.service import build_google_service

        svc = build_google_service(workspace, "classroom", "v1", "school")
        items, unreadable = upcoming_assignments(
            svc,
            days_ahead=days_ahead,
            dispositions=ClassroomDispositionStore(workspace),
            include_suppressed=True,
        )
    except Exception as exc:  # noqa: BLE001 — a school outage must stay visible, not fatal
        return _degraded("classroom", exc, "school")

    snapshot = SourceSnapshot("classroom", tuple(items), None, tuple(unreadable))
    with _classroom_lock:
        _classroom_cache[cache_key] = (time.monotonic(), snapshot)
    return snapshot


def tasks_snapshot(workspace: Path, *, store: Any = None) -> SourceSnapshot:
    """Pending Google Tasks. Not cached here — see ``CLASSROOM_TTL_S``."""
    try:
        if store is None:
            from argon.tasks.local_store import LocalTaskStore

            store = LocalTaskStore(workspace)
        items = store.get_all()
    except Exception as exc:  # noqa: BLE001 — offline must not become a guess
        return _degraded("tasks", exc, "work")
    return SourceSnapshot("tasks", tuple(items), None, ())


def load_board(
    workspace: Path,
    *,
    days_ahead: int = 7,
    store: Any = None,
    fresh: bool = False,
) -> Board:
    """The canonical board. Never raises: a dead source becomes source health."""
    return build_board(
        classroom_snapshot(workspace, days_ahead=days_ahead, fresh=fresh),
        tasks_snapshot(workspace, store=store),
    )


def invalidate() -> None:
    """Drop the cached Classroom read after an ignore/restore decision."""
    with _classroom_lock:
        _classroom_cache.clear()
