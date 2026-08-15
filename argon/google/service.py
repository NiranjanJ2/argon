"""Shared plumbing for Google API tools: clients, error translation, tool base."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from argon.tools.base import Tool

#: Niranjan's timezone — every Google tool reports times in it.
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def build_google_service(workspace: Path, service_name: str, version: str, account: str):
    """Build an authenticated Google API client.

    Raises ``GoogleAuthExpired`` / ``GoogleAuthUnavailable`` from ``auth`` when
    the account's credentials cannot be used.
    """
    from googleapiclient.discovery import build

    from argon.google.auth import GoogleAuth

    creds = GoogleAuth(workspace).get_credentials(account)
    return build(service_name, version, credentials=creds, cache_discovery=False)


def google_error_message(exc: BaseException, account: str | None = None) -> str | None:
    """Actionable text for a Google failure, or ``None`` if *exc* isn't one.

    Returning ``None`` means "not mine" — the caller should re-raise.
    """
    from googleapiclient.errors import HttpError

    from argon.google.auth import GoogleAuthExpired, GoogleAuthUnavailable

    if isinstance(exc, (GoogleAuthExpired, GoogleAuthUnavailable)):
        return str(exc)

    if isinstance(exc, HttpError):
        status = getattr(exc, "status_code", None) or getattr(exc.resp, "status", None)
        reason = (getattr(exc, "reason", "") or "").strip() or str(exc)
        if status == 401 or (status == 403 and "insufficient" in reason.lower()):
            remedy = (
                f"run `argon google-auth {account}` to re-authorize"
                if account
                else "the account needs re-authorization"
            )
            return f"Google rejected the request ({status}: {reason}) — {remedy}."
        if status == 429 or (status == 403 and "rate" in reason.lower()):
            return f"Google API rate limit hit ({reason}). Try again in a few minutes."
        if status == 404:
            return f"Google API: not found ({reason})."
        return f"Google API error {status}: {reason}"

    return None


def google_tools(workspace: Path) -> list[Tool]:
    """Every Google API tool, ready to register.

    Registration is unconditional on purpose: a tool whose account is not
    authenticated returns an explanation instead of silently not existing.
    """
    from argon.google.calendar import (
        CreateCalendarEventTool,
        DeleteCalendarEventTool,
        GetTodayEventsTool,
        ListCalendarEventsTool,
        ListCalendarsTool,
        UpdateCalendarEventTool,
    )
    from argon.google.classroom import (
        GetAllAssignmentsTool,
        GetAssignmentInfoTool,
        GetCourseAssignmentsTool,
        GetCoursesTool,
        GetCourseStreamTool,
    )
    from argon.google.classroom_dispositions import (
        IgnoreClassroomAssignmentTool,
        RestoreClassroomAssignmentTool,
    )
    from argon.google.drive import DriveTool
    from argon.google.gmail import GmailTool

    tool_types = [
        GetTodayEventsTool, ListCalendarEventsTool, CreateCalendarEventTool,
        UpdateCalendarEventTool, DeleteCalendarEventTool, ListCalendarsTool,
        GetCoursesTool, GetCourseAssignmentsTool, GetAllAssignmentsTool,
        GetAssignmentInfoTool, GetCourseStreamTool,
        IgnoreClassroomAssignmentTool, RestoreClassroomAssignmentTool,
        DriveTool, GmailTool,
    ]
    return [tool_type(workspace) for tool_type in tool_types]


class GoogleAPITool(Tool):
    """Base class for Google API tools.

    Subclasses set ``api`` / ``api_version`` / ``account`` and implement
    ``_run(kwargs) -> str``. Auth and API failures become readable text so the
    tool degrades honestly instead of raising an opaque traceback.
    """

    api: str = ""
    api_version: str = ""
    account: str = ""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    def _svc(self, account: str | None = None):
        """Authenticated client for this tool's API, optionally on another account."""
        return build_google_service(
            self._workspace, self.api, self.api_version, account or self.account
        )

    async def execute(self, **kwargs: Any) -> str:
        return await asyncio.get_running_loop().run_in_executor(
            None, self._guarded_run, kwargs
        )

    def _guarded_run(self, kwargs: dict[str, Any]) -> str:
        try:
            return self._run(kwargs)
        except KeyError as exc:
            # A small model dropping a required argument is routine. Tell it what
            # is missing so it can retry, instead of raising into the agent loop.
            missing = exc.args[0] if exc.args else "an argument"
            logger.warning(f"{self.name}: missing argument {missing!r}")
            return f"Error: {self.name} requires the '{missing}' argument."
        except Exception as exc:
            message = google_error_message(exc, kwargs.get("account") or self.account)
            if message is None:
                raise
            logger.warning(f"{self.name}: {exc}")
            return message

    def _run(self, kwargs: dict[str, Any]) -> str:
        raise NotImplementedError
