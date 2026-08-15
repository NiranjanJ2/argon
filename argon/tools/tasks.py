"""Task management tools — individual tools over GoogleTasksStore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argon.google.tasks_store import GoogleTasksStore, TaskResolutionAmbiguityError
from argon.paths import argon_home
from argon.productivity.habits import HabitsTracker
from argon.productivity.log import DailyLog
from argon.productivity.state import DailyState
from argon.tools.base import Tool, ToolResult

__all__ = ["mark_running", "mark_scheduled", "unscheduled"]


def _matches(task: dict[str, Any], summary: str) -> bool:
    """Does this agenda entry refer to this task?

    The reminder Argon writes is "Start Math homework" for a task titled "Math
    homework", so containment is the relation — in that direction only, or a
    task called "Prep" would match every reminder with the word in it.
    """
    title = (task.get("title") or "").strip().lower()
    return bool(title) and title in (summary or "").lower()


def mark_scheduled(
    tasks: list[dict[str, Any]], agenda: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Stamp tasks he has already committed to a time for.

    Deciding when to do something is doing something about it. Argon knew about
    the 3 PM reminder and about the task, but nothing connected them, so at noon
    it read "Math homework, outstanding" and told him to start it — arguing with
    a plan he had made two hours earlier and it had scheduled for him.
    """
    out = []
    for task in tasks:
        when = next(
            (e["start"] for e in agenda if _matches(task, e.get("summary", ""))), None
        )
        if when is not None:
            task = {**task, "scheduled_for": when.strftime("%-I:%M %p")}
        out.append(task)
    return out


def unscheduled(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the tasks with no time set aside for them yet."""
    return [t for t in tasks if not t.get("scheduled_for") and not t.get("running")]


def mark_running(
    tasks: list[dict[str, Any]], session: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Flag whichever task today's session is on.

    "Is this running?" is answered by the session, never by the task record.
    While the answer lived in Google Tasks metadata it had no day boundary, so
    a task started at 1:42 AM was still "in progress" two days later and
    ``list_tasks`` contradicted ``get_status`` in the same prompt.
    """
    if not session:
        return tasks
    task_id, title = session.get("task_id"), session.get("title")
    out = []
    for task in tasks:
        if task_id and task.get("id") == task_id or (not task_id and title
                                                     and task.get("title") == title):
            task = {**task, "running": True, "running_minutes": session.get("elapsed_min")}
        out.append(task)
    return out


class ListTasksTool(Tool):
    """List everything outstanding, from the one reconciled commitment board."""

    def __init__(
        self, store: GoogleTasksStore, state: DailyState, workspace: Path | None = None
    ) -> None:
        self._store = store
        self._state = state
        self._state_workspace = workspace or argon_home()

    @property
    def name(self) -> str:
        return "list_tasks"

    @property
    def description(self) -> str:
        return (
            "List Niranjan's outstanding commitments — Google Tasks and "
            "Classroom assignments already reconciled into one list, sorted by "
            "priority then due date. Anything turned in or ignored is gone. "
            "`official_due` is the school's deadline and `work_by` is the "
            "earlier date he set himself; they are different facts. Each entry "
            "carries days_overdue and days_open. Read them the way a "
            "secretary would: a task that has sat there for a week, or is days "
            "past its due date and still open, has usually stopped being real — "
            "it was finished and never ticked off, or quietly dropped. Ask him "
            "which, rather than listing it again as ordinary pending work. Use "
            "your judgement about when it is worth raising; there is no "
            "threshold that makes it true. If `complete` is false, one of the "
            "sources is down — say so rather than implying the list is all of it."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        from argon.commitments import load_board
        from argon.services import agenda

        board = load_board(self._state_workspace, store=self._store)
        rows = mark_running(board.as_dicts(), self._state.get_session())
        try:
            rows = mark_scheduled(rows, agenda.upcoming(self._state_workspace))
        except Exception:  # noqa: BLE001 — the list matters more than the stamp
            pass
        return json.dumps(
            {
                "commitments": rows,
                "sources": board.health_as_dicts(),
                "complete": board.complete,
            },
            indent=2,
        )


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
                "due": {"type": "string", "description": "Work-by date: YYYY-MM-DD."},
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
        return ToolResult(f"Added: {title}")


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
        try:
            task = self._store.start_task(kwargs["task_id"])
        except TaskResolutionAmbiguityError as exc:
            return ToolResult(str(exc), success=False)
        if not task:
            return ToolResult(f"No task matching '{kwargs['task_id']}'.", success=False)
        # Starting work is what puts him in "working" mode. Setting only the
        # task left mode on "idle", so the check-in gate kept classifying a
        # working afternoon as free time and interrupting it.
        self._state.start_session(
            kind="working", task_id=task["id"], title=task["title"]
        )
        self._log.log_task_started(task["title"])
        return ToolResult(f"Started: {task['title']}")


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

        # Only a session that was actually on this task can time it. Reading a
        # start stamp off the task record recorded 2921 minutes of English
        # study for a task left open across two nights, and averaged it into
        # the subject's habit stats.
        session = self._state.get_session()
        on_this_task = bool(session) and (
            (target and session.get("task_id") == target["id"])
            or session.get("title") == (target or {}).get("title")
        )
        actual_min = session.get("elapsed_min") if on_this_task else None

        try:
            completed = self._store.complete_task(task_id, actual_min=actual_min)
        except TaskResolutionAmbiguityError as exc:
            return ToolResult(str(exc), success=False)
        if not completed:
            return ToolResult(f"No pending task matching '{task_id}'.", success=False)

        title = completed["title"]
        subject = completed.get("subject")

        if subject and actual_min:
            self._habits.record_task_completion(subject, actual_min, priority_rank)
        self._log.log_task_done(title, actual_min)
        # Finishing the work ends the session. Leaving mode on "working" with
        # nothing running made the check-in gate take its mid-flow branch,
        # measure zero minutes, and stay silent for the rest of the day.
        if on_this_task:
            self._state.end_session_if_task(completed.get("id") or task_id, title=title)

        return ToolResult(f"Done: {title}" + (f" ({actual_min}min)" if actual_min else ""))


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
                "due": {"type": "string", "description": "Work-by date YYYY-MM-DD, or 'tomorrow'."},
            },
            "required": ["task_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        task_id = kwargs["task_id"]
        changes: list[str] = []

        if priority := kwargs.get("priority"):
            try:
                ok = self._store.update_priority(task_id, priority)
            except TaskResolutionAmbiguityError as exc:
                return ToolResult(str(exc), success=False)
            if not ok:
                return ToolResult(f"No task matching '{task_id}'.", success=False)
            changes.append(f"priority → {priority}")

        if due := kwargs.get("due"):
            try:
                if due.lower() == "tomorrow":
                    ok = self._store.carry_over_task(task_id)
                else:
                    ok = self._store.update_due(task_id, due)
            except TaskResolutionAmbiguityError as exc:
                return ToolResult(str(exc), success=False)
            if not ok:
                return ToolResult(f"No task matching '{task_id}'.", success=False)
            changes.append(f"due → {due}")

        if not changes:
            return ToolResult("Provide at least one field to update: priority or due.", success=False)
        return ToolResult("Updated: " + ", ".join(changes))
