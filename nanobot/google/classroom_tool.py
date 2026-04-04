"""Google Classroom tool — read-only on the 'school' account."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.google.auth import GoogleAuth


def _build_service(workspace: Path):
    from googleapiclient.discovery import build
    auth = GoogleAuth(workspace)
    creds = auth.get_credentials("school")
    return build("classroom", "v1", credentials=creds)


class ClassroomTool(Tool):
    """Read Google Classroom (school account)."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "google_classroom"

    @property
    def description(self) -> str:
        return (
            "Read Google Classroom data on the school account. "
            "Actions: list_courses, list_coursework, list_announcements, list_submissions, list_students."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_courses", "list_coursework",
                        "list_announcements", "list_submissions", "list_students",
                    ],
                    "description": "Operation to perform.",
                },
                "course_id": {
                    "type": "string",
                    "description": "Course ID (required for all actions except list_courses).",
                },
                "coursework_id": {
                    "type": "string",
                    "description": "Coursework ID (required for list_submissions).",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Max results (default 20).",
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, self._run, kwargs)

    def _run(self, kwargs: dict[str, Any]) -> str:
        action = kwargs["action"]
        course_id = kwargs.get("course_id")
        page_size = kwargs.get("page_size", 20)
        svc = _build_service(self._workspace)

        if action == "list_courses":
            result = svc.courses().list(pageSize=page_size, studentId="me").execute()
            courses = [
                {
                    "id": c["id"],
                    "name": c.get("name"),
                    "section": c.get("section"),
                    "description": c.get("description"),
                    "room": c.get("room"),
                    "courseState": c.get("courseState"),
                }
                for c in result.get("courses", [])
            ]
            return json.dumps(courses, indent=2)

        if not course_id:
            return f"Error: course_id required for {action}."

        if action == "list_coursework":
            result = svc.courses().courseWork().list(
                courseId=course_id, pageSize=page_size
            ).execute()
            items = [_fmt_coursework(cw) for cw in result.get("courseWork", [])]
            return json.dumps(items, indent=2)

        if action == "list_announcements":
            result = svc.courses().announcements().list(
                courseId=course_id, pageSize=page_size
            ).execute()
            items = [
                {
                    "id": a["id"],
                    "text": a.get("text"),
                    "creationTime": a.get("creationTime"),
                    "updateTime": a.get("updateTime"),
                    "state": a.get("state"),
                }
                for a in result.get("announcements", [])
            ]
            return json.dumps(items, indent=2)

        if action == "list_submissions":
            cw_id = kwargs.get("coursework_id")
            if not cw_id:
                return "Error: coursework_id required for list_submissions."
            result = svc.courses().courseWork().studentSubmissions().list(
                courseId=course_id, courseWorkId=cw_id, userId="me"
            ).execute()
            items = [
                {
                    "id": s["id"],
                    "courseWorkId": s.get("courseWorkId"),
                    "state": s.get("state"),
                    "late": s.get("late"),
                    "draftGrade": s.get("draftGrade"),
                    "assignedGrade": s.get("assignedGrade"),
                }
                for s in result.get("studentSubmissions", [])
            ]
            return json.dumps(items, indent=2)

        if action == "list_students":
            result = svc.courses().students().list(
                courseId=course_id, pageSize=page_size
            ).execute()
            items = [
                {
                    "userId": s["userId"],
                    "name": s.get("profile", {}).get("name", {}).get("fullName"),
                    "email": s.get("profile", {}).get("emailAddress"),
                }
                for s in result.get("students", [])
            ]
            return json.dumps(items, indent=2)

        return f"Error: Unknown action '{action}'."


def _fmt_coursework(cw: dict) -> dict:
    return {
        "id": cw.get("id"),
        "title": cw.get("title"),
        "description": cw.get("description"),
        "state": cw.get("state"),
        "workType": cw.get("workType"),
        "dueDate": cw.get("dueDate"),
        "dueTime": cw.get("dueTime"),
        "maxPoints": cw.get("maxPoints"),
        "creationTime": cw.get("creationTime"),
        "updateTime": cw.get("updateTime"),
    }
