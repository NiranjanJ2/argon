"""Tasks, stored here rather than in Google.

Google Tasks was the source of truth, and that made every property of the task
list a property of somebody else's API: no time estimates, no started-at, no
subject, no work-by separate from the deadline. All of it was smuggled into the
notes field as encoded metadata, one round trip per read, rate-limited, and
unavailable whenever the OAuth grant lapsed.

This is the same interface backed by SQLite in ``~/.argon``. It is a drop-in on
purpose: the board, the tools and the API all keep calling the methods they
already call, so the migration is a constructor change rather than a rewrite of
every consumer.

Google Tasks does not go away — it becomes a mirror of today's work, so the
list still shows up on his phone. A mirror can be rebuilt; a source of truth
cannot.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from argon import clock
from argon.core import store
from argon.google.tasks_store import (
    TaskResolutionAmbiguityError,
    _age_days,
    _normalized_task_title,
    _overdue_days,
    _when,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    done              INTEGER NOT NULL DEFAULT 0,
    done_at           TEXT,
    priority          TEXT NOT NULL DEFAULT 'medium',
    source            TEXT NOT NULL DEFAULT 'manual',
    subject           TEXT,
    notes             TEXT,
    due               TEXT,
    official_due      TEXT,
    classroom_id      TEXT,
    classroom_key     TEXT,
    time_estimate_min INTEGER,
    time_actual_min   INTEGER,
    started_at        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    google_task_id    TEXT
);
CREATE INDEX IF NOT EXISTS tasks_done ON tasks(done);
-- Classroom identity is what stops one assignment becoming two commitments,
-- so it is enforced here rather than left to the caller to remember.
CREATE UNIQUE INDEX IF NOT EXISTS tasks_classroom_key
    ON tasks(classroom_key) WHERE classroom_key IS NOT NULL;
"""

_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}

_COLUMNS = (
    "id", "title", "done", "done_at", "priority", "source", "subject", "notes",
    "due", "official_due", "classroom_id", "classroom_key",
    "time_estimate_min", "time_actual_min", "started_at",
    "created_at", "updated_at", "google_task_id",
)


def _now_iso() -> str:
    return clock.now().replace(microsecond=0).isoformat()


def _row_to_task(row: Any) -> dict[str, Any]:
    """One row, in exactly the shape every consumer already expects."""
    task = {key: row[key] for key in _COLUMNS}
    due = task.get("due")
    work_by = None
    if due:
        try:
            work_by = datetime.fromisoformat(due)
        except ValueError:
            work_by = None
    task["done"] = bool(task["done"])
    task["work_by"] = due
    task["work_by_at"] = due
    task["due_when"] = _when(work_by)
    task["days_open"] = _age_days(task.get("created_at"))
    task["days_overdue"] = _overdue_days(due)
    return task


