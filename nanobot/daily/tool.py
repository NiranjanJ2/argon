"""Daily tool — agent interface for todo, state, habits, and log."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.daily.habits import HabitsTracker
from nanobot.daily.log import DailyLog
from nanobot.daily.memory import PersistentMemory
from nanobot.daily.state import DailyState
from nanobot.daily.todo import DailyTodo


class DailyTool(Tool):
    """Manage Niranjan's daily todo list, session state, habit tracking, and persistent memory."""

    def __init__(self, workspace: Path, *, phone_number: str | None = None) -> None:
        self._workspace = workspace
        self._phone_number = phone_number
        self._todo = DailyTodo(workspace)
        self._state = DailyState(workspace)
        self._habits = HabitsTracker(workspace)
        self._log = DailyLog(workspace)
        self._memory = PersistentMemory(workspace)
        self._log.refresh_symlink()

    @property
    def name(self) -> str:
        return "daily"

    @property
    def description(self) -> str:
        return (
            "Manage Niranjan's daily productivity session. "
            "Actions: get_todo, add_task, complete_task, start_task, carry_over_task, update_priority, "
            "get_state, set_mode, set_current_task, log_home_arrival, log_note, "
            "get_habits, add_from_classroom, read_daily_log, sync_google_tasks, schedule_study_blocks, "
            "send_phone_keyword, remember, recall, forget. "
            "remember/recall/forget manage persistent long-term memory that survives across days. "
            "send_phone_keyword with keyword='lockdown' or 'unlock' toggles phone app restrictions."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "get_todo", "add_task", "complete_task", "start_task",
                        "carry_over_task", "update_priority",
                        "get_state", "set_mode", "set_current_task",
                        "log_home_arrival", "log_note",
                        "get_habits", "add_from_classroom", "read_daily_log",
                        "sync_google_tasks", "schedule_study_blocks",
                        "send_phone_keyword",
                        "remember", "recall", "forget",
                    ],
                },
                # Task fields
                "task_id": {"type": "string", "description": "Task ID or partial title match."},
                "title": {"type": "string"},
                "subject": {"type": "string", "description": "Subject/class (e.g. 'AP Chemistry')."},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "source": {"type": "string", "enum": ["classroom", "manual", "ucla", "club"]},
                "due": {"type": "string", "description": "ISO 8601 due datetime."},
                "notes": {"type": "string"},
                "time_estimate_min": {"type": "integer", "description": "Estimated minutes to complete."},
                # State fields
                "mode": {
                    "type": "string",
                    "enum": ["idle", "working", "napping", "lock_in", "done"],
                },
                "current_task": {"type": "string", "description": "Task title to set as current."},
                # Log
                "note": {"type": "string", "description": "Note to append to daily log."},
                # Memory
                "memory_note": {"type": "string", "description": "Fact or note to store in persistent memory."},
                "memory_keyword": {"type": "string", "description": "Keyword to search/remove from persistent memory."},
                # Phone keyword
                "keyword": {
                    "type": "string",
                    "enum": ["lockdown", "unlock"],
                    "description": "Keyword to text to Niranjan's phone via WhatsApp.",
                },
                # Classroom bulk import
                "assignments": {
                    "type": "array",
                    "description": "List of assignment dicts from Google Classroom.",
                    "items": {"type": "object"},
                },
            },
            "required": ["action"],
        }

    def _push(self, event: str) -> None:
        try:
            from nanobot.dashboard.app import push_update
            push_update(event)
        except Exception:
            pass

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs["action"]

        # ── Todo operations ──────────────────────────────────────────────
        if action == "get_todo":
            tasks = self._todo.get_all()
            pending = [t for t in tasks if not t["done"]]
            done = [t for t in tasks if t["done"]]
            return json.dumps({"pending": pending, "done": done, "total": len(tasks)}, indent=2)

        if action == "add_task":
            title = kwargs.get("title")
            if not title:
                return "Error: title required."
            task = self._todo.add_task(
                title=title,
                source=kwargs.get("source", "manual"),
                priority=kwargs.get("priority", "medium"),
                due=kwargs.get("due"),
                subject=kwargs.get("subject"),
                notes=kwargs.get("notes"),
            )
            if kwargs.get("time_estimate_min"):
                self._todo.set_time_estimate(task["id"], kwargs["time_estimate_min"])
            self._push("todo")
            return f"Added task [{task['id']}]: {title}"

        if action == "complete_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required."
            # Look up subject for habit recording before completing
            all_tasks = self._todo.get_all()
            subject = next((t.get("subject") for t in all_tasks
                           if t["id"] == task_id or task_id.lower() in t["title"].lower()), None)
            _priority_order = {"high": 0, "medium": 1, "low": 2}
            priority_rank = next((i + 1 for i, t in enumerate(
                sorted([x for x in all_tasks if not x["done"]],
                       key=lambda x: _priority_order.get(x.get("priority", "medium"), 1))
            ) if t["id"] == task_id or task_id.lower() in t["title"].lower()), 1)

            completed_id = self._todo.complete_task(task_id)
            if not completed_id:
                return f"No pending task matching '{task_id}'."
            task = next((t for t in self._todo.get_all() if t["id"] == completed_id), None)
            actual_min = task.get("time_actual_min") if task else None
            if subject and actual_min:
                self._habits.record_task_completion(subject, actual_min, priority_rank)
            title = task["title"] if task else task_id
            self._log.log_task_done(title, actual_min)
            self._state.set_current_task(None)
            # Mirror completion to Google Tasks if synced
            if task and task.get("google_task_id"):
                try:
                    from nanobot.google.sync import complete_in_google_tasks
                    complete_in_google_tasks(self._workspace, task["google_task_id"])
                except Exception:
                    pass
            self._push("todo")
            self._push("state")
            return f"Completed: {title}" + (f" ({actual_min} min)" if actual_min else "")

        if action == "start_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required."
            tid = self._todo.start_task(task_id)
            if not tid:
                return f"No task matching '{task_id}'."
            task = next((t for t in self._todo.get_all() if t["id"] == tid), None)
            title = task["title"] if task else task_id
            self._state.set_current_task(title)
            self._log.log_task_started(title)
            self._push("state")
            return f"Started: {title}"

        if action == "carry_over_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required."
            ok = self._todo.carry_over_task(task_id)
            if ok: self._push("todo")
            return f"Carried over to tomorrow." if ok else f"Task '{task_id}' not found."

        if action == "update_priority":
            task_id = kwargs.get("task_id")
            priority = kwargs.get("priority")
            if not task_id or not priority:
                return "Error: task_id and priority required."
            ok = self._todo.update_priority(task_id, priority)
            if ok: self._push("todo")
            return "Priority updated." if ok else f"Task '{task_id}' not found."

        # ── State operations ─────────────────────────────────────────────
        if action == "get_state":
            state = self._state.get()
            work_min = self._state.get_work_session_duration_minutes()
            lock_min = self._state.get_lock_in_duration_minutes()
            return json.dumps({
                **state,
                "work_session_duration_minutes": work_min,
                "lock_in_duration_minutes": lock_min,
            }, indent=2)

        if action == "set_mode":
            mode = kwargs.get("mode")
            if not mode:
                return "Error: mode required."
            self._state.set_mode(mode)
            self._log.log_mode_change(mode)
            if mode == "working":
                self._habits.record_work_start()
            self._push("state")
            return f"Mode set to '{mode}'."

        if action == "set_current_task":
            task = kwargs.get("current_task")
            self._state.set_current_task(task)
            if task:
                self._log.log_task_started(task)
            self._push("state")
            return f"Current task set to: {task or '(none)'}"

        if action == "log_home_arrival":
            self._state.set_home_arrival()
            self._log.log_home_arrival()
            self._push("state")
            return "Home arrival logged."

        if action == "log_note":
            note = kwargs.get("note", "")
            if not note:
                return "Error: note required."
            self._state.add_note(note)
            self._log.log_note(note)
            return "Note logged."

        # ── Habits ──────────────────────────────────────────────────────
        if action == "get_habits":
            return json.dumps(self._habits.get_summary(), indent=2)

        # ── Classroom import ─────────────────────────────────────────────
        if action == "add_from_classroom":
            assignments = kwargs.get("assignments", [])
            if not assignments:
                return "No assignments provided."
            added = self._todo.bulk_add_from_classroom(assignments)
            self._push("todo")
            return f"Added {added} new assignments from Google Classroom."

        # ── Log ──────────────────────────────────────────────────────────
        if action == "read_daily_log":
            return self._log.read()

        # ── Google sync ──────────────────────────────────────────────────
        if action == "sync_google_tasks":
            from nanobot.google.sync import sync_tasks
            result = sync_tasks(self._workspace)
            if "error" in result:
                return f"Error: {result['error']}"
            self._push("todo")
            return (
                f"Sync complete — pushed {result['pushed']} new tasks to Google Tasks, "
                f"pulled {result['completed_from_gt']} completions, "
                f"{result['already_synced']} already in sync."
            )

        if action == "schedule_study_blocks":
            from nanobot.google.sync import schedule_study_blocks
            state = self._state.get()
            arrival = None
            if state.get("home_arrival"):
                try:
                    arrival = datetime.fromisoformat(state["home_arrival"])
                except Exception:
                    pass
            result = schedule_study_blocks(self._workspace, arrival_time=arrival)
            if "error" in result:
                return f"Error: {result['error']}"
            if result.get("message"):
                return result["message"]
            return (
                f"Scheduled {result['created']} study blocks on Google Calendar"
                + (f" ({result['skipped']} tasks skipped — no time estimate)." if result["skipped"] else ".")
            )

        # ── Persistent memory ────────────────────────────────────────────────
        if action == "remember":
            note = kwargs.get("memory_note", "").strip()
            if not note:
                return "Error: memory_note required."
            self._memory.remember(note)
            return f"Remembered: {note}"

        if action == "recall":
            return self._memory.recall()

        if action == "forget":
            keyword = kwargs.get("memory_keyword", "").strip()
            if not keyword:
                return "Error: memory_keyword required."
            removed = self._memory.forget(keyword)
            return f"Removed {removed} memory entry/entries matching '{keyword}'."

        # ── Phone keyword (lockdown / unlock) ────────────────────────────────
        if action == "send_phone_keyword":
            keyword = kwargs.get("keyword", "").strip().lower()
            if keyword not in ("lockdown", "unlock"):
                return "Error: keyword must be 'lockdown' or 'unlock'."
            if not self._phone_number:
                return "Error: phone_number not configured — add phoneNumber to channels.whatsapp in config.json."
            return await self._send_whatsapp_keyword(self._phone_number, keyword)

        return f"Error: Unknown action '{action}'."

    async def _send_whatsapp_keyword(self, phone: str, keyword: str) -> str:
        """Send a keyword text to Niranjan's phone via the local WhatsApp bridge."""
        import httpx
        bridge_url = "http://127.0.0.1:3996/send"
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post(bridge_url, json={"to": phone, "body": keyword})
            if resp.status_code == 200:
                self._log.log_note(f"Phone keyword sent: {keyword}")
                return f"Sent '{keyword}' to phone."
            return f"Bridge returned HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return f"Error sending to phone: {e}"
