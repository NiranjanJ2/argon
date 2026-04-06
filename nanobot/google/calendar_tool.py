"""Google Calendar tool — read/write on the 'work' account."""

from __future__ import annotations

import json
from typing import Any

from nanobot.google.base import GoogleAPITool, build_google_service


class CalendarTool(GoogleAPITool):
    """Interact with Google Calendar (work account)."""

    @property
    def name(self) -> str:
        return "google_calendar"

    @property
    def description(self) -> str:
        return (
            "Read and write Google Calendar events on the work account. "
            "Actions: list_events, get_event, create_event, update_event, delete_event, list_calendars, free_busy."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_events", "get_event", "create_event",
                        "update_event", "delete_event", "list_calendars", "free_busy",
                    ],
                    "description": "Operation to perform.",
                },
                "calendar_id": {
                    "type": "string",
                    "description": "Calendar ID. Defaults to 'primary'.",
                },
                "event_id": {
                    "type": "string",
                    "description": "Event ID (required for get/update/delete).",
                },
                "time_min": {
                    "type": "string",
                    "description": "ISO 8601 datetime lower bound for list_events / free_busy.",
                },
                "time_max": {
                    "type": "string",
                    "description": "ISO 8601 datetime upper bound for list_events / free_busy.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max events to return (default 20).",
                },
                "event_body": {
                    "type": "object",
                    "description": (
                        "Event resource for create/update. "
                        "Fields: summary, description, start (dateTime/date), end (dateTime/date), "
                        "location, attendees ([{email}]), recurrence ([RRULE:...])."
                    ),
                },
            },
            "required": ["action"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        action = kwargs["action"]
        cal_id = kwargs.get("calendar_id", "primary")
        svc = build_google_service(self._workspace, "calendar", "v3", "work")

        if action == "list_calendars":
            result = svc.calendarList().list().execute()
            items = [
                {"id": c["id"], "summary": c.get("summary", ""), "primary": c.get("primary", False)}
                for c in result.get("items", [])
            ]
            return json.dumps(items, indent=2)

        if action == "list_events":
            params: dict[str, Any] = {
                "calendarId": cal_id,
                "maxResults": kwargs.get("max_results", 20),
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if kwargs.get("time_min"):
                params["timeMin"] = kwargs["time_min"]
            if kwargs.get("time_max"):
                params["timeMax"] = kwargs["time_max"]
            result = svc.events().list(**params).execute()
            events = [_fmt_event(e) for e in result.get("items", [])]
            return json.dumps(events, indent=2)

        if action == "get_event":
            event_id = kwargs.get("event_id")
            if not event_id:
                return "Error: event_id required for get_event."
            event = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
            return json.dumps(_fmt_event(event), indent=2)

        if action == "create_event":
            body = kwargs.get("event_body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    return "Error: event_body must be a JSON object, not a string."
            if not body:
                return "Error: event_body required for create_event."
            event = svc.events().insert(calendarId=cal_id, body=body).execute()
            return f"Created event: {event.get('id')} — {event.get('summary', '')}"

        if action == "update_event":
            event_id = kwargs.get("event_id")
            body = kwargs.get("event_body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    return "Error: event_body must be a JSON object, not a string."
            if not event_id or not body:
                return "Error: event_id and event_body required for update_event."
            event = svc.events().patch(calendarId=cal_id, eventId=event_id, body=body).execute()
            return f"Updated event: {event.get('id')} — {event.get('summary', '')}"

        if action == "delete_event":
            event_id = kwargs.get("event_id")
            if not event_id:
                return "Error: event_id required for delete_event."
            svc.events().delete(calendarId=cal_id, eventId=event_id).execute()
            return f"Deleted event {event_id}."

        if action == "free_busy":
            time_min = kwargs.get("time_min")
            time_max = kwargs.get("time_max")
            if not time_min or not time_max:
                return "Error: time_min and time_max required for free_busy."
            body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": cal_id}],
            }
            result = svc.freebusy().query(body=body).execute()
            return json.dumps(result.get("calendars", {}), indent=2)

        return f"Error: Unknown action '{action}'."


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
        "recurrence": e.get("recurrence"),
    }
