"""Sending a notification to the phone.

Until now the iOS app was pull-only: it reconciled when Niranjan opened it and
every twenty seconds while it stayed on screen. So Argon could publish "lock in
for an hour" and the phone would not find out until he next launched the app,
and a reminder could only ever reach him on Discord. The device token was being
registered and stored and nothing was ever sent to it.

APNs is the only way a server reaches an iOS app that is not open. Authentication
is a short-lived ES256 JWT signed with a `.p8` key from the developer account —
not a password, and not per-notification: one token is reused for up to an hour,
and Apple rate-limits regenerating it faster than every twenty minutes.

Delivery here is the same shape as every other promise Argon makes: this returns
whether Apple accepted the notification, with Apple's own reason when it did not,
so the outbox records what actually happened rather than that a request was made.
A token Apple reports as dead is deleted, because continuing to push at an
uninstalled app is how a "delivered" record starts meaning nothing.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from argon.paths import argon_home

#: Apple rejects a token minted more often than every 20 minutes and expires one
#: after 60. Refreshing at 45 sits clear of both edges.
TOKEN_REFRESH_SECONDS = 45 * 60

PRODUCTION_HOST = "https://api.push.apple.com"
SANDBOX_HOST = "https://api.sandbox.push.apple.com"

#: Reasons that mean this device will never accept another notification. The
#: token is removed rather than retried — see `_forget_token`.
DEAD_TOKEN_REASONS = frozenset({"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"})


@dataclass(frozen=True)
class PushResult:
    """What Apple said. `ok` means Apple accepted it for delivery."""

    ok: bool
    status: int = 0
    reason: str | None = None

    def __bool__(self) -> bool:
        return self.ok


class APNsUnavailable(RuntimeError):
    """Push is not configured, so nothing can be sent."""


def _state_path() -> Path:
    return argon_home() / "ios" / "device.json"


def device_token() -> tuple[str | None, str]:
    """The phone's current token and which APNs environment issued it.

    A TestFlight or App Store build registers against production; a build run
    from Xcode registers against sandbox. Pushing a sandbox token to the
    production host is rejected as `BadDeviceToken`, so the environment travels
    with the token rather than being assumed.
    """
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "production"
    token = data.get("device_token")
    env = str(data.get("environment") or "production").lower()
    return (str(token) if token else None), ("sandbox" if env.startswith("sand") else "production")


def _forget_token(reason: str) -> None:
    """Drop a token Apple says is dead, so we stop pushing into nothing."""
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    data.pop("device_token", None)
    data["unregistered_reason"] = reason
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.warning("APNs: device token discarded ({}); the app must re-register", reason)
    except OSError:
        pass


class APNsClient:
    """Signs the JWT and posts to Apple. One per runtime; the token is cached."""

    def __init__(self, config: Any) -> None:
        apns = getattr(getattr(config, "ios", None), "apns", None)
        self._enabled = bool(getattr(apns, "enabled", False))
        self._team_id = str(getattr(apns, "team_id", "") or "")
        self._key_id = str(getattr(apns, "key_id", "") or "")
        self._topic = str(getattr(apns, "bundle_id", "") or "")
        self._key_path = str(getattr(apns, "key_path", "") or "")
        self._token: str | None = None
        self._token_minted: float = 0.0

    @property
    def configured(self) -> bool:
        return bool(
            self._enabled and self._team_id and self._key_id and self._topic and self._key_file()
        )

    def _key_file(self) -> Path | None:
        if self._key_path:
            path = Path(self._key_path).expanduser()
            return path if path.is_file() else None
        default = argon_home() / "apns" / f"AuthKey_{self._key_id}.p8"
        return default if default.is_file() else None

    def _bearer(self) -> str:
        """A cached ES256 assertion. Minted at most every TOKEN_REFRESH_SECONDS."""
        now = time.time()
        if self._token and (now - self._token_minted) < TOKEN_REFRESH_SECONDS:
            return self._token
        key_file = self._key_file()
        if key_file is None:
            raise APNsUnavailable(f"no APNs key for key id {self._key_id!r}")
        import jwt

        self._token = jwt.encode(
            {"iss": self._team_id, "iat": int(now)},
            key_file.read_text(encoding="utf-8"),
            algorithm="ES256",
            headers={"kid": self._key_id},
        )
        self._token_minted = now
        return self._token

    @staticmethod
    def payload(
        title: str, body: str, *, data: dict[str, Any] | None = None, category: str | None = None
    ) -> dict[str, Any]:
        aps: dict[str, Any] = {
            "alert": {"title": title, "body": body},
            "sound": "default",
            "interruption-level": "active",
        }
        if category:
            aps["category"] = category
        return {"aps": aps, **(data or {})}

    async def send(
        self,
        title: str,
        body: str,
        *,
        data: dict[str, Any] | None = None,
        category: str | None = None,
        collapse_id: str | None = None,
    ) -> PushResult:
        """Push one notification. Never raises for an ordinary failure."""
        if not self.configured:
            return PushResult(False, reason="not configured")
        token, environment = device_token()
        if not token:
            return PushResult(False, reason="no device token registered")

        host = SANDBOX_HOST if environment == "sandbox" else PRODUCTION_HOST
        headers = {
            "authorization": f"bearer {self._bearer()}",
            "apns-topic": self._topic,
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        if collapse_id:
            # Two notifications about the same thing should replace each other
            # on the lock screen rather than stack.
            headers["apns-collapse-id"] = collapse_id[:64]

        import httpx

        try:
            async with httpx.AsyncClient(http2=True, timeout=15.0) as client:
                response = await client.post(
                    f"{host}/3/device/{token}",
                    headers=headers,
                    content=json.dumps(
                        self.payload(title, body, data=data, category=category)
                    ).encode(),
                )
        except Exception as exc:  # noqa: BLE001 — a dead network is not a crash
            logger.warning("APNs request failed: {}", exc)
            return PushResult(False, reason=str(exc))

        if response.status_code == 200:
            return PushResult(True, 200)

        reason = ""
        try:
            reason = str(response.json().get("reason") or "")
        except Exception:  # noqa: BLE001
            reason = response.text[:120]
        if reason in DEAD_TOKEN_REASONS:
            _forget_token(reason)
        logger.warning("APNs rejected the push: {} {}", response.status_code, reason)
        return PushResult(False, response.status_code, reason)


__all__ = [
    "APNsClient", "PushResult", "APNsUnavailable", "device_token",
    "PRODUCTION_HOST", "SANDBOX_HOST", "TOKEN_REFRESH_SECONDS", "DEAD_TOKEN_REASONS",
]
