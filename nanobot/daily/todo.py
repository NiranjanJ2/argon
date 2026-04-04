"""Daily to-do list — JSON-persisted, resets at 4:00 AM.

File: workspace/daily/todo_{date}.json
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("America/Los_Angeles")

Priority = Literal["high", "medium", "low"]
Source = Literal["classroom", "manual", "ucla", "club"]


def _now() -> datetime:
    return datetime.now(_TZ)


def _today_key() -> str:
    now = _now()
    if now.hour < 4:
        from datetime import timedelta
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


class DailyTodo:
    """Manages the daily to-do list."""

    def __init__(self, workspace: Path) -> None:
        self._dir = workspace / "daily"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, date_key: str | None = None) -> Path:
        return self._dir / f"todo_{date_key or _today_key()}.json"

    def _load(self, date_key: str | None = None) -> list[dict[str, Any]]:
        p = self._path(date_key)
        if p.exists():
            return json.loads(p.read_text())
        return []

    def _save(self, tasks: list[dict[str, Any]], date_key: str | None = None) -> None:
        self._path(date_key).write_text(json.dumps(tasks, indent=2))

    # ------------------------------------------------------------------
    # Task operations
    # ------------------------------------------------------------------

    def add_task(
        self,
        title: str,
        *,
        source: Source = "manual",
        priority: Priority = "medium",
        due: str | None = None,          # ISO datetime string from Classroom
        subject: str | None = None,
        notes: str | None = None,
        carry_over: bool = False,
        classroom_id: str | None = None,
    ) -> dict[str, Any]:
        tasks = self._load()
        task: dict[str, Any] = {
            "id": str(uuid.uuid4())[:8],
            "title": title,
            "source": source,
            "priority": priority,
            "due": due,
            "subject": subject,
            "notes": notes,
            "done": False,
            "done_at": None,
            "added_at": _now().isoformat(),
            "carry_over": carry_over,
            "classroom_id": classroom_id,
            "time_estimate_min": None,   # filled in by habit learner
            "time_actual_min": None,     # filled in on completion
            "started_at": None,
        }
        tasks.append(task)
        self._save(tasks)
        return task

    def complete_task(self, task_id: str) -> str:
        tasks = self._load()
        for t in tasks:
            if t["id"] == task_id or task_id.lower() in t["title"].lower():
                if t.get("started_at"):
                    started = datetime.fromisoformat(t["started_at"])
                    t["time_actual_min"] = int((_now() - started).total_seconds() / 60)
                t["done"] = True
                t["done_at"] = _now().isoformat()
                self._save(tasks)
                return t["id"]
        return ""

    def start_task(self, task_id: str) -> str:
        tasks = self._load()
        for t in tasks:
            if t["id"] == task_id or task_id.lower() in t["title"].lower():
                t["started_at"] = _now().isoformat()
                self._save(tasks)
                return t["id"]
        return ""

    def update_priority(self, task_id: str, priority: Priority) -> bool:
        tasks = self._load()
        for t in tasks:
            if t["id"] == task_id or task_id.lower() in t["title"].lower():
                t["priority"] = priority
                self._save(tasks)
                return True
        return False

    def set_time_estimate(self, task_id: str, minutes: int) -> bool:
        tasks = self._load()
        for t in tasks:
            if t["id"] == task_id or task_id.lower() in t["title"].lower():
                t["time_estimate_min"] = minutes
                self._save(tasks)
                return True
        return False

    def carry_over_task(self, task_id: str) -> bool:
        """Mark a task to carry over to tomorrow."""
        today_tasks = self._load()
        tomorrow_key = _tomorrow_key()
        tomorrow_tasks = self._load(tomorrow_key)

        for t in today_tasks:
            if t["id"] == task_id or task_id.lower() in t["title"].lower():
                carried = dict(t)
                carried["id"] = str(uuid.uuid4())[:8]
                carried["added_at"] = _now().isoformat()
                carried["carry_over"] = True
                carried["done"] = False
                carried["done_at"] = None
                carried["started_at"] = None
                carried["time_actual_min"] = None
                tomorrow_tasks.append(carried)
                self._save(tomorrow_tasks, tomorrow_key)
                return True
        return False

    def get_pending(self) -> list[dict[str, Any]]:
        return [t for t in self._load() if not t["done"]]

    def get_all(self) -> list[dict[str, Any]]:
        return self._load()

    def get_overdue_carryovers(self) -> list[dict[str, Any]]:
        """Tasks with hard Classroom deadlines that should auto carry over."""
        pending = self.get_pending()
        result = []
        for t in pending:
            if t.get("due") and t.get("classroom_id"):
                try:
                    due_dt = datetime.fromisoformat(t["due"])
                    if due_dt > _now():
                        result.append(t)
                except Exception:
                    pass
        return result

    def bulk_add_from_classroom(self, assignments: list[dict[str, Any]]) -> int:
        """Add assignments from Google Classroom, skipping duplicates."""
        tasks = self._load()
        existing_ids = {t.get("classroom_id") for t in tasks if t.get("classroom_id")}
        added = 0
        for a in assignments:
            cid = a.get("id")
            if cid and cid in existing_ids:
                continue
            self.add_task(
                title=a.get("title", "Untitled assignment"),
                source="classroom",
                due=a.get("due"),
                subject=a.get("subject"),
                classroom_id=cid,
                priority=_infer_priority(a),
            )
            added += 1
        return added


def _tomorrow_key() -> str:
    from datetime import timedelta
    now = _now()
    if now.hour < 4:
        now = now - timedelta(days=1)
    return (now + timedelta(days=1)).strftime("%Y-%m-%d")


def _infer_priority(assignment: dict) -> Priority:
    """Guess priority from due date proximity."""
    due_str = assignment.get("due")
    if not due_str:
        return "medium"
    try:
        due = datetime.fromisoformat(due_str)
        delta = (due - _now()).total_seconds() / 3600
        if delta < 24:
            return "high"
        if delta < 72:
            return "medium"
        return "low"
    except Exception:
        return "medium"
