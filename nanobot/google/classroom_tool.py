"""Google Classroom tools — one tool per operation."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nanobot.google.base import GoogleAPITool, build_google_service

_TZ = ZoneInfo("America/Los_Angeles")


def _fmt_due(due_date: dict | None, due_time: dict | None = None) -> str | None:
    if not due_date:
        return None
    try:
        h = (due_time or {}).get("hours", 23)
        mi = (due_time or {}).get("minutes", 59)
        return datetime(
            due_date["year"], due_date["month"], due_date["day"], h, mi, tzinfo=_TZ
        ).isoformat()
    except Exception:
        return None


def _fmt_coursework(cw: dict) -> dict:
    desc = cw.get("description", "")
    return {
        "id": cw.get("id"),
        "course_id": cw.get("courseId"),
        "title": cw.get("title"),
        "description": desc[:400] if desc else None,
        "due": _fmt_due(cw.get("dueDate"), cw.get("dueTime")),
        "type": cw.get("workType"),
        "max_points": cw.get("maxPoints"),
        "state": cw.get("state"),
        "link": cw.get("alternateLink"),
    }


# ---------------------------------------------------------------------------

class GetCoursesTool(GoogleAPITool):
    """List active Google Classroom courses (school account)."""

    @property
    def name(self) -> str:
        return "get_courses"

    @property
    def description(self) -> str:
        return (
            "List Niranjan's active Google Classroom courses. "
            "Returns course IDs needed for get_course_assignments, get_course_stream, etc."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def _run(self, kwargs: dict) -> str:
        svc = build_google_service(self._workspace, "classroom", "v1", "school")
        result = svc.courses().list(studentId="me", courseStates=["ACTIVE"]).execute()
        courses = [
            {
                "id": c["id"],
                "name": c.get("name"),
                "section": c.get("section"),
                "room": c.get("room"),
            }
            for c in result.get("courses", [])
        ]
        return json.dumps(courses, indent=2)


# ---------------------------------------------------------------------------

class GetCourseAssignmentsTool(GoogleAPITool):
    """Get assignments for a specific course."""

    @property
    def name(self) -> str:
        return "get_course_assignments"

    @property
    def description(self) -> str:
        return (
            "Get assignments for a specific Google Classroom course. "
            "Use get_courses first to get the course_id."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "course_id": {
                    "type": "string",
                    "description": "Course ID from get_courses.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max assignments to return (default 20).",
                },
            },
            "required": ["course_id"],
        }

    def _run(self, kwargs: dict) -> str:
        course_id = kwargs["course_id"]
        limit = int(kwargs.get("limit", 20))
        svc = build_google_service(self._workspace, "classroom", "v1", "school")
        result = svc.courses().courseWork().list(
            courseId=course_id, pageSize=limit
        ).execute()
        items = [_fmt_coursework(cw) for cw in result.get("courseWork", [])]
        items.sort(key=lambda x: x.get("due") or "9999")
        return json.dumps(items, indent=2)


# ---------------------------------------------------------------------------

class GetAllAssignmentsTool(GoogleAPITool):
    """Get all assignments due in the coming month across all courses."""

    @property
    def name(self) -> str:
        return "get_all_assignments"

    @property
    def description(self) -> str:
        return (
            "Fetch all Google Classroom assignments due in the coming month across every course. "
            "Results are sorted by due date. Use this to get a full picture of upcoming work."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "How many days ahead to look (default 30).",
                },
            },
            "required": [],
        }

    def _run(self, kwargs: dict) -> str:
        days_ahead = int(kwargs.get("days_ahead", 30))
        svc = build_google_service(self._workspace, "classroom", "v1", "school")

        today = date.today()
        cutoff = today + timedelta(days=days_ahead)

        courses_result = svc.courses().list(
            studentId="me", courseStates=["ACTIVE"]
        ).execute()
        courses = {
            c["id"]: c.get("name", "")
            for c in courses_result.get("courses", [])
        }

        assignments = []
        for course_id, course_name in courses.items():
            try:
                cw_result = svc.courses().courseWork().list(
                    courseId=course_id, pageSize=50
                ).execute()
                for cw in cw_result.get("courseWork", []):
                    due = cw.get("dueDate")
                    if not due:
                        continue
                    try:
                        due_date = date(due["year"], due["month"], due["day"])
                        if due_date < today or due_date > cutoff:
                            continue
                    except (KeyError, ValueError):
                        continue
                    item = _fmt_coursework(cw)
                    item["course_name"] = course_name
                    assignments.append(item)
            except Exception:
                continue

        assignments.sort(key=lambda x: x.get("due") or "9999")
        return json.dumps({"count": len(assignments), "assignments": assignments}, indent=2)


# ---------------------------------------------------------------------------

class GetAssignmentInfoTool(GoogleAPITool):
    """Get full details and submission status for a specific assignment."""

    @property
    def name(self) -> str:
        return "get_assignment_info"

    @property
    def description(self) -> str:
        return (
            "Get full details and submission status for a specific assignment. "
            "Requires course_id and assignment_id (from get_courses / get_course_assignments)."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "course_id": {
                    "type": "string",
                    "description": "The course ID.",
                },
                "assignment_id": {
                    "type": "string",
                    "description": "The assignment (coursework) ID.",
                },
            },
            "required": ["course_id", "assignment_id"],
        }

    def _run(self, kwargs: dict) -> str:
        course_id = kwargs["course_id"]
        assignment_id = kwargs["assignment_id"]
        svc = build_google_service(self._workspace, "classroom", "v1", "school")

        cw = svc.courses().courseWork().get(
            courseId=course_id, id=assignment_id
        ).execute()
        result = _fmt_coursework(cw)

        # Include full description without truncation
        result["description"] = cw.get("description")

        # Submission status
        try:
            subs = svc.courses().courseWork().studentSubmissions().list(
                courseId=course_id,
                courseWorkId=assignment_id,
                userId="me",
            ).execute()
            sub_list = subs.get("studentSubmissions", [])
            if sub_list:
                sub = sub_list[0]
                result["submission"] = {
                    "state": sub.get("state"),
                    "late": sub.get("late", False),
                    "draft_grade": sub.get("draftGrade"),
                    "assigned_grade": sub.get("assignedGrade"),
                }
        except Exception:
            pass

        return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------

class GetCourseStreamTool(GoogleAPITool):
    """Get recent announcements and posts from a course stream."""

    @property
    def name(self) -> str:
        return "get_course_stream"

    @property
    def description(self) -> str:
        return (
            "Get recent announcements and posts from a Google Classroom course stream. "
            "Use get_courses first to get the course_id."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "course_id": {
                    "type": "string",
                    "description": "The course ID.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max items to return (default 10).",
                },
            },
            "required": ["course_id"],
        }

    def _run(self, kwargs: dict) -> str:
        course_id = kwargs["course_id"]
        limit = int(kwargs.get("limit", 10))
        svc = build_google_service(self._workspace, "classroom", "v1", "school")

        items = []

        try:
            result = svc.courses().announcements().list(
                courseId=course_id, pageSize=limit
            ).execute()
            for ann in result.get("announcements", []):
                text = ann.get("text", "")
                items.append({
                    "type": "announcement",
                    "id": ann.get("id"),
                    "text": text[:600] if text else None,
                    "created": ann.get("creationTime"),
                    "updated": ann.get("updateTime"),
                    "state": ann.get("state"),
                    "link": ann.get("alternateLink"),
                })
        except Exception:
            pass

        items.sort(key=lambda x: x.get("updated") or "", reverse=True)
        return json.dumps({"course_id": course_id, "stream": items[:limit]}, indent=2)
