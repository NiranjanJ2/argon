"""Google Gmail tool — read-only on work and school accounts."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from argon.google.service import GoogleAPITool

_ACCOUNTS = ("work", "school")

_HEADERS = ("From", "To", "Subject", "Date")
_TAG_RE = re.compile(r"<[^>]+>")


class GmailTool(GoogleAPITool):
    """Read Gmail on work and school accounts."""

    api = "gmail"
    api_version = "v1"

    @property
    def name(self) -> str:
        return "gmail"

    @property
    def description(self) -> str:
        return (
            "Read Gmail on work and school accounts. "
            "Actions: list_messages, get_message, search_messages, list_labels, get_profile."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "list_messages", "get_message",
                        "search_messages", "list_labels", "get_profile",
                    ],
                    "description": "Operation to perform.",
                },
                "account": {
                    "type": "string",
                    "enum": list(_ACCOUNTS),
                    "description": "Which Gmail account to use (work or school).",
                },
                "message_id": {
                    "type": "string",
                    "description": "Message ID (required for get_message).",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Gmail search query for search_messages / list_messages "
                        "(e.g. 'from:boss@work.com', 'is:unread', 'subject:report')."
                    ),
                },
                "label_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Filter by label IDs (e.g. ['INBOX', 'UNREAD']).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max messages to return (default 10).",
                },
                "include_body": {
                    "type": "boolean",
                    "description": "Include message body in get_message (default true).",
                },
            },
            "required": ["action", "account"],
        }

    def _run(self, kwargs: dict[str, Any]) -> str:
        action = kwargs["action"]
        account = kwargs["account"]
        if account not in _ACCOUNTS:
            return f"Error: account must be one of {_ACCOUNTS}."

        max_results = max(1, min(int(kwargs.get("max_results", 10)), 50))
        svc = self._svc(account)

        if action == "get_profile":
            profile = svc.users().getProfile(userId="me").execute()
            return json.dumps({
                "emailAddress": profile.get("emailAddress"),
                "messagesTotal": profile.get("messagesTotal"),
                "threadsTotal": profile.get("threadsTotal"),
            }, indent=2)

        if action == "list_labels":
            result = svc.users().labels().list(userId="me").execute()
            labels = [
                {"id": lb["id"], "name": lb.get("name"), "type": lb.get("type")}
                for lb in result.get("labels", [])
            ]
            return json.dumps(labels, indent=2)

        if action in ("list_messages", "search_messages"):
            params: dict[str, Any] = {"userId": "me", "maxResults": max_results}
            if kwargs.get("query"):
                params["q"] = kwargs["query"]
            if kwargs.get("label_ids"):
                params["labelIds"] = kwargs["label_ids"]
            refs = svc.users().messages().list(**params).execute().get("messages", [])
            summaries = [
                _fmt_message_summary(
                    svc.users().messages().get(
                        userId="me", id=ref["id"],
                        format="metadata", metadataHeaders=list(_HEADERS),
                    ).execute()
                )
                for ref in refs[:max_results]
            ]
            return json.dumps(summaries, indent=2)

        if action == "get_message":
            message_id = kwargs.get("message_id")
            if not message_id:
                return "Error: message_id required for get_message."
            include_body = kwargs.get("include_body", True)
            msg = svc.users().messages().get(
                userId="me", id=message_id, format="full" if include_body else "metadata",
            ).execute()
            result = _fmt_message_summary(msg, snippet_chars=None)
            if include_body:
                result["body"] = _extract_body(msg.get("payload") or {})
            return json.dumps(result, indent=2)

        return f"Error: Unknown action '{action}'."


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _b64(data: str) -> str:
    """Decode Gmail's url-safe base64, padding it correctly first."""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
        "utf-8", errors="replace"
    )


def _find_part(payload: dict, mime_type: str) -> str:
    """Depth-first search for the first body of *mime_type*, decoded."""
    if payload.get("mimeType") == mime_type:
        data = (payload.get("body") or {}).get("data")
        if data:
            return _b64(data)
    for part in payload.get("parts") or []:
        found = _find_part(part, mime_type)
        if found:
            return found
    return ""


def _extract_body(payload: dict, max_chars: int = 8000) -> str:
    """Plain-text body, falling back to de-tagged HTML for HTML-only mail."""
    text = _find_part(payload, "text/plain")
    if not text:
        html = _find_part(payload, "text/html")
        if html:
            text = _TAG_RE.sub(" ", html)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:max_chars]


def _fmt_message_summary(msg: dict, snippet_chars: int | None = 200) -> dict:
    headers = (msg.get("payload") or {}).get("headers") or []
    snippet = msg.get("snippet", "")
    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "snippet": snippet[:snippet_chars] if snippet_chars else snippet,
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "date": _get_header(headers, "Date"),
        "labelIds": msg.get("labelIds", []),
    }
