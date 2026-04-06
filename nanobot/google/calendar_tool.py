"""Google Calendar tools — individual focused tools for the work account."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nanobot.google.base import GoogleAPITool, build_google_service

_TZ = ZoneInfo("America/Los_Angeles")


def _now() -> datetime:
    return datetime.now(_TZ)


def _fmt_event(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "summary": e.get("summary"),
        "description": e.get("description"),
        "location": e.get("location"),
        "start": e.get("start"),
        "end": e.get("end"),
        "status": e.get("status"),
        "attendees": [a.get("email") for a in e.get("attendees", [])],
        "htmlLink": e.get("htmlLink"),
    }


def _svc(workspace):
    return build_google_service(workspace, "calendar", "v3", "work")


class GetTodayEventsTool(GoogleAPITool):
    """Get today's calendar events."""

    @property
    def name(self) -> str:
        return "get_today_events"

    @property
    def description(self) -> str:
        return "Get all Google Calendar events for today (work account, primary calendar)."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def _run(self, kwargs: dict[str, Any]) -> str:
        now = _now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        items = _svc(self._workspace).events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute().get("items", [])
        return json.dumps([_fmt_event(e) for e in items], indent=2)


class ListCalendarEventsTool(GoogleAPITool):
    """List calendar events over a date range."""

    @property
    def name(self) -> str:
        return "list_calendar_events"

    @property
    def description(self) -> str:
        return "List Google Calendar events between two ISO 8601 datetimes."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "ISO 8601 start datetime."},
                "time_max": {"type": "string", "description": "ISO 8601 end datetime."},
                "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)."},
                "max_results": {"type": "integer", "description": "Max events to return (default 20)."},
            },
            "required": ["time_min", "time_max"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        items = _svc(self._workspace).events().list(
            calendarId=kwargs.get("calendar_id", "primary"),
            timeMin=kwargs["time_min"],
            timeMax=kwargs["time_max"],
            maxResults=kwargs.get("max_results", 20),
            singleEvents=True,
            orderBy="startTime",
        ).execute().get("items", [])
        return json.dumps([_fmt_event(e) for e in items], indent=2)


class CreateCalendarEventTool(GoogleAPITool):
    """Create a calendar event."""

    @property
    def name(self) -> str:
        return "create_calendar_event"

    @property
    def description(self) -> str:
        return (
            "Create a Google Calendar event. "
            "event_body fields: summary, description, start (dateTime or date), "
            "end (dateTime or date), location, attendees ([{email}])."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "event_body": {
                    "type": "object",
                    "description": "Event resource with summary, start, end, etc.",
                },
                "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)."},
            },
            "required": ["event_body"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        body = kwargs["event_body"]
        if isinstance(body, str):
            body = json.loads(body)
        event = _svc(self._workspace).events().insert(
            calendarId=kwargs.get("calendar_id", "primary"), body=body,
        ).execute()
        return f"Created: {event.get('id')} — {event.get('summary', '')}"


class UpdateCalendarEventTool(GoogleAPITool):
    """Update a calendar event."""

    @property
    def name(self) -> str:
        return "update_calendar_event"

    @property
    def description(self) -> str:
        return "Update an existing Google Calendar event by ID."

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "event_body": {"type": "object", "description": "Fields to update."},
                "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)."},
            },
            "required": ["event_id", "event_body"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        body = kwargs["event_body"]
        if isinstance(body, str):
            body = json.loads(body)
        event = _svc(self._workspace).events().patch(
            calendarId=kwargs.get("calendar_id", "primary"),
            eventId=kwargs["event_id"],
            body=body,
        ).execute()
        return f"Updated: {event.get('id')} — {event.get('summary', '')}"


class DeleteCalendarEventTool(GoogleAPITool):
    """Delete a calendar event."""

    @property
    def name(self) -> str:
        return "delete_calendar_event"

    @property
    def description(self) -> str:
        return "Delete a Google Calendar event by ID."

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)."},
            },
            "required": ["event_id"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        _svc(self._workspace).events().delete(
            calendarId=kwargs.get("calendar_id", "primary"),
            eventId=kwargs["event_id"],
        ).execute()
        return f"Deleted event {kwargs['event_id']}."


class ListCalendarsTool(GoogleAPITool):
    """List all calendars on the work account."""

    @property
    def name(self) -> str:
        return "list_calendars"

    @property
    def description(self) -> str:
        return "List all Google Calendars on the work account."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def _run(self, kwargs: dict[str, Any]) -> str:
        items = _svc(self._workspace).calendarList().list().execute().get("items", [])
        result = [
            {"id": c["id"], "summary": c.get("summary", ""), "primary": c.get("primary", False)}
            for c in items
        ]
        return json.dumps(result, indent=2)
