"""Daily overview tool — the reconciled commitment board plus today's calendar."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from argon.commitments import Board, load_board
from argon.google.service import LOCAL_TZ
from argon.tools.base import Tool


class GetDailyOverviewTool(Tool):
    """Fetch today's calendar events and the one reconciled commitment board."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "get_daily_overview"

    @property
    def description(self) -> str:
        return (
            "Get today's full picture in one call: "
            "calendar events for today, and the single reconciled commitment "
            "board — Google Tasks and Classroom assignments due in the next 7 "
            "days joined into one list, with anything turned in or ignored "
            "already removed. "
            "The `board` field is the answer to \"what's due\" already written out — "
            "relay every line of `board.text` rather than summarising it, and check "
            "your reply against `board.counts`. If `complete` is false, say which "
            "source is missing; a short board may be an outage, not a free evening."
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
        events = self._section(auth, "work", self._calendar_today)
        board = load_board(self._workspace, days_ahead=7)
        # Asked "what's due", the model read all twelve assignments and wrote
        # three of them into prose as though that were the board — including
        # dropping a whole course. Nothing was truncated; it simply lost items
        # while transcribing JSON into a sentence. So the list it should relay
        # is built here, exactly once, and the counts make a short answer
        # visibly wrong instead of quietly wrong.
        return json.dumps(
            {
                "calendar_today": events,
                "commitments": board.as_dicts(),
                "sources": board.health_as_dicts(),
                "complete": board.complete,
                "board": self._board(board, events),
            },
            indent=2,
        )

    @staticmethod
    def _board(board: Board, events: Any) -> dict[str, Any]:
        """The list to read back verbatim, plus what it should add up to."""
        lines = list(board.health_lines())
        if isinstance(events, dict) and events.get("error"):
            lines.append("Unavailable: Calendar — {}".format(events["error"]))

        assignments = [c for c in board.commitments if c.origin == "classroom"]
        tasks = [c for c in board.commitments if c.origin != "classroom"]

        if assignments:
            lines.append("Due from Classroom:")
            for c in assignments:
                course = f" ({c.subject})" if c.subject else ""
                # His own earlier date and the school's deadline are different
                # facts; showing only one of them is how a board stops matching
                # what he actually planned.
                work_by = (
                    f" (personal work-by {c.work_by_when or c.work_by})"
                    if c.work_by else ""
                )
                warning = (
                    f" (submission status unavailable: {c.submission_error})"
                    if c.submission_error else ""
                )
                lines.append(
                    f"  - {c.title}{course} — "
                    f"{c.official_due_when or c.official_due}{work_by}{warning}"
                )
        if tasks:
            lines.append("Tasks:")
            for c in tasks:
                when = f" — {c.due_when}" if c.due_when else ""
                lines.append(f"  - {c.title}{when}")
        if isinstance(events, list) and events:
            lines.append("On the calendar today:")
            for e in events:
                lines.append(f"  - {e.get('summary', '?')} — {e.get('when') or ''}")

        errors = len(board.health_lines()) + (
            1 if isinstance(events, dict) and events.get("error") else 0
        )
        counts = {
            "assignments": len(assignments),
            "tasks": len(tasks),
            "events": len(events) if isinstance(events, list) else 0,
            "errors": errors,
        }
        return {
            "counts": counts,
            "complete": board.complete and not (
                isinstance(events, dict) and events.get("error")
            ),
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
