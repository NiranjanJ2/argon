"""Phone lockdown trigger.

Sends a one-word mail to the carrier's SMS gateway; an iOS Shortcut watching for
it toggles Focus mode and app restrictions.

``send_trigger`` is deliberately a plain function so the HTTP API can call it
directly when the iOS app takes over from the SMS gateway.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText
from typing import Any, Literal

from loguru import logger

from argon.config import LockdownConfig
from argon.tools.base import Tool

# Fixed thread anchor — every trigger replies to this id so the phone shows one
# conversation instead of an inbox full of one-word messages.
_THREAD_MESSAGE_ID = "<argon-control-thread@tmomail>"
_SUBJECT = "Argon Control"
_GATEWAY = "tmomail.net"  # ponytail: T-Mobile only; add a carrier field if he switches

State = Literal["lockdown", "unlock"]


def normalize_state(raw: str) -> State:
    """Map anything the model says onto the two real states."""
    return "unlock" if "unlock" in raw.strip().lower() else "lockdown"


def send_trigger(config: LockdownConfig, state: State) -> str:
    """Send the trigger synchronously. Returns a human-readable result."""
    if not config.configured:
        return "Lockdown is not configured (set lockdown.email/password/phone)."

    msg = MIMEText(state.upper())
    msg["Subject"] = _SUBJECT
    msg["From"] = config.email
    msg["To"] = f"+1{config.phone}@{_GATEWAY}"
    msg["In-Reply-To"] = _THREAD_MESSAGE_ID
    msg["References"] = _THREAD_MESSAGE_ID
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(config.email, config.password)
            server.sendmail(config.email, msg["To"], msg.as_string())
    except smtplib.SMTPAuthenticationError:
        logger.error("Lockdown: Gmail rejected the app password")
        return "Failed: Gmail rejected the app password — regenerate it."
    except Exception as e:
        logger.error("Lockdown trigger failed: {}", e)
        return f"Failed to send trigger: {e}"
    logger.info("Lockdown trigger sent: {}", state)
    return f"Trigger '{state.upper()}' sent to phone."


class SendPhoneNotificationTool(Tool):
    """Toggle the phone's Focus mode / app restrictions."""

    def __init__(self, config: LockdownConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "send_phone_notification"

    @property
    def description(self) -> str:
        return (
            "Trigger the iOS Shortcut on Niranjan's phone. "
            "notification='lockdown' enables Focus mode and app restrictions, "
            "'unlock' disables them. Always pair with set_mode when changing state."
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
                    "description": "'lockdown' to restrict the phone, 'unlock' to release it.",
                },
            },
            "required": ["notification"],
        }

    async def execute(self, **kwargs: Any) -> str:
        state = normalize_state(str(kwargs.get("notification", "")))
        return await asyncio.to_thread(send_trigger, self._config, state)
