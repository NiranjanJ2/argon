"""Base class for Google API tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


def build_google_service(workspace: Path, service_name: str, version: str, account: str):
    """Build an authenticated Google API service client."""
    from googleapiclient.discovery import build
    from nanobot.google.auth import GoogleAuth
    auth = GoogleAuth(workspace)
    creds = auth.get_credentials(account)
    return build(service_name, version, credentials=creds)


class GoogleAPITool(Tool):
    """Base class for Google API tools. Subclasses implement _run(kwargs) -> str."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def execute(self, **kwargs: Any) -> str:
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, self._run, kwargs)

    def _run(self, kwargs: dict[str, Any]) -> str:
        raise NotImplementedError
