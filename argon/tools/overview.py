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
            "Use this at the start of a session or when Niranjan asks what's going on. "
            "The `board` field is the answer to \"what's due\" already written out — "
            "relay every line of `board.text` rather than summarising it, and check "
            "your reply against `board.counts`."
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
        payload = {
            "calendar_today": self._section(auth, "work", self._calendar_today),
            "tasks": self._section(auth, "work", self._tasks),
            "assignments_next_7d": self._section(auth, "school", self._assignments),
        }
        # Asked "what's due", the model read all twelve assignments and wrote
        # three of them into prose as though that were the board — including
        # dropping a whole course. Nothing was truncated; it simply lost items
        # while transcribing JSON into a sentence. So the list it should relay
        # is built here, exactly once, and the counts make a short answer
        # visibly wrong instead of quietly wrong.
        payload["board"] = self._board(payload)
        return json.dumps(payload, indent=2)

    @staticmethod
    def _board(payload: dict[str, Any]) -> dict[str, Any]:
        """The list to read back verbatim, plus what it should add up to."""
        assignments = payload.get("assignments_next_7d")
        tasks = payload.get("tasks")
        events = payload.get("calendar_today")

        lines: list[str] = []
        if isinstance(assignments, list) and assignments:
            lines.append("Due from Classroom:")
            for a in assignments:
                course = f" ({a['course']})" if a.get("course") else ""
                lines.append(f"  - {a.get('title', '?')}{course} — {a.get('due_when') or a.get('due')}")
        if isinstance(tasks, list) and tasks:
            lines.append("Tasks:")
            for t in tasks:
                when = f" — {t['due_when']}" if t.get("due_when") else ""
                lines.append(f"  - {t.get('title', '?')}{when}")
        if isinstance(events, list) and events:
            lines.append("On the calendar today:")
            for e in events:
                lines.append(f"  - {e.get('summary', '?')} — {e.get('when') or ''}")

        counts = {
            "assignments": len(assignments) if isinstance(assignments, list) else 0,
            "tasks": len(tasks) if isinstance(tasks, list) else 0,
            "events": len(events) if isinstance(events, list) else 0,
        }
        return {
            "counts": counts,
            "text": "\n".join(lines) or "Nothing due and nothing scheduled.",
            "how_to_use": (
                "When he asks what is due or what the board looks like, relay "
                "`text` — every line of it. Do not summarise it into a "
                "sentence and do not choose the important ones: he is asking "
                "what exists, and an answer missing {} of {} assignments is "
                "worse than no answer because he cannot tell.".format(
                    max(0, counts["assignments"] - 3), counts["assignments"])
            ),
        }

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
        from argon.utils.helpers import when_label

        return [
            {
                "summary": e.get("summary"),
                "start": e.get("start"),
                "when": when_label((e.get("start") or {}).get("dateTime")
                                   or (e.get("start") or {}).get("date")),
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
        from argon.utils.helpers import when_label

        return [
            {"title": a["title"], "course": a.get("course_name"), "due": a["due"],
             "due_when": when_label(a["due"])}
            for a in assignments
        ]
