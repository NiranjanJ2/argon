"""Email trigger tool — send lockdown/unlock SMS via Gmail SMTP to T-Mobile gateway."""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText
from typing import Any

from nanobot.agent.tools.base import Tool

# Fixed thread anchor — all lockdown/unlock emails reply to this ID so they
# stay in one chain instead of flooding the inbox.
_THREAD_MESSAGE_ID = "<argon-control-thread@tmomail>"
_SUBJECT = "Argon Control"


class SendPhoneNotificationTool(Tool):
    """Send a lockdown/unlock trigger SMS via Gmail SMTP → T-Mobile email gateway."""

    def __init__(self, email: str, password: str, phone_number: str) -> None:
        self._email = email
        self._password = password
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

        def _send() -> None:
            msg = MIMEText(notification.upper())
            msg["Subject"] = _SUBJECT
            msg["From"] = self._email
            msg["To"] = self._sms_gateway
            msg["In-Reply-To"] = _THREAD_MESSAGE_ID
            msg["References"] = _THREAD_MESSAGE_ID
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(self._email, self._password)
                server.sendmail(self._email, self._sms_gateway, msg.as_string())

        try:
            await asyncio.to_thread(_send)
            return f"Trigger '{notification.upper()}' sent to phone."
        except smtplib.SMTPAuthenticationError:
            return "Failed: Gmail authentication error."
        except Exception as e:
            return f"Failed to send trigger: {e}"
