"""Daily overview tool — fetches calendar, tasks, and assignments in one call."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from argon.google.service import LOCAL_TZ
from argon.tools.base import Tool


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
        from argon.google.auth import GoogleAuth

        auth = GoogleAuth(self._workspace)
        return json.dumps(
            {
                "calendar_today": self._section(auth, "work", self._calendar_today),
                "tasks": self._section(auth, "work", self._tasks),
                "assignments_next_7d": self._section(auth, "school", self._assignments),
            },
            indent=2,
        )

    def _section(self, auth, account: str, fetch: Callable[[], Any]) -> Any:
        """Run one section, degrading to an actionable error instead of failing."""
        from argon.google.service import google_error_message

        blocked = auth.status_message(account)
        if blocked:
            return {"error": blocked}
        try:
            return fetch()
        except Exception as exc:
            logger.warning(f"get_daily_overview[{account}]: {exc}")
            return {
                "error": google_error_message(exc, account)
                or f"{type(exc).__name__}: {exc}"
            }

    # -- sections ------------------------------------------------------

    def _calendar_today(self) -> list[dict]:
        from argon.google.service import build_google_service

        svc = build_google_service(self._workspace, "calendar", "v3", "work")
        start = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        items = svc.events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=(start + timedelta(days=1)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute().get("items", [])
        return [
            {
                "summary": e.get("summary"),
                "start": e.get("start"),
                "end": e.get("end"),
                "location": e.get("location"),
            }
            for e in items
        ]

    def _tasks(self) -> list[dict]:
        from argon.google.tasks_store import GoogleTasksStore

        return GoogleTasksStore(self._workspace).get_all()

    def _assignments(self) -> list[dict]:
        from argon.google.classroom import upcoming_assignments
        from argon.google.service import build_google_service

        svc = build_google_service(self._workspace, "classroom", "v1", "school")
        assignments, unreadable = upcoming_assignments(svc, days_ahead=7)
        if unreadable:
            logger.warning(f"get_daily_overview: unreadable courses {unreadable}")
        return [
            {"title": a["title"], "course": a.get("course_name"), "due": a["due"]}
            for a in assignments
        ]
