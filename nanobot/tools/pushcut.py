"""Pushcut tool — send notifications to Niranjan's phone to trigger iOS Shortcuts."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool


class SendPhoneNotificationTool(Tool):
    """Trigger a Pushcut notification on Niranjan's iPhone."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "send_phone_notification"

    @property
    def description(self) -> str:
        return (
            "Send a notification to Niranjan's phone via Pushcut. "
            "Notifications are linked to iOS Shortcuts that run automatically. "
            "Use notification='Lockdown' to enable phone restrictions (Focus mode / app limits), "
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
                    "description": "Pushcut notification name exactly as configured in the app (e.g. 'Lockdown', 'Unlock').",
                },
                "input": {
                    "type": "string",
                    "description": "Optional text passed as input to the triggered Shortcut.",
                },
            },
            "required": ["notification"],
        }

    async def execute(self, **kwargs: Any) -> str:
        import httpx

        name = kwargs["notification"]
        body: dict[str, Any] = {}
        if kwargs.get("input"):
            body["input"] = kwargs["input"]

        url = f"https://api.pushcut.io/{self._api_key}/notifications/{name}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=body if body else None)
                resp.raise_for_status()
            return f"Notification '{name}' sent to phone."
        except httpx.HTTPStatusError as e:
            return f"Pushcut error {e.response.status_code}: {e.response.text}"
        except Exception as e:
            return f"Failed to send notification: {e}"
