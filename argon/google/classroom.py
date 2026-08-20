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


#: Posts older than this are history, not homework.
POSTS_LOOKBACK_DAYS = 7


def _post_text(item: dict) -> str:
    """The readable body of an announcement or a material post."""
    text = (item.get("text") or item.get("description") or "").strip()
    title = (item.get("title") or "").strip()
    if title and title.lower() not in text.lower():
        text = f"{title}\n{text}".strip()
    return text


def recent_posts(
    svc,
    course_id: str,
    course_name: str = "",
    *,
    days_back: int = POSTS_LOOKBACK_DAYS,
    limit: int = 10,
) -> tuple[list[dict], str | None]:
    """Announcements and materials a teacher posted lately.

    Not every teacher uses assignments. AP Lang posts the day's work as a
    Material every afternoon, so that class read as having no homework at all
    while the work was sitting in Classroom the whole time — and APUSH puts real
    deadlines in announcements ("chapter 2 InQuizitive due tonight").

    Returns ``(posts, error)``. These are deliberately *not* turned into tasks:
    a post is prose, and inventing an assignment out of it is the one thing he
    has asked Argon never to do. They are context for answering "what do I have
    for Lang", not commitments.
    """
    from googleapiclient.errors import HttpError

    cutoff = datetime.now(LOCAL_TZ) - timedelta(days=days_back)
    posts: list[dict] = []
    error: str | None = None

    sources = (
        ("announcement", lambda: svc.courses().announcements().list(
            courseId=course_id, pageSize=limit, orderBy="updateTime desc"
        ).execute().get("announcements", [])),
        ("material", lambda: svc.courses().courseWorkMaterials().list(
            courseId=course_id, pageSize=limit, orderBy="updateTime desc"
        ).execute().get("courseWorkMaterial", [])),
    )

    for kind, call in sources:
        try:
            items = call()
        except HttpError as exc:
            # One source failing must not hide the other: materials 403 until
            # the new scope is granted, and announcements still work meanwhile.
            error = f"{kind}s unreadable ({exc.resp.status})" if exc.resp else str(exc)
            continue
        except Exception as exc:  # noqa: BLE001 — a dead class is not fatal
            error = str(exc)
            continue

        for item in items:
            stamp = item.get("updateTime") or item.get("creationTime") or ""
            try:
                posted = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if posted < cutoff:
                continue
            body = _post_text(item)
            if not body:
                continue
            posts.append({
                "kind": kind,
                "course": course_name,
                "course_id": course_id,
                "id": item.get("id"),
                "posted_at": posted.astimezone(LOCAL_TZ).isoformat(),
                "text": body,
                "link": item.get("alternateLink"),
            })

    posts.sort(key=lambda p: p["posted_at"], reverse=True)
    return posts[:limit], error


def posts_across_courses(
    svc, *, days_back: int = POSTS_LOOKBACK_DAYS, courses: list[str] | None = None
) -> tuple[list[dict], list[str]]:
    """Recent posts from every active course, or only the named ones."""
    found: list[dict] = []
    unreadable: list[str] = []
    for course in active_courses(svc):
        name = course.get("name", "")
        if courses and not any(c.lower() in name.lower() for c in courses):
            continue
        posts, error = recent_posts(svc, course["id"], name, days_back=days_back)
        found.extend(posts)
        if error:
            unreadable.append(f"{name}: {error}")
    found.sort(key=lambda p: p["posted_at"], reverse=True)
    return found, unreadable


# ---------------------------------------------------------------------------

class GetClassPostsTool(ClassroomTool):
    """Read what teachers posted, not just what they assigned."""

    @property
    def name(self) -> str:
        return "get_class_posts"

    @property
    def description(self) -> str:
        return (
            "Read recent Google Classroom posts — announcements and materials — "
            "for one class or all of them. Not every teacher creates assignments: "
            "AP Lang posts the day's homework as a daily Material, so it never "
            "appears in get_all_assignments or on the board. APUSH puts real "
            "deadlines in announcements. Use this when he asks what he has for a "
            "class that looks empty, or when a class is known to work this way. "
            "These are posts, not assignments — read them and say what they say. "
            "Never turn one into a task on your own."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "course": {
                    "type": "string",
                    "description": (
                        "Part of a course name, e.g. 'Lang' or 'APUSH'. Omit for "
                        "every active course."
                    ),
                },
                "days_back": {
                    "type": "integer",
                    "description": f"How far back to look. Default {POSTS_LOOKBACK_DAYS}.",
                },
            },
            "required": [],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        course = (kwargs.get("course") or "").strip()
        days_back = int(kwargs.get("days_back") or POSTS_LOOKBACK_DAYS)
        posts, unreadable = posts_across_courses(
            self._svc(),
            days_back=days_back,
            courses=[course] if course else None,
        )
        payload: dict[str, Any] = {"posts": posts, "count": len(posts)}
        if unreadable:
            # Surfaced rather than swallowed: a 403 here means a missing scope,
            # and a class that silently returns nothing looks like a class with
            # no homework, which is the exact failure this tool exists to fix.
            payload["unreadable"] = unreadable
        return json.dumps(payload, indent=2)
