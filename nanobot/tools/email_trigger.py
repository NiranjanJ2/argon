"""Email trigger tool — send control emails to Argon's own inbox to fire iOS Shortcuts."""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText
from typing import Any

from nanobot.agent.tools.base import Tool


class SendPhoneNotificationTool(Tool):
    """Send a trigger email to agentargonai@gmail.com to fire iOS Shortcuts."""

    def __init__(self, email: str, password: str, phone_number: str) -> None:
        self._email = email
        self._password = password
        self._sms_gateway = f"{phone_number}@tmomail.net"

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
            msg = MIMEText("")
            msg["Subject"] = subject
            msg["From"] = self._email
            msg["To"] = self._sms_gateway
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self._email, self._password)
                server.sendmail(self._email, self._sms_gateway, msg.as_string())

        try:
            await asyncio.to_thread(_send)
            return f"Trigger '{subject}' sent to phone."
        except smtplib.SMTPAuthenticationError:
            return "Failed: Gmail authentication error. Check email/password or use an App Password."
        except Exception as e:
            return f"Failed to send trigger: {e}"
