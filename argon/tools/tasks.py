"""Task management tools — individual tools over GoogleTasksStore."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from argon.google.tasks_store import GoogleTasksStore, TaskResolutionAmbiguityError
from argon.ios import mode as ios_mode
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
        # Say so rather than reporting a second copy as a new task. Two rows for
        # one piece of work get counted, reminded on and reported separately,
        # and he is the one who has to notice.
        if task.get("already_existed"):
            when = task.get("due_when") or task.get("due") or "no date"
            return ToolResult(
                f"Already on the list: {task['title']} (due {when}) — not added again."
            )
        self._log.append(f"Task added: {title}", tag="task")
        return ToolResult(f"Added: {title}")


#: The block lasts until he says he is done, which cannot be expressed as an
#: open-ended block — the phone refuses one, because a hard block with no expiry
#: never lifts if Argon dies. It is a rolling window instead: renewed on every
#: check-in while the task is still running, so it behaves as "until completion"
#: while keeping a ceiling on a block nobody is renewing.
#:
#: This is also the maximum a block can outlive Argon or a phone that has gone
#: quiet. Long enough not to interrupt real work, short enough to be a recovery.
TASK_FOCUS_WINDOW_MIN = 120
#: Marks a block as belonging to a task, so finishing that task can clear it and
#: finishing any *other* task cannot.
AUTO_FOCUS_SOURCE = "task"


def overlay_for_classroom(store: GoogleTasksStore, task_id: str) -> dict[str, Any] | None:
    """Give a Classroom assignment the Google Task it needs to be worked on.

    The board shows assignments directly, because Classroom owns the title and
    the deadline and copying them wholesale creates a second owner that goes
    stale the moment either changes. The cost is that an assignment has nowhere
    to record "started 4:10 PM, took 35 minutes" — those facts live on a task,
    and every commitment on the board can be Classroom-only, which is why
    starting anything returned "task not found".

    So the overlay is created at the moment he actually starts one. That is
    precisely the case the design reserves it for — "a task he wants to
    schedule" — rather than the bulk projection it forbids. ``add_task``
    deduplicates on ``classroom_key``, so this cannot produce a second row.

    Returns None when the id is not a Classroom commitment, leaving the caller's
    existing "no such task" answer intact.
    """
    from argon.commitments import load_board

    board = load_board(store.workspace, store=store)
    match = next(
        (c for c in board.as_dicts()
         if c.get("id") == task_id and c.get("source") == "classroom"),
        None,
    )
    if not match or match.get("google_task_id"):
        return None

    created = store.add_task(
        title=match.get("title") or "Untitled assignment",
        source="classroom",
        priority=match.get("priority") or "medium",
        # The work-by date if he set one, else the school's deadline — `due` is
        # already that derived value, so it does not need recomputing here.
        due=match.get("due"),
        subject=match.get("subject"),
        classroom_id=match.get("classroom_id"),
        classroom_key=match.get("classroom_key"),
        official_due=match.get("official_due"),
    )
    logger.info("Created a Classroom overlay so {!r} could be started", match.get("title"))
    return created


def settle_classroom_done(store: GoogleTasksStore, completed: dict[str, Any]) -> None:
    """Record that he finished a Classroom assignment. Never raises.

    Completing the overlay alone is not enough to make it stay gone: the board
    reads pending tasks, so a completed overlay simply stops existing and the
    assignment reappears underneath it, unfinished, on the next sync.

    Classroom cannot settle it either. Plenty of coursework has nothing to turn
    in — read chapter 2, study for the quiz — so its submission state stays
    "not turned in" forever and the item would nag him about work he has done.
    His word is the authority here, so it is written down as one.
    """
    key = completed.get("classroom_key")
    if not key:
        return
    try:
        from argon.google.classroom_dispositions import ClassroomDispositionStore

        ClassroomDispositionStore(store.workspace).complete(key)
    except Exception as exc:  # noqa: BLE001 — the task is already completed
        logger.warning("Could not settle Classroom assignment {}: {}", key, exc)


def engage_task_focus(task: dict[str, Any]) -> str | None:
    """Shield the phone for a task that just started. Never raises.

    Best effort on purpose: an unreachable phone, an emergency override or a
    Screen Time failure must not stop the task from being marked started. The
    task record is the thing worth keeping — the block is a convenience on top.
    """
    try:
        ios_mode.set_mode(
            "lock_in",
            duration_min=TASK_FOCUS_WINDOW_MIN,
            reason=f"working on {task.get('title') or 'a task'}",
            source=AUTO_FOCUS_SOURCE,
        )
    except ios_mode.OverrideActive:
        return None  # He pulled the release. Do not argue with it.
    except Exception as exc:  # noqa: BLE001 - never fail a start over the phone
        logger.warning("Auto-focus on task start failed: {}", exc)
        return None
    return "phone shielded until you mark it done"


def renew_task_focus() -> None:
    """Keep a running task's block alive. Never raises.

    Called on every status read. Estimates are deliberately not consulted: a
    block that expires because the work took longer than guessed is worse than
    useless, because it lifts precisely when the task is dragging and the
    distraction is most tempting.
    """
    try:
        ios_mode.renew(TASK_FOCUS_WINDOW_MIN, source=AUTO_FOCUS_SOURCE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Renewing task focus failed: {}", exc)


def release_task_focus(task_id: str | None = None) -> None:
    """Clear a block that a task start imposed, if that is what is running.

    Only clears blocks tagged ``task``. A lock Niranjan asked for himself, or a
    weekend allowance, survives finishing a task — otherwise ticking something
    off would quietly unlock the phone for the rest of the evening.
    """
    try:
        current = ios_mode.get_mode()
        if current.get("mode") != "off" and current.get("source") == AUTO_FOCUS_SOURCE:
            ios_mode.set_mode("off", reason="task finished")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Releasing task focus failed: {}", exc)


class StartTaskTool(Tool):
    """Mark a task as started."""

    def __init__(
        self,
        store: GoogleTasksStore,
        state: DailyState,
        log: DailyLog,
        auto_focus: bool = True,
    ) -> None:
        self._store = store
        self._state = state
        self._log = log
        self._auto_focus = auto_focus

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
                "focus": {
                    "type": "boolean",
                    "description": (
                        "Shield the phone for this task. Defaults to true. Pass false "
                        "only when he said he needs his phone for the work itself."
                    ),
                },
            },
            "required": ["task_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        try:
            task = self._store.start_task(kwargs["task_id"])
            if not task:
                # Probably a Classroom assignment with no overlay yet: it is on
                # the board and looks startable, but there is no task record to
                # start. Make one, then start that.
                if overlay := overlay_for_classroom(self._store, kwargs["task_id"]):
                    task = self._store.start_task(overlay["id"])
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

        note = None
        if self._auto_focus and kwargs.get("focus", True):
            note = engage_task_focus(task)
        return ToolResult(f"Started: {task['title']}" + (f" — {note}" if note else ""))


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
            if not completed:
                # A Classroom assignment he never started, so it has no task to
                # complete. Give it one and complete that, so the work is on the
                # record rather than vanishing.
                if overlay := overlay_for_classroom(self._store, task_id):
                    completed = self._store.complete_task(overlay["id"], actual_min=actual_min)
        except TaskResolutionAmbiguityError as exc:
            return ToolResult(str(exc), success=False)
        if not completed:
            return ToolResult(f"No pending task matching '{task_id}'.", success=False)

        settle_classroom_done(self._store, completed)

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
            # Only the block this task raised. Finishing homework should not
            # unlock a phone he locked himself, nor end a weekend allowance.
            release_task_focus(completed.get("id") or task_id)

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
