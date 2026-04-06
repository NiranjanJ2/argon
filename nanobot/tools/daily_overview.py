"""Daily overview tool — fetches calendar, tasks, and assignments in one call."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nanobot.agent.tools.base import Tool

_TZ = ZoneInfo("America/Los_Angeles")


class GetDailyOverviewTool(Tool):
    """Fetch today's calendar events, pending tasks, and upcoming assignments."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "get_daily_overview"

    @property
    def description(self) -> str:
        return (
            "Get today's full picture in one call: "
            "calendar events for today, all pending tasks (sorted by priority), "
            "and classroom assignments due in the next 7 days. "
            "Use this at the start of a session or when Niranjan asks what's going on."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run)

    def _run(self) -> str:
        now = datetime.now(_TZ)
        result: dict[str, Any] = {}

        # ── Calendar: today's events ──────────────────────────────
        try:
            from nanobot.google.base import build_google_service
            svc = build_google_service(self._workspace, "calendar", "v3", "work")
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
            items = svc.events().list(
                calendarId="primary",
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            ).execute().get("items", [])
            result["calendar_today"] = [
                {
                    "summary": e.get("summary"),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "location": e.get("location"),
                }
                for e in items
            ]
        except Exception as e:
            result["calendar_today"] = {"error": str(e)}

        # ── Tasks: all pending ────────────────────────────────────
        try:
            from nanobot.google.tasks_store import GoogleTasksStore
            result["tasks"] = GoogleTasksStore(self._workspace).get_all()
        except Exception as e:
            result["tasks"] = {"error": str(e)}

        # ── Classroom: assignments due in the next 7 days ─────────
        try:
            from nanobot.google.base import build_google_service
            svc = build_google_service(self._workspace, "classroom", "v1", "school")
            cutoff = now + timedelta(days=7)
            courses = svc.courses().list(courseStates=["ACTIVE"]).execute().get("courses", [])
            assignments: list[dict] = []
            for course in courses:
                works = svc.courses().courseWork().list(
                    courseId=course["id"],
                    courseWorkStates=["PUBLISHED"],
                    maxResults=30,
                ).execute().get("courseWork", [])
                for w in works:
                    due = w.get("dueDate")
                    if not due:
                        continue
                    try:
                        hour = (w.get("dueTime") or {}).get("hours", 23)
                        minute = (w.get("dueTime") or {}).get("minutes", 59)
                        due_dt = datetime(
                            due["year"], due["month"], due["day"], hour, minute, tzinfo=_TZ,
                        )
                        if now <= due_dt <= cutoff:
                            assignments.append({
                                "title": w.get("title"),
                                "course": course.get("name"),
                                "due": due_dt.isoformat(),
                            })
                    except Exception:
                        pass
            assignments.sort(key=lambda x: x["due"])
            result["assignments_next_7d"] = assignments
        except Exception as e:
            result["assignments_next_7d"] = {"error": str(e)}

        return json.dumps(result, indent=2)
