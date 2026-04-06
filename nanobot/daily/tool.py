"""Daily tool — agent interface for todo, state, habits, and log.

Tasks are backed by Google Tasks (GoogleTasksStore) — no local JSON, no sync step.
State, habits, log, and memory remain workspace-local.
"""

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


class DailyTool(Tool):
    """Manage Niranjan's daily productivity session."""

    def __init__(self, workspace: Path, *, phone_number: str | None = None) -> None:
        self._workspace = workspace
        self._phone_number = phone_number
        self._state = DailyState(workspace)
        self._habits = HabitsTracker(workspace)
        self._log = DailyLog(workspace)
        self._memory = PersistentMemory(workspace)
        self._log.refresh_symlink()

        # Lazily initialized — requires Google auth (work account)
        self._todo: Any = None

    def _get_todo(self):
        """Return the GoogleTasksStore, initializing it on first use."""
        if self._todo is None:
            from nanobot.google.tasks_store import GoogleTasksStore
            self._todo = GoogleTasksStore(self._workspace)
        return self._todo

    @property
    def name(self) -> str:
        return "daily"

    @property
    def description(self) -> str:
        return (
            "Manage Niranjan's daily productivity session. "
            "Tasks are stored in Google Tasks (Argon task list) — no sync needed. "
            "To import Google Classroom assignments, call sync_classroom. "
            "Actions: get_state, get_todo, get_habits, read_daily_log, recall, "
            "sync_classroom, add_task, complete_task, start_task, carry_over_task, update_priority, "
            "set_mode, set_current_task, log_home_arrival, log_note, "
            "schedule_study_blocks, send_phone_keyword, remember, forget."
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
                        "get_state", "get_todo", "get_habits", "read_daily_log", "recall",
                        "sync_classroom",
                        "add_task", "complete_task", "start_task",
                        "carry_over_task", "update_priority",
                        "set_mode", "set_current_task",
                        "log_home_arrival", "log_note",
                        "schedule_study_blocks",
                        "send_phone_keyword",
                        "remember", "forget",
                    ],
                },
                "task_id": {"type": "string", "description": "Task ID or partial title match."},
                "title": {"type": "string"},
                "subject": {"type": "string", "description": "Subject/class (e.g. 'AP Chemistry')."},
                "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                "source": {"type": "string", "enum": ["classroom", "manual", "ucla", "club"]},
                "due": {"type": "string", "description": "ISO 8601 due datetime."},
                "notes": {"type": "string"},
                "time_estimate_min": {"type": "integer", "description": "Estimated minutes to complete."},
                "mode": {
                    "type": "string",
                    "enum": ["idle", "working", "napping", "lock_in", "done"],
                },
                "current_task": {"type": "string", "description": "Task title to set as current."},
                "note": {"type": "string", "description": "Note to append to daily log."},
                "memory_note": {"type": "string", "description": "Fact to store in persistent memory."},
                "memory_keyword": {"type": "string", "description": "Keyword to search/remove from memory."},
                "keyword": {
                    "type": "string",
                    "enum": ["lockdown", "unlock"],
                    "description": "Keyword to text to Niranjan's phone via WhatsApp.",
                },
            },
            "required": ["action"],
        }

    def build_context_snapshot(self) -> str:
        """Return a minimal daily context string for pre-injection into the runtime context.

        Intentionally small — just enough for the agent to know Niranjan's current
        state without pre-loading the full todo list or log on every message.
        Call daily(action="get_todo") / daily(action="read_daily_log") for full data.
        """
        state = self._state.get()
        work_min = self._state.get_work_session_duration_minutes()

        parts = [f"Mode: {state.get('mode', 'idle')}"]
        if state.get("current_task"):
            parts.append(f"Current task: {state['current_task']}")
        if work_min:
            parts.append(f"Working {work_min}min this session")

        return "[Daily Context]\n" + " | ".join(parts)

    def _push(self, event: str) -> None:
        try:
            from nanobot.dashboard.app import push_update
            push_update(event)
        except Exception:
            pass

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs["action"]

        # ── Read operations ──────────────────────────────────────────────
        if action == "get_todo":
            tasks = self._get_todo().get_all()
            return json.dumps({"tasks": tasks, "total": len(tasks)}, indent=2)

        if action == "get_state":
            state = self._state.get()
            work_min = self._state.get_work_session_duration_minutes()
            lock_min = self._state.get_lock_in_duration_minutes()
            return json.dumps({
                **state,
                "work_session_duration_minutes": work_min,
                "lock_in_duration_minutes": lock_min,
            }, indent=2)

        if action == "get_habits":
            return json.dumps(self._habits.get_summary(), indent=2)

        if action == "read_daily_log":
            return self._log.read()

        if action == "recall":
            return self._memory.recall()

        # ── Task operations ──────────────────────────────────────────────
        if action == "add_task":
            title = kwargs.get("title")
            if not title:
                return "Error: title required."
            task = self._get_todo().add_task(
                title=title,
                source=kwargs.get("source", "manual"),
                priority=kwargs.get("priority", "medium"),
                due=kwargs.get("due"),
                subject=kwargs.get("subject"),
                notes=kwargs.get("notes"),
            )
            if kwargs.get("time_estimate_min"):
                self._get_todo().set_time_estimate(task["id"], kwargs["time_estimate_min"])
            self._push("todo")
            return f"Added: {title}"

        if action == "complete_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required."

            # Capture priority rank before completing (for habit recording)
            all_tasks = self._get_todo().get_all()
            _p = {"high": 0, "medium": 1, "low": 2}
            pending_sorted = sorted(all_tasks, key=lambda t: _p.get(t.get("priority", "medium"), 1))
            target_pre = next(
                (t for t in all_tasks if t["id"] == task_id or task_id.lower() in t["title"].lower()),
                None,
            )
            priority_rank = next(
                (i + 1 for i, t in enumerate(pending_sorted)
                 if t["id"] == (target_pre["id"] if target_pre else "")),
                1,
            )

            completed = self._get_todo().complete_task(task_id)
            if not completed:
                return f"No pending task matching '{task_id}'."

            title = completed["title"]
            actual_min = completed.get("time_actual_min")
            subject = completed.get("subject")

            if subject and actual_min:
                self._habits.record_task_completion(subject, actual_min, priority_rank)
            self._log.log_task_done(title, actual_min)
            self._state.set_current_task(None)
            self._push("todo")
            self._push("state")
            return f"Completed: {title}" + (f" ({actual_min} min)" if actual_min else "")

        if action == "start_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required."
            task = self._get_todo().start_task(task_id)
            if not task:
                return f"No task matching '{task_id}'."
            self._state.set_current_task(task["title"])
            self._log.log_task_started(task["title"])
            self._push("state")
            return f"Started: {task['title']}"

        if action == "carry_over_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required."
            ok = self._get_todo().carry_over_task(task_id)
            if ok:
                self._push("todo")
            return "Due date pushed to tomorrow." if ok else f"Task '{task_id}' not found."

        if action == "update_priority":
            task_id = kwargs.get("task_id")
            priority = kwargs.get("priority")
            if not task_id or not priority:
                return "Error: task_id and priority required."
            ok = self._get_todo().update_priority(task_id, priority)
            if ok:
                self._push("todo")
            return "Priority updated." if ok else f"Task '{task_id}' not found."

        # ── State operations ─────────────────────────────────────────────
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
            return f"Current task: {task or '(none)'}"

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

        # ── Classroom import ─────────────────────────────────────────────
        if action == "sync_classroom":
            return await self._sync_classroom_to_todo()

        # ── Study blocks ─────────────────────────────────────────────────
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

        # ── Persistent memory ────────────────────────────────────────────
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

        # ── Phone keyword (lockdown / unlock) ────────────────────────────
        if action == "send_phone_keyword":
            keyword = kwargs.get("keyword", "").strip().lower()
            if keyword not in ("lockdown", "unlock"):
                return "Error: keyword must be 'lockdown' or 'unlock'."
            if not self._phone_number:
                return "Error: phone_number not configured."
            return await self._send_whatsapp_keyword(self._phone_number, keyword)

        return f"Error: Unknown action '{action}'."

    async def _sync_classroom_to_todo(self) -> str:
        """Fetch all Google Classroom coursework and import new items into Google Tasks."""
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, self._sync_classroom_sync)

    def _sync_classroom_sync(self) -> str:
        from googleapiclient.discovery import build
        from nanobot.google.auth import GoogleAuth
        from nanobot.google.tasks_store import _parse_classroom_due, _infer_priority

        try:
            auth = GoogleAuth(self._workspace)
            creds = auth.get_credentials("school")
            svc = build("classroom", "v1", credentials=creds)
        except Exception as e:
            return f"Error: Google Classroom not authenticated — {e}"

        try:
            courses = svc.courses().list(pageSize=20, studentId="me").execute().get("courses", [])
        except Exception as e:
            return f"Error fetching courses: {e}"

        from datetime import date, timedelta
        today = date.today()
        cutoff = today + timedelta(days=30)

        all_assignments = []
        for course in courses:
            try:
                for cw in svc.courses().courseWork().list(
                    courseId=course["id"], pageSize=30
                ).execute().get("courseWork", []):
                    due = cw.get("dueDate")
                    if due:
                        try:
                            due_date = date(due["year"], due["month"], due["day"])
                            if due_date < today or due_date > cutoff:
                                continue
                        except (KeyError, ValueError):
                            continue
                    else:
                        continue
                    all_assignments.append({
                        "id": cw.get("id"),
                        "title": cw.get("title"),
                        "dueDate": due,
                        "dueTime": cw.get("dueTime"),
                        "course_name": course.get("name"),
                    })
            except Exception:
                continue

        if not all_assignments:
            return "No upcoming assignments (due within 30 days) found in Google Classroom."

        added = self._get_todo().bulk_add_from_classroom(all_assignments)
        self._push("todo")
        return f"Synced {added} new assignments from {len(courses)} courses (due within 30 days)."

    async def _send_whatsapp_keyword(self, phone: str, keyword: str) -> str:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.post("http://127.0.0.1:3996/send", json={"to": phone, "body": keyword})
            if resp.status_code == 200:
                self._log.log_note(f"Phone keyword sent: {keyword}")
                return f"Sent '{keyword}' to phone."
            return f"Bridge returned HTTP {resp.status_code}: {resp.text}"
        except Exception as e:
            return f"Error sending to phone: {e}"