class LocalTaskStore:
    """The task list, owned by Argon."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        # Statement by statement rather than executescript: that helper issues
        # its own COMMIT, which ends the surrounding transaction and makes
        # txn() fail on the way out with "no transaction is active".
        with store.txn() as conn:
            for statement in filter(None, (s.strip() for s in _SCHEMA.split(";"))):
                conn.execute(statement)

    @property
    def workspace(self) -> Path:
        return self._workspace

    # -- reads -------------------------------------------------------------

    def get_all(self, *, include_done: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks"
        if not include_done:
            sql += " WHERE done = 0"
        with store.txn() as conn:
            rows = conn.execute(sql).fetchall()
        tasks = [_row_to_task(r) for r in rows]
        tasks.sort(key=lambda t: (_PRIORITY_RANK.get(t["priority"], 1), t["due"] or "9999"))
        return tasks

    def get_pending(self) -> list[dict[str, Any]]:
        return self.get_all()

    def _resolve(self, task_id: str) -> dict[str, Any] | None:
        """Exact id, then one exact title, then one substring — same as before.

        The ambiguity error is not politeness. Acting on a guess once completed
        the wrong "Math homework" of two, and a wrong completion is invisible:
        the board simply looks one item shorter.
        """
        with store.txn() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is not None:
                return _row_to_task(row)
            # Classroom rows are addressed by their composite key everywhere
            # else, so accept that as identity too.
            row = conn.execute(
                "SELECT * FROM tasks WHERE classroom_key = ?", (task_id,)
            ).fetchone()
            if row is not None:
                return _row_to_task(row)
            rows = conn.execute("SELECT * FROM tasks WHERE done = 0").fetchall()

        needle = _normalized_task_title(task_id)
        if not needle:
            return None
        pending = [_row_to_task(r) for r in rows]
        exact = [t for t in pending if _normalized_task_title(t["title"]) == needle]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise TaskResolutionAmbiguityError(task_id, exact)
        partial = [t for t in pending if needle in _normalized_task_title(t["title"])]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise TaskResolutionAmbiguityError(task_id, partial)
        return None

    # -- writes ------------------------------------------------------------

    def add_task(
        self,
        title: str,
        *,
        source: str = "manual",
        priority: str = "medium",
        due: str | None = None,
        subject: str | None = None,
        notes: str | None = None,
        carry_over: bool = False,
        classroom_id: str | None = None,
        classroom_key: str | None = None,
        official_due: str | None = None,
        time_estimate_min: int | None = None,
    ) -> dict[str, Any]:
        existing = self._existing_match(title, classroom_key, classroom_id)
        if existing:
            # Adding the same thing twice is not a new commitment, it is a
            # second copy that then gets counted and reminded on separately.
            logger.info("add_task: {!r} already exists; returning it", title)
            return {**existing, "already_existed": True}

        now = _now_iso()
        task_id = uuid.uuid4().hex[:16]
        with store.txn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, title, priority, source, subject, notes, due,"
                " official_due, classroom_id, classroom_key, time_estimate_min,"
                " created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, title.strip(), priority, source, subject, notes, due,
                 official_due, classroom_id, classroom_key, time_estimate_min, now, now),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row)

    def _existing_match(
        self, title: str, classroom_key: str | None, classroom_id: str | None
    ) -> dict[str, Any] | None:
        with store.txn() as conn:
            if classroom_key:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE classroom_key = ? AND done = 0",
                    (classroom_key,),
                ).fetchone()
                if row:
                    return _row_to_task(row)
            rows = conn.execute("SELECT * FROM tasks WHERE done = 0").fetchall()
        wanted = _normalized_task_title(title)
        for row in rows:
            if _normalized_task_title(row["title"]) == wanted:
                return _row_to_task(row)
        return None

    def _update(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        target = self._resolve(task_id)
        if target is None:
            return None
        fields["updated_at"] = _now_iso()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with store.txn() as conn:
            conn.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?",
                (*fields.values(), target["id"]),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (target["id"],)).fetchone()
        return _row_to_task(row)

    def set_time_estimate(self, task_id: str, minutes: int) -> bool:
        return self._update(task_id, time_estimate_min=int(minutes)) is not None

    def start_task(self, task_id: str) -> dict[str, Any] | None:
        return self._update(task_id, started_at=_now_iso())

    def complete_task(
        self, task_id: str, *, actual_min: int | None = None
    ) -> dict[str, Any] | None:
        fields: dict[str, Any] = {"done": 1, "done_at": _now_iso()}
        if actual_min is not None:
            fields["time_actual_min"] = int(actual_min)
        return self._update(task_id, **fields)

    def update_priority(self, task_id: str, priority: str) -> bool:
        return self._update(task_id, priority=priority) is not None

    def update_due(self, task_id: str, due_iso: str) -> bool:
        return self._update(task_id, due=due_iso) is not None

    def carry_over_task(self, task_id: str) -> bool:
        tomorrow = (clock.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return self.update_due(task_id, tomorrow)

    def delete_task(self, task_id: str) -> bool:
        target = self._resolve(task_id)
        if target is None:
            return False
        with store.txn() as conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (target["id"],))
        return True

    def bulk_add_from_classroom(self, assignments: list[dict[str, Any]]) -> int:
        added = 0
        for item in assignments:
            key = item.get("classroom_key")
            if not key:
                continue
            result = self.add_task(
                item.get("title") or "Untitled assignment",
                source="classroom",
                due=item.get("due"),
                subject=item.get("course_name") or item.get("course"),
                classroom_id=item.get("course_id"),
                classroom_key=key,
                official_due=item.get("official_due") or item.get("due"),
            )
            if not result.get("already_existed"):
                added += 1
        return added


def migrate_from_google(workspace: Path, *, include_done: bool = True) -> dict[str, Any]:
    """Copy everything out of Google Tasks into the local store.

    Idempotent: rows are matched on ``google_task_id`` first, then on Classroom
    identity, so running it twice imports nothing the second time. That matters
    because the safe way to do this is to run it, look at the counts, and run
    it again after checking rather than having one shot at it.

    Nothing is deleted from Google. The old list stays exactly as it was until
    it is deliberately turned into a mirror, so a bad migration costs a rerun
    rather than his task list.
    """
    from argon.google.tasks_store import GoogleTasksStore

    local = LocalTaskStore(workspace)
    remote = GoogleTasksStore(workspace)
    source = remote.get_all(include_done=include_done)

    imported = skipped = 0
    for task in source:
        gid = task.get("google_task_id")
        with store.txn() as conn:
            if gid and conn.execute(
                "SELECT 1 FROM tasks WHERE google_task_id = ?", (gid,)
            ).fetchone():
                skipped += 1
                continue
            key = task.get("classroom_key")
            if key and conn.execute(
                "SELECT 1 FROM tasks WHERE classroom_key = ?", (key,)
            ).fetchone():
                skipped += 1
                continue

        now = _now_iso()
        with store.txn() as conn:
            conn.execute(
                "INSERT INTO tasks (id, title, done, done_at, priority, source, subject,"
                " notes, due, official_due, classroom_id, classroom_key,"
                " time_estimate_min, time_actual_min, started_at, created_at,"
                " updated_at, google_task_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex[:16],
                    task.get("title") or "Untitled",
                    1 if task.get("done") else 0,
                    task.get("done_at"),
                    task.get("priority") or "medium",
                    task.get("source") or "manual",
                    task.get("subject"),
                    task.get("notes"),
                    task.get("work_by") or task.get("due"),
                    task.get("official_due"),
                    task.get("classroom_id"),
                    task.get("classroom_key"),
                    task.get("time_estimate_min"),
                    task.get("time_actual_min"),
                    task.get("started_at"),
                    now,
                    now,
                    gid,
                ),
            )
        imported += 1

    logger.info("Task migration: imported {}, already present {}", imported, skipped)
    return {
        "read_from_google": len(source),
        "imported": imported,
        "already_present": skipped,
        "local_pending": len(local.get_all()),
        "local_total": len(local.get_all(include_done=True)),
    }
