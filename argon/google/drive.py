"""Google Drive tool — read-only across personal, work, and school accounts."""

from __future__ import annotations

import json
from typing import Any

from argon.google.service import GoogleAPITool

_ACCOUNTS = ("personal", "work", "school")

_FIELDS = "id,name,mimeType,size,modifiedTime,parents,webViewLink,description"

#: Google Workspace types have no bytes to download; export them instead.
_EXPORT_MAP = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}

#: Cap on returned file content, sized for a single tool result.
_MAX_CONTENT = 16000


def _fmt_file(f: dict) -> dict:
    out = {
        "id": f.get("id"),
        "name": f.get("name"),
        "mimeType": f.get("mimeType"),
        "size": f.get("size"),
        "modifiedTime": f.get("modifiedTime"),
        "parents": f.get("parents"),
        "webViewLink": f.get("webViewLink"),
        "description": f.get("description"),
    }
    # Only present on get_file_metadata, which asks for extra fields.
    if "shared" in f:
        out["shared"] = f.get("shared")
    if f.get("owners"):
        out["owners"] = [o.get("emailAddress") for o in f["owners"]]
    return out


def _decode(content: Any) -> str:
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return str(content)[:_MAX_CONTENT]


class DriveTool(GoogleAPITool):
    """Read Google Drive files across personal, work, and school accounts."""

    api = "drive"
    api_version = "v3"

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

    def _run(self, kwargs: dict[str, Any]) -> str:
        action = kwargs["action"]
        account = kwargs["account"]
        if account not in _ACCOUNTS:
            return f"Error: account must be one of {_ACCOUNTS}."

        page_size = max(1, min(int(kwargs.get("page_size", 20)), 1000))
        svc = self._svc(account)

        if action == "list_files":
            clauses = []
            if kwargs.get("folder_id"):
                clauses.append(f"'{kwargs['folder_id']}' in parents")
            if not kwargs.get("include_trashed", False):
                clauses.append("trashed = false")
            params: dict[str, Any] = {
                "pageSize": page_size,
                "fields": f"files({_FIELDS})",
                "orderBy": "modifiedTime desc",
            }
            if clauses:
                params["q"] = " and ".join(clauses)
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
                fileId=file_id, fields=f"{_FIELDS},shared,owners(emailAddress)",
            ).execute()
            return json.dumps(_fmt_file(f), indent=2)

        if action == "read_file":
            file_id = kwargs.get("file_id")
            if not file_id:
                return "Error: file_id required for read_file."
            meta = svc.files().get(fileId=file_id, fields="mimeType,name").execute()
            mime = meta.get("mimeType", "")

            if mime in _EXPORT_MAP:
                return _decode(
                    svc.files().export(fileId=file_id, mimeType=_EXPORT_MAP[mime]).execute()
                )
            if mime.startswith("application/vnd.google-apps."):
                return (
                    f"File '{meta.get('name')}' is a Google {mime.rsplit('.', 1)[-1]} "
                    "and cannot be read as text."
                )
            if not (mime.startswith("text/") or mime in ("application/json", "application/xml")):
                return (
                    f"File '{meta.get('name')}' is binary ({mime}). "
                    "Use get_file_metadata for details."
                )
            return _decode(svc.files().get_media(fileId=file_id).execute())

        if action == "list_shared_drives":
            result = svc.drives().list(pageSize=min(page_size, 100)).execute()
            drives = [
                {"id": d["id"], "name": d.get("name")}
                for d in result.get("drives", [])
            ]
            return json.dumps(drives, indent=2)

        return f"Error: Unknown action '{action}'."
