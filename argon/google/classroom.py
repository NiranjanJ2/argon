"""Google Classroom tools — one tool per operation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from argon.google.service import LOCAL_TZ, GoogleAPITool


def classroom_due(coursework: dict) -> datetime | None:
    """Local due datetime of a courseWork item, or None if it has no deadline.

    Classroom reports ``dueDate``/``dueTime`` in **UTC**; reading them as local
    time shifts every deadline by the UTC offset (an 11:59 PM assignment lands
    on the following morning). With no ``dueTime`` the deadline is a date only,
    so it becomes end of the local day.
    """
    due_date = coursework.get("dueDate")
    if not due_date:
        return None
    due_time = coursework.get("dueTime")
    try:
        if due_time is None:
            return datetime(
                due_date["year"], due_date["month"], due_date["day"],
                23, 59, tzinfo=LOCAL_TZ,
            )
        return datetime(
            due_date["year"], due_date["month"], due_date["day"],
            due_time.get("hours", 0), due_time.get("minutes", 0),
            tzinfo=timezone.utc,
        ).astimezone(LOCAL_TZ)
    except (KeyError, TypeError, ValueError):
        return None


def _fmt_coursework(cw: dict, *, full_description: bool = False) -> dict:
    desc = cw.get("description") or None
    due = classroom_due(cw)
    return {
        "id": cw.get("id"),
        "course_id": cw.get("courseId"),
        "title": cw.get("title"),
        "description": desc if full_description or not desc else desc[:400],
        "due": due.isoformat() if due else None,
        "type": cw.get("workType"),
        "max_points": cw.get("maxPoints"),
        "state": cw.get("state"),
        "link": cw.get("alternateLink"),
    }


def _by_due(item: dict) -> str:
    """Sort key placing undated work last."""
    return item.get("due") or "9999"


def active_courses(svc) -> list[dict]:
    """Courses the student is currently enrolled in."""
    return svc.courses().list(
        studentId="me", courseStates=["ACTIVE"]
    ).execute().get("courses", [])


def upcoming_assignments(svc, days_ahead: int = 30) -> tuple[list[dict], list[str]]:
    """Published assignments due within *days_ahead*, plus any courses that failed.

    Shared by ``get_all_assignments`` and the daily overview so both agree on
    what "upcoming" means.
    """
    from googleapiclient.errors import HttpError

    now = datetime.now(LOCAL_TZ)
    cutoff = now + timedelta(days=days_ahead)

    assignments: list[dict] = []
    unreadable: list[str] = []
    for course in active_courses(svc):
        try:
            works = svc.courses().courseWork().list(
                courseId=course["id"],
                courseWorkStates=["PUBLISHED"],
                pageSize=50,
            ).execute().get("courseWork", [])
        except HttpError as exc:
            # One locked-down course must not blank out every other course.
            unreadable.append(f"{course.get('name', course['id'])}: {exc.reason}")
            continue
        for cw in works:
            due = classroom_due(cw)
            if due is None or not (now < due <= cutoff):
                continue
            item = _fmt_coursework(cw)
            item["course_name"] = course.get("name", "")
            assignments.append(item)

    assignments.sort(key=_by_due)
    return assignments, unreadable


class ClassroomTool(GoogleAPITool):
    """Shared config for every Classroom tool (school account, Classroom v1)."""

    api = "classroom"
    api_version = "v1"
    account = "school"

    @property
    def read_only(self) -> bool:
        return True


# ---------------------------------------------------------------------------

class GetCoursesTool(ClassroomTool):
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
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def _run(self, kwargs: dict[str, Any]) -> str:
        courses = [
            {
                "id": c["id"],
                "name": c.get("name"),
                "section": c.get("section"),
                "room": c.get("room"),
            }
            for c in active_courses(self._svc())
        ]
        return json.dumps(courses, indent=2)


# ---------------------------------------------------------------------------

class GetCourseAssignmentsTool(ClassroomTool):
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

    def _run(self, kwargs: dict[str, Any]) -> str:
        result = self._svc().courses().courseWork().list(
            courseId=kwargs["course_id"],
            courseWorkStates=["PUBLISHED"],
            pageSize=int(kwargs.get("limit", 20)),
        ).execute()
        items = sorted(
            (_fmt_coursework(cw) for cw in result.get("courseWork", [])), key=_by_due
        )
        return json.dumps(items, indent=2)


# ---------------------------------------------------------------------------

class GetAllAssignmentsTool(ClassroomTool):
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

    def _run(self, kwargs: dict[str, Any]) -> str:
        assignments, unreadable = upcoming_assignments(
            self._svc(), int(kwargs.get("days_ahead", 30))
        )
        payload: dict[str, Any] = {"count": len(assignments), "assignments": assignments}
        if unreadable:
            payload["courses_unreadable"] = unreadable
        return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------

class GetAssignmentInfoTool(ClassroomTool):
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

    def _run(self, kwargs: dict[str, Any]) -> str:
        from googleapiclient.errors import HttpError

        course_id = kwargs["course_id"]
        assignment_id = kwargs["assignment_id"]
        svc = self._svc()

        cw = svc.courses().courseWork().get(
            courseId=course_id, id=assignment_id
        ).execute()
        result = _fmt_coursework(cw, full_description=True)

        # Submission state is a bonus — report why it is absent, keep the rest.
        try:
            submissions = svc.courses().courseWork().studentSubmissions().list(
                courseId=course_id, courseWorkId=assignment_id, userId="me",
            ).execute().get("studentSubmissions", [])
        except HttpError as exc:
            result["submission_error"] = exc.reason
            submissions = []
        if submissions:
            sub = submissions[0]
            result["submission"] = {
                "state": sub.get("state"),
                "late": sub.get("late", False),
                "draft_grade": sub.get("draftGrade"),
                "assigned_grade": sub.get("assignedGrade"),
            }

        return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------

class GetCourseStreamTool(ClassroomTool):
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

    def _run(self, kwargs: dict[str, Any]) -> str:
        course_id = kwargs["course_id"]
        limit = int(kwargs.get("limit", 10))

        result = self._svc().courses().announcements().list(
            courseId=course_id, pageSize=limit
        ).execute()
        items = [
            {
                "type": "announcement",
                "id": ann.get("id"),
                "text": (ann.get("text") or "")[:600] or None,
                "created": ann.get("creationTime"),
                "updated": ann.get("updateTime"),
                "state": ann.get("state"),
                "link": ann.get("alternateLink"),
            }
            for ann in result.get("announcements", [])
        ]
        items.sort(key=lambda x: x.get("updated") or "", reverse=True)
        return json.dumps({"course_id": course_id, "stream": items[:limit]}, indent=2)
