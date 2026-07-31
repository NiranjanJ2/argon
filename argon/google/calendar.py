"""Google Calendar tools — individual focused tools for the work account."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from argon.google.service import LOCAL_TZ, GoogleAPITool


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _fmt_event(e: dict) -> dict:
    return {
        "id": e.get("id"),
        "summary": e.get("summary"),
        "description": e.get("description"),
        "location": e.get("location"),
        "start": e.get("start"),
        "end": e.get("end"),
        "status": e.get("status"),
        "attendees": [a.get("email") for a in e.get("attendees") or []],
        "htmlLink": e.get("htmlLink"),
    }


def _as_body(value: Any) -> dict:
    """Accept an event body as a dict or a JSON string."""
    if isinstance(value, str):
        return json.loads(value)
    return value


class CalendarTool(GoogleAPITool):
    """Shared config for every Calendar tool (work account, Calendar v3)."""

    api = "calendar"
    api_version = "v3"
    account = "work"

    @property
    def read_only(self) -> bool:
        return True

    @staticmethod
    def _calendar_id(kwargs: dict[str, Any]) -> str:
        return kwargs.get("calendar_id") or "primary"


class GetTodayEventsTool(CalendarTool):
    """Get today's calendar events."""

    @property
    def name(self) -> str:
        return "get_today_events"

    @property
    def description(self) -> str:
        return "Get all Google Calendar events for today (work account, primary calendar)."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def _run(self, kwargs: dict[str, Any]) -> str:
        start = _now().replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        items = self._svc().events().list(
            calendarId="primary",
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=20,
        ).execute().get("items", [])
        return json.dumps([_fmt_event(e) for e in items], indent=2)


class ListCalendarEventsTool(CalendarTool):
    """List calendar events over a date range."""

    @property
    def name(self) -> str:
        return "list_calendar_events"

    @property
    def description(self) -> str:
        return "List Google Calendar events between two ISO 8601 datetimes."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "ISO 8601 start datetime."},
                "time_max": {"type": "string", "description": "ISO 8601 end datetime."},
                "calendar_id": {"type": "string", "description": "Calendar ID (default: primary)."},
                "max_results": {"type": "integer", "description": "Max events (default 20)."},
            },
            "required": ["time_min", "time_max"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        items = self._svc().events().list(
            calendarId=self._calendar_id(kwargs),
            timeMin=kwargs["time_min"],
            timeMax=kwargs["time_max"],
            maxResults=kwargs.get("max_results", 20),
            singleEvents=True,
            orderBy="startTime",
        ).execute().get("items", [])
        return json.dumps([_fmt_event(e) for e in items], indent=2)


class CreateCalendarEventTool(CalendarTool):
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
        event = self._svc().events().insert(
            calendarId=self._calendar_id(kwargs),
            body=_as_body(kwargs["event_body"]),
        ).execute()
        return f"Created: {event.get('id')} — {event.get('summary', '')}"


class UpdateCalendarEventTool(CalendarTool):
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
        event = self._svc().events().patch(
            calendarId=self._calendar_id(kwargs),
            eventId=kwargs["event_id"],
            body=_as_body(kwargs["event_body"]),
        ).execute()
        return f"Updated: {event.get('id')} — {event.get('summary', '')}"


class DeleteCalendarEventTool(CalendarTool):
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
        self._svc().events().delete(
            calendarId=self._calendar_id(kwargs),
            eventId=kwargs["event_id"],
        ).execute()
        return f"Deleted event {kwargs['event_id']}."


class ListCalendarsTool(CalendarTool):
    """List all calendars on the work account."""

    @property
    def name(self) -> str:
        return "list_calendars"

    @property
    def description(self) -> str:
        return "List all Google Calendars on the work account."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    def _run(self, kwargs: dict[str, Any]) -> str:
        items = self._svc().calendarList().list().execute().get("items", [])
        result = [
            {"id": c["id"], "summary": c.get("summary", ""), "primary": c.get("primary", False)}
            for c in items
        ]
        return json.dumps(result, indent=2)
