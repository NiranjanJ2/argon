"""Google Drive tool — read-only across personal, work, and school accounts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.google.auth import GoogleAuth

_ACCOUNTS = ("personal", "work", "school")


def _build_service(workspace: Path, account: str):
    from googleapiclient.discovery import build
    auth = GoogleAuth(workspace)
    creds = auth.get_credentials(account)
    return build("drive", "v3", credentials=creds)


class DriveTool(Tool):
    """Read Google Drive files across personal, work, and school accounts."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "google_drive"

    @property
    def description(self) -> str:
        return (
            "Read Google Drive files across personal, work, and school accounts. "
            "Actions: list_files, search_files, get_file_metadata, read_file, list_shared_drives."
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
                        "list_files", "search_files",
                        "get_file_metadata", "read_file", "list_shared_drives",
                    ],
                    "description": "Operation to perform.",
                },
                "account": {
                    "type": "string",
                    "enum": list(_ACCOUNTS),
                    "description": "Which Google account to use.",
                },
                "file_id": {
                    "type": "string",
                    "description": "File ID (required for get_file_metadata / read_file).",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "Drive query string for search_files "
                        "(e.g. \"name contains 'budget'\" or \"mimeType='application/pdf'\")."
                    ),
                },
                "folder_id": {
                    "type": "string",
                    "description": "Folder ID to list files within (optional).",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Max results (default 20).",
                },
                "include_trashed": {
                    "type": "boolean",
                    "description": "Include trashed files (default false).",
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

        page_size = kwargs.get("page_size", 20)
        svc = _build_service(self._workspace, account)

        _FIELDS = "id, name, mimeType, size, modifiedTime, parents, webViewLink, description"

        if action == "list_files":
            folder_id = kwargs.get("folder_id")
            include_trashed = kwargs.get("include_trashed", False)
            q_parts = []
            if folder_id:
                q_parts.append(f"'{folder_id}' in parents")
            if not include_trashed:
                q_parts.append("trashed = false")
            q = " and ".join(q_parts) if q_parts else None
            params: dict[str, Any] = {
                "pageSize": page_size,
                "fields": f"files({_FIELDS})",
                "orderBy": "modifiedTime desc",
            }
            if q:
                params["q"] = q
            result = svc.files().list(**params).execute()
            return json.dumps([_fmt_file(f) for f in result.get("files", [])], indent=2)

        if action == "search_files":
            query = kwargs.get("query")
            if not query:
                return "Error: query required for search_files."
            full_query = query if "trashed" in query else f"({query}) and trashed = false"
            result = svc.files().list(
                q=full_query,
                pageSize=page_size,
                fields=f"files({_FIELDS})",
            ).execute()
            return json.dumps([_fmt_file(f) for f in result.get("files", [])], indent=2)

        if action == "get_file_metadata":
            file_id = kwargs.get("file_id")
            if not file_id:
                return "Error: file_id required for get_file_metadata."
            f = svc.files().get(
                fileId=file_id,
                fields=f"{_FIELDS}, permissions, shared, owners",
            ).execute()
            return json.dumps(_fmt_file(f), indent=2)

        if action == "read_file":
            file_id = kwargs.get("file_id")
            if not file_id:
                return "Error: file_id required for read_file."
            # Get metadata first to determine mime type
            meta = svc.files().get(fileId=file_id, fields="mimeType, name").execute()
            mime = meta.get("mimeType", "")

            # Google Workspace docs: export as plain text
            _EXPORT_MAP = {
                "application/vnd.google-apps.document": "text/plain",
                "application/vnd.google-apps.spreadsheet": "text/csv",
                "application/vnd.google-apps.presentation": "text/plain",
            }
            if mime in _EXPORT_MAP:
                content = svc.files().export(
                    fileId=file_id, mimeType=_EXPORT_MAP[mime]
                ).execute()
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="replace")
                return content[:16000]  # cap to tool result limit

            # Binary / other: return metadata only
            if not mime.startswith("text/"):
                return f"File '{meta.get('name')}' is binary ({mime}). Use get_file_metadata for details."

            content = svc.files().get_media(fileId=file_id).execute()
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            return content[:16000]

        if action == "list_shared_drives":
            result = svc.drives().list(pageSize=page_size).execute()
            drives = [
                {"id": d["id"], "name": d.get("name")}
                for d in result.get("drives", [])
            ]
            return json.dumps(drives, indent=2)

        return f"Error: Unknown action '{action}'."


def _fmt_file(f: dict) -> dict:
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "mimeType": f.get("mimeType"),
        "size": f.get("size"),
        "modifiedTime": f.get("modifiedTime"),
        "parents": f.get("parents"),
        "webViewLink": f.get("webViewLink"),
        "description": f.get("description"),
    }
