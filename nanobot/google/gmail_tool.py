"""Google Gmail tool — read-only on work and school accounts."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.google.auth import GoogleAuth

_ACCOUNTS = ("work", "school")


def _build_service(workspace: Path, account: str):
    from googleapiclient.discovery import build
    auth = GoogleAuth(workspace)
    creds = auth.get_credentials(account)
    return build("gmail", "v1", credentials=creds)


class GmailTool(Tool):
    """Read Gmail on work and school accounts."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

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

    async def execute(self, **kwargs: Any) -> str:
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, self._run, kwargs)

    def _run(self, kwargs: dict[str, Any]) -> str:
        action = kwargs["action"]
        account = kwargs["account"]
        if account not in _ACCOUNTS:
            return f"Error: account must be one of {_ACCOUNTS}."

        max_results = kwargs.get("max_results", 10)
        svc = _build_service(self._workspace, account)

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
                {"id": l["id"], "name": l.get("name"), "type": l.get("type")}
                for l in result.get("labels", [])
            ]
            return json.dumps(labels, indent=2)

        if action in ("list_messages", "search_messages"):
            params: dict[str, Any] = {"userId": "me", "maxResults": max_results}
            if kwargs.get("query"):
                params["q"] = kwargs["query"]
            if kwargs.get("label_ids"):
                params["labelIds"] = kwargs["label_ids"]
            result = svc.users().messages().list(**params).execute()
            messages = result.get("messages", [])
            # Fetch snippet for each
            summaries = []
            for msg_ref in messages[:max_results]:
                msg = svc.users().messages().get(
                    userId="me", id=msg_ref["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                ).execute()
                summaries.append(_fmt_message_summary(msg))
            return json.dumps(summaries, indent=2)

        if action == "get_message":
            message_id = kwargs.get("message_id")
            if not message_id:
                return "Error: message_id required for get_message."
            include_body = kwargs.get("include_body", True)
            fmt = "full" if include_body else "metadata"
            msg = svc.users().messages().get(
                userId="me", id=message_id, format=fmt
            ).execute()
            return json.dumps(_fmt_message_full(msg, include_body=include_body), indent=2)

        return f"Error: Unknown action '{action}'."


def _get_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_body(payload: dict, max_chars: int = 8000) -> str:
    """Recursively extract plain-text body from a message payload."""
    mime = payload.get("mimeType", "")
    body_data = (payload.get("body") or {}).get("data")

    if mime == "text/plain" and body_data:
        text = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
        return text[:max_chars]

    parts = payload.get("parts") or []
    for part in parts:
        text = _extract_body(part, max_chars)
        if text:
            return text
    return ""


def _fmt_message_summary(msg: dict) -> dict:
    headers = (msg.get("payload") or {}).get("headers") or []
    return {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "snippet": msg.get("snippet", "")[:200],
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "date": _get_header(headers, "Date"),
        "labelIds": msg.get("labelIds", []),
    }


def _fmt_message_full(msg: dict, include_body: bool = True) -> dict:
    payload = msg.get("payload") or {}
    headers = payload.get("headers") or []
    result = {
        "id": msg.get("id"),
        "threadId": msg.get("threadId"),
        "snippet": msg.get("snippet", ""),
        "from": _get_header(headers, "From"),
        "to": _get_header(headers, "To"),
        "subject": _get_header(headers, "Subject"),
        "date": _get_header(headers, "Date"),
        "labelIds": msg.get("labelIds", []),
    }
    if include_body:
        result["body"] = _extract_body(payload)
    return result
