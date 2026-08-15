"""Google Classroom tools — one tool per operation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from argon.google.classroom_dispositions import ClassroomDispositionStore, assignment_key
from argon.google.service import LOCAL_TZ, GoogleAPITool


def classroom_due(coursework: dict) -> datetime | None:
    """Local due datetime of a courseWork item, or None if it has no deadline.

    Classroom reports ``dueDate``/``dueTime`` in **UTC**; reading them as local
    time shifts every deadline by the UTC offset (an 11:59 PM assignment lands
    on the following morning). With no ``dueTime`` there is no official instant;
    the local end-of-day value is only a work-by fallback.
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
            due_time.get("seconds", 0), due_time.get("nanos", 0) // 1000,
            tzinfo=timezone.utc,
        ).astimezone(LOCAL_TZ)
    except (KeyError, TypeError, ValueError):
        return None


def _fmt_coursework(cw: dict, *, full_description: bool = False) -> dict:
    from argon.utils.helpers import when_label

    desc = cw.get("description") or None
    due = classroom_due(cw)
    has_due_time = cw.get("dueTime") is not None
    due_value = (
        due.isoformat() if due and has_due_time
        else due.date().isoformat() if due
        else None
    )
    return {
        "id": cw.get("id"),
        "course_id": cw.get("courseId"),
        "classroom_key": assignment_key(str(cw.get("courseId", "")), str(cw.get("id", ""))),
        "title": cw.get("title"),
        "description": desc if full_description or not desc else desc[:400],
        "due": due_value,
        "due_when": when_label(due_value),
        "due_precision": "instant" if has_due_time else "work_by_day" if due else None,
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
    courses: list[dict] = []
    page_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"studentId": "me", "courseStates": ["ACTIVE"]}
        if page_token:
            kwargs["pageToken"] = page_token
        page = svc.courses().list(**kwargs).execute()
        courses.extend(page.get("courses", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            return courses


def upcoming_assignments(
    svc,
    days_ahead: int = 30,
    *,
    dispositions: ClassroomDispositionStore | None = None,
    include_suppressed: bool = False,
) -> tuple[list[dict], list[str]]:
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
            works: list[dict] = []
            page_token: str | None = None
            while True:
                kwargs: dict[str, Any] = {
                    "courseId": course["id"],
                    "courseWorkStates": ["PUBLISHED"],
                    "pageSize": 50,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                page = svc.courses().courseWork().list(**kwargs).execute()
                works.extend(page.get("courseWork", []))
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as exc:
            # One locked-down course must not blank out every other course.
            unreadable.append(f"{course.get('name', course['id'])}: {exc.reason}")
            continue
        for cw in works:
            due = classroom_due(cw)
            if due is None or not (now < due <= cutoff):
                continue
            item = _fmt_coursework(cw)
            item["course_id"] = course["id"]
            item["classroom_key"] = assignment_key(course["id"], str(cw.get("id", "")))
            item["course_name"] = course.get("name", "")
            # "done" suppresses exactly like "ignored". He said he finished it,
            # and a lot of coursework has nothing to submit, so waiting on a
            # submission state that will never arrive would keep nagging him
            # about work that is done. The reason is kept distinct because the
            # two decisions are different and he may want to undo either.
            if dispositions and (settled := dispositions.settled(item["classroom_key"])):
                if include_suppressed:
                    item["suppressed_reason"] = settled
                    assignments.append(item)
                continue
            try:
                submissions = svc.courses().courseWork().studentSubmissions().list(
                    courseId=course["id"], courseWorkId=cw["id"], userId="me", pageSize=1,
                ).execute().get("studentSubmissions", [])
            except Exception as exc:  # noqa: BLE001 — unknown must remain visible
                message = f"{type(exc).__name__}: {exc}"
                item["submission_error"] = message
                unreadable.append(f"{course.get('name', course['id'])} / {cw.get('id', '?')}: {message}")
            else:
                if submissions:
                    item["submission_state"] = submissions[0].get("state")
                else:
                    message = "No submission record returned"
                    item["submission_error"] = message
                    unreadable.append(
                        f"{course.get('name', course['id'])} / {cw.get('id', '?')}: {message}"
                    )
            if item.get("submission_state") in {"TURNED_IN", "RETURNED"}:
                if include_suppressed:
                    item["suppressed_reason"] = item["submission_state"]
                    assignments.append(item)
                continue
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
            self._svc(), int(kwargs.get("days_ahead", 30)),
            dispositions=ClassroomDispositionStore(self._workspace),
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
