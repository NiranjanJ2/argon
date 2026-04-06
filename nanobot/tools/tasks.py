"""Task management tools — individual tools over GoogleTasksStore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.google.tasks_store import GoogleTasksStore
from nanobot.daily.state import DailyState
from nanobot.daily.log import DailyLog
from nanobot.daily.habits import HabitsTracker


class ListTasksTool(Tool):
    """List all pending tasks from Google Tasks."""

    def __init__(self, store: GoogleTasksStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def description(self) -> str:
        return "List Niranjan's pending tasks, sorted by priority then due date."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        tasks = self._store.get_all()
        return json.dumps(tasks, indent=2)


class AddTaskTool(Tool):
    """Add a new task to Google Tasks."""

    def __init__(self, store: GoogleTasksStore, log: DailyLog) -> None:
        self._store = store
        self._log = log

    @property
    def name(self) -> str:
        return "add_task"

    @property
    def description(self) -> str:
        return "Add a new task to Niranjan's task list in Google Tasks."

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Default: medium.",
                },
                "due": {"type": "string", "description": "ISO 8601 due date/time."},
                "subject": {"type": "string", "description": "Class or project (e.g. 'AP Chemistry')."},
                "source": {
                    "type": "string",
                    "enum": ["manual", "classroom", "ucla", "club"],
                    "description": "Default: manual.",
                },
                "notes": {"type": "string"},
                "time_estimate_min": {
                    "type": "integer",
                    "description": "Estimated minutes to complete.",
                },
            },
            "required": ["title"],
        }

    async def execute(self, **kwargs: Any) -> str:
        title = kwargs["title"]
        task = self._store.add_task(
            title=title,
            priority=kwargs.get("priority", "medium"),
            due=kwargs.get("due"),
            subject=kwargs.get("subject"),
            source=kwargs.get("source", "manual"),
            notes=kwargs.get("notes"),
        )
        if kwargs.get("time_estimate_min"):
            self._store.set_time_estimate(task["id"], int(kwargs["time_estimate_min"]))
        self._log.append(f"Task added: {title}", tag="task")
        return f"Added: {title}"


class StartTaskTool(Tool):
    """Mark a task as started."""

    def __init__(self, store: GoogleTasksStore, state: DailyState, log: DailyLog) -> None:
        self._store = store
        self._state = state
        self._log = log

    @property
    def name(self) -> str:
        return "start_task"

    @property
    def description(self) -> str:
        return (
            "Mark a task as started. Records start time so duration is tracked on completion. "
            "Sets it as the current active task."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or partial title match."},
            },
            "required": ["task_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        task = self._store.start_task(kwargs["task_id"])
        if not task:
            return f"No task matching '{kwargs['task_id']}'."
        self._state.set_current_task(task["title"])
        self._log.log_task_started(task["title"])
        return f"Started: {task['title']}"


class CompleteTaskTool(Tool):
    """Mark a task as completed."""

    def __init__(
        self,
        store: GoogleTasksStore,
        state: DailyState,
        log: DailyLog,
        habits: HabitsTracker,
    ) -> None:
        self._store = store
        self._state = state
        self._log = log
        self._habits = habits

    @property
    def name(self) -> str:
        return "complete_task"

    @property
    def description(self) -> str:
        return "Mark a task as completed. Calculates time spent if the task was started."

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or partial title match."},
            },
            "required": ["task_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]

        # Capture priority rank before completing (for habit tracking)
        all_tasks = self._store.get_all()
        _p = {"high": 0, "medium": 1, "low": 2}
        sorted_tasks = sorted(all_tasks, key=lambda t: _p.get(t.get("priority", "medium"), 1))
        target = next(
            (t for t in all_tasks if t["id"] == task_id or task_id.lower() in t["title"].lower()),
            None,
        )
        priority_rank = next(
            (i + 1 for i, t in enumerate(sorted_tasks)
             if target and t["id"] == target["id"]),
            1,
        )

        completed = self._store.complete_task(task_id)
        if not completed:
            return f"No pending task matching '{task_id}'."

        title = completed["title"]
        actual_min = completed.get("time_actual_min")
        subject = completed.get("subject")

        if subject and actual_min:
            self._habits.record_task_completion(subject, actual_min, priority_rank)
        self._log.log_task_done(title, actual_min)
        self._state.set_current_task(None)

        return f"Done: {title}" + (f" ({actual_min}min)" if actual_min else "")


class UpdateTaskTool(Tool):
    """Update a task's priority or due date."""

    def __init__(self, store: GoogleTasksStore) -> None:
        self._store = store

    @property
    def name(self) -> str:
        return "update_task"

    @property
    def description(self) -> str:
        return (
            "Update a task's priority or due date. "
            "Set due to 'tomorrow' to push it to the next day."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID or partial title match."},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "due": {"type": "string", "description": "ISO 8601 date/time, or 'tomorrow'."},
            },
            "required": ["task_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        changes: list[str] = []

        if priority := kwargs.get("priority"):
            ok = self._store.update_priority(task_id, priority)
            if not ok:
                return f"No task matching '{task_id}'."
            changes.append(f"priority → {priority}")

        if due := kwargs.get("due"):
            if due.lower() == "tomorrow":
                ok = self._store.carry_over_task(task_id)
            else:
                ok = self._store.update_due(task_id, due)
            if not ok:
                return f"No task matching '{task_id}'."
            changes.append(f"due → {due}")

        if not changes:
            return "Provide at least one field to update: priority or due."
        return "Updated: " + ", ".join(changes)
