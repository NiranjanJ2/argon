"""Desired focus mode for the iPhone, and the state the phone reports back.

Argon publishes a *desired* mode; the app reconciles toward it and reports what
it actually did. Nothing here sends a command, so a dropped push or a phone
that was offline for an hour converges instead of replaying a stale order.

``version`` is the whole protocol: the app stores the last version it applied
and ignores anything it has already seen. Every change bumps it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from argon import clock
from argon.paths import get_runtime_subdir

# Modes the app understands. "off" clears the shield; everything else asks for
# one. The app maps the name to a Screen Time profile locally — the server
# never learns a profile UUID, so adding a mode is a string on both sides.
MODES = ("off", "school", "homework", "lock_in", "sleep")

_DEFAULT_DESIRED: dict[str, Any] = {
    "mode": "off",
    "version": 0,
    "since": None,
    "expires_at": None,
    "allow_early_end": True,
    "reason": "",
}

_DEFAULT_ACTUAL: dict[str, Any] = {
    "mode": "off",
    "version": 0,
    "shielded": False,
    "last_seen": None,
}


def _stamp(moment: datetime) -> str:
    """ISO 8601 at whole-second precision.

    Swift's ``ISO8601DateFormatter`` parses either zero or exactly three
    fractional digits. Python's ``isoformat()`` emits six, which fails to parse
    and lands as a nil ``expiryDate`` — the app would then treat a timed lock as
    open-ended and never release it on its own. Second precision avoids the
    whole class of problem.
    """
    return moment.replace(microsecond=0).isoformat()


def _file(name: str):
    return get_runtime_subdir("ios") / name


def _read(name: str, default: dict[str, Any]) -> dict[str, Any]:
    """Load a state file, falling back to a complete default.

    The app decodes these into non-optional Swift fields, so a partial object
    fails the whole ``/v1/status`` decode and shows as "Offline". Always merge
    over the defaults rather than returning whatever is on disk.
    """
    try:
        stored = json.loads(_file(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    if not isinstance(stored, dict):
        return dict(default)
    return {**default, **stored}


def _write(name: str, data: dict[str, Any]) -> None:
    _file(name).write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_mode() -> dict[str, Any]:
    """The mode Argon currently wants, with expiry already applied.

    An elapsed window is collapsed to ``off`` here and persisted, so Argon's own
    tools and the app agree instead of the server advertising a lock that the
    phone released ten minutes ago.
    """
    desired = _read("desired_mode.json", _DEFAULT_DESIRED)
    expires_at = desired.get("expires_at")
    if desired.get("mode") != "off" and expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= clock.now():
                return set_mode("off", reason="focus window ended")
        except ValueError:
            pass  # Unparseable expiry: leave it be rather than lose the mode.
    return desired


def set_mode(
    mode: str,
    *,
    duration_min: int | None = None,
    allow_early_end: bool = True,
    reason: str = "",
) -> dict[str, Any]:
    """Publish a new desired mode and bump the version."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")

    now = clock.now()
    expires_at = None
    if mode != "off" and duration_min:
        expires_at = _stamp(now + timedelta(minutes=int(duration_min)))

    desired = {
        "mode": mode,
        "version": int(_read("desired_mode.json", _DEFAULT_DESIRED).get("version", 0)) + 1,
        "since": _stamp(now),
        "expires_at": expires_at,
        "allow_early_end": bool(allow_early_end),
        "reason": reason or "",
    }
    _write("desired_mode.json", desired)
    return desired


def get_actual() -> dict[str, Any]:
    """Last state the phone reported applying."""
    return _read("state.json", _DEFAULT_ACTUAL)


def record_actual(payload: dict[str, Any]) -> dict[str, Any]:
    """Store what the phone says it did. Doubles as a liveness heartbeat."""
    actual = {
        "mode": str(payload.get("mode") or "off"),
        "version": int(payload.get("version") or 0),
        "shielded": bool(payload.get("shielded")),
        "last_seen": _stamp(clock.now()),
        "applied_at": payload.get("applied_at"),
        # -1.0 is what UIDevice reports when battery monitoring is off.
        "battery": payload.get("battery"),
    }
    _write("state.json", actual)
    return actual


def record_device(payload: dict[str, Any]) -> dict[str, Any]:
    """Store the APNs token so a push sender can be dropped in later."""
    device = {
        "device_token": str(payload.get("device_token") or ""),
        "environment": str(payload.get("environment") or "production"),
        "app_version": str(payload.get("app_version") or "unknown"),
        "registered_at": _stamp(clock.now()),
    }
    _write("device.json", device)
    return device


def snapshot() -> dict[str, Any]:
    """The ``ios`` block of ``/v1/status``: what Argon wants, what the phone did."""
    return {"desired": get_mode(), "actual": get_actual()}
