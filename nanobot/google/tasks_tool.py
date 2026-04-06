"""Google Tasks tool — read/write on the 'work' account."""

from __future__ import annotations

import json
from typing import Any

from nanobot.google.base import GoogleAPITool, build_google_service


class TasksTool(GoogleAPITool):
    """Interact with Google Tasks (work account)."""

    @property
    def name(self) -> str:
        return "google_tasks"

    @property
    def description(self) -> str:
        return (
            "Read and write Google Tasks on the work account. "
            "Actions: list_tasklists, list_tasks, get_task, create_task, update_task, complete_task, delete_task."
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
                        "list_tasklists", "list_tasks", "get_task",
                        "create_task", "update_task", "complete_task", "delete_task",
                    ],
                    "description": "Operation to perform.",
                },
                "tasklist_id": {
                    "type": "string",
                    "description": "Task list ID. Defaults to '@default'.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (required for get/update/complete/delete).",
                },
                "title": {
                    "type": "string",
                    "description": "Task title (for create/update).",
                },
                "notes": {
                    "type": "string",
                    "description": "Task notes/description.",
                },
                "due": {
                    "type": "string",
                    "description": "Due date in RFC 3339 format (e.g. '2025-12-31T00:00:00.000Z').",
                },
                "show_completed": {
                    "type": "boolean",
                    "description": "Include completed tasks in list_tasks (default false).",
                },
            },
            "required": ["action"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        action = kwargs["action"]
        tl_id = kwargs.get("tasklist_id", "@default")
        svc = build_google_service(self._workspace, "tasks", "v1", "work")

        if action == "list_tasklists":
            result = svc.tasklists().list().execute()
            items = [{"id": t["id"], "title": t.get("title", "")} for t in result.get("items", [])]
            return json.dumps(items, indent=2)

        if action == "list_tasks":
            show_completed = kwargs.get("show_completed", False)
            result = svc.tasks().list(
                tasklist=tl_id,
                showCompleted=show_completed,
                showHidden=show_completed,
            ).execute()
            tasks = [_fmt_task(t) for t in result.get("items", [])]
            return json.dumps(tasks, indent=2)

        if action == "get_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required for get_task."
            task = svc.tasks().get(tasklist=tl_id, task=task_id).execute()
            return json.dumps(_fmt_task(task), indent=2)

        if action == "create_task":
            title = kwargs.get("title")
            if not title:
                return "Error: title required for create_task."
            body: dict[str, Any] = {"title": title}
            if kwargs.get("notes"):
                body["notes"] = kwargs["notes"]
            if kwargs.get("due"):
                body["due"] = kwargs["due"]
            task = svc.tasks().insert(tasklist=tl_id, body=body).execute()
            return f"Created task: {task.get('id')} — {task.get('title', '')}"

        if action == "update_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required for update_task."
            body = {}
            if kwargs.get("title"):
                body["title"] = kwargs["title"]
            if kwargs.get("notes") is not None:
                body["notes"] = kwargs["notes"]
            if kwargs.get("due"):
                body["due"] = kwargs["due"]
            task = svc.tasks().patch(tasklist=tl_id, task=task_id, body=body).execute()
            return f"Updated task: {task.get('id')} — {task.get('title', '')}"

        if action == "complete_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required for complete_task."
            task = svc.tasks().patch(
                tasklist=tl_id, task=task_id, body={"status": "completed"}
            ).execute()
            return f"Completed task: {task.get('id')} — {task.get('title', '')}"

        if action == "delete_task":
            task_id = kwargs.get("task_id")
            if not task_id:
                return "Error: task_id required for delete_task."
            svc.tasks().delete(tasklist=tl_id, task=task_id).execute()
            return f"Deleted task {task_id}."

        return f"Error: Unknown action '{action}'."


def _fmt_task(t: dict) -> dict:
    return {
        "id": t.get("id"),
        "title": t.get("title"),
        "notes": t.get("notes"),
        "status": t.get("status"),
        "due": t.get("due"),
        "completed": t.get("completed"),
        "parent": t.get("parent"),
    }
