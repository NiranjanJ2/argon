"""Email trigger tool — send SMS trigger via Gmail API to T-Mobile gateway."""

from __future__ import annotations

import asyncio
import base64
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class SendPhoneNotificationTool(Tool):
    """Send a lockdown/unlock trigger SMS via Gmail API → T-Mobile email gateway."""

    def __init__(self, workspace: Path, phone_number: str) -> None:
        self._workspace = workspace
        self._sms_gateway = f"+1{phone_number}@tmomail.net"

    @property
    def name(self) -> str:
        return "send_phone_notification"

    @property
    def description(self) -> str:
        return (
            "Send a trigger to Niranjan's phone to run an iOS Shortcut. "
            "Use notification='Lockdown' to enable Focus mode / app restrictions, "
            "'Unlock' to disable them. "
            "Always pair with set_mode when changing lockdown state."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "notification": {
                    "type": "string",
                    "enum": ["Lockdown", "Unlock"],
                    "description": "Action to trigger on the phone.",
                },
            },
            "required": ["notification"],
        }

    async def execute(self, **kwargs: Any) -> str:
        notification = kwargs["notification"]
        subject = notification.upper()

        def _send() -> None:
            from googleapiclient.discovery import build
            from nanobot.google.auth import GoogleAuth

            auth = GoogleAuth(self._workspace)
            creds = auth.get_credentials("trigger")
            service = build("gmail", "v1", credentials=creds)

            msg = MIMEText(subject)
            msg["Subject"] = subject
            msg["To"] = self._sms_gateway
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()

        try:
            await asyncio.to_thread(_send)
            return f"Trigger '{subject}' sent to phone."
        except RuntimeError as e:
            return f"Failed: {e}"
        except Exception as e:
            return f"Failed to send trigger: {e}"
