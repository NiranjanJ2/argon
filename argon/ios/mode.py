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

from loguru import logger

from argon import clock
from argon.paths import get_runtime_subdir

# Modes the app understands. "off" clears the shield; everything else asks for
# one. The app maps the name to a Screen Time profile locally — the server
# never learns a profile UUID, so adding a mode is a string on both sides.
#
# "weekend" is the odd one: it shields like the rest, but the phone also opens a
# metered allowance, so tapping a blocked app offers a short break instead of a
# wall. It is a different *kind* of block, not a stricter one.
MODES = ("off", "school", "homework", "lock_in", "sleep", "weekend")

#: Modes that carry a metered allowance when none is given explicitly.
_DEFAULT_ALLOWANCE = {"minutes": 15, "per_hours": 1}

_DEFAULT_DESIRED: dict[str, Any] = {
    "mode": "off",
    "version": 0,
    "since": None,
    "expires_at": None,
    "allow_early_end": True,
    "reason": "",
    # Who asked for this block. "task" means it rode in on a start_task and may
    # be cleared when that task finishes; anything else is Niranjan's own call
    # and finishing a task must leave it alone.
    "source": "",
    # None for an ordinary hard block. {"minutes": int, "per_hours": int} means
    # the phone should meter distracting apps rather than forbid them.
    "allowance": None,
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


def renew(minutes: int, *, source: str) -> dict[str, Any] | None:
    """Push a running block's expiry out, without bumping the version.

    This is how "lock until I say I'm done" is built without an open-ended hard
    block. The phone rewrites its on-device failsafe timer from ``expires_at``
    on every poll, not only when the version changes, so extending in place is
    picked up within a poll — and *not* bumping the version matters, or the app
    would re-apply the whole block every twenty seconds.

    The safety property survives intact. Nothing here renews itself: if Argon
    dies, or the phone stops checking in, the last expiry it was handed stands
    and the block lifts on its own. An unattended block has a ceiling.

    Only renews a block from *source*, so a task that is still running cannot
    quietly extend a lock Niranjan set himself on different terms.
    """
    desired = _read("desired_mode.json", _DEFAULT_DESIRED)
    if desired.get("mode") == "off" or desired.get("source") != source:
        return None
    desired["expires_at"] = _stamp(clock.now() + timedelta(minutes=max(1, int(minutes))))
    _write("desired_mode.json", desired)
    return desired


class OverrideActive(ValueError):
    """Raised when a lock is attempted during an emergency override."""


def override_status() -> tuple[bool, str | None]:
    """``(active, until)`` for the emergency override."""
    until = _read("override.json", {}).get("until")
    if not until:
        return False, None
    try:
        if datetime.fromisoformat(until) > clock.now():
            return True, until
    except ValueError:
        return False, None
    return False, until


def engage_override(minutes: int, source: str = "phone") -> dict[str, Any]:
    """Release any block and refuse to impose another for *minutes*.

    Releasing alone is not enough: the phone re-applies the desired mode on
    every poll, and Argon could publish a fresh lock a minute later. The
    override is the part that makes "let me out" actually stick.
    """
    now = clock.now()
    record = {
        "until": _stamp(now + timedelta(minutes=max(1, int(minutes)))),
        "since": _stamp(now),
        "source": source,
    }
    _write("override.json", record)
    set_mode("off", reason=f"emergency override ({source})")
    logger.warning("Emergency override engaged until {} by {}", record["until"], source)
    return record


def clear_override() -> None:
    """End an override early. Locks may be imposed again immediately."""
    _write("override.json", {})


def set_mode(
    mode: str,
    *,
    duration_min: int | None = None,
    allow_early_end: bool = True,
    reason: str = "",
    source: str = "",
    allowance_minutes: int | None = None,
    allowance_per_hours: int | None = None,
) -> dict[str, Any]:
    """Publish a new desired mode and bump the version."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")

    # 'off' is always allowed — an escape hatch must never be able to jam shut.
    if mode != "off":
        active, until = override_status()
        if active:
            raise OverrideActive(
                f"emergency override is active until {until}; no block can be "
                "imposed before then"
            )

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
        "source": source or "",
        "allowance": _allowance_for(mode, allowance_minutes, allowance_per_hours),
    }
    _write("desired_mode.json", desired)
    return desired


#: Reset windows the phone will accept. Anything else is silently dropped there,
#: so it is clamped here instead — the server advertising an allowance the phone
#: never applied is the same lie as advertising a lock it never took.
#:
#: Only two, because only two are expressible. Screen Time repeats a schedule
#: whose components are minutes hourly, and one with hours and minutes daily;
#: there is no single schedule that repeats every six hours. The previous list
#: included 6 and 12 because the foqos allowance this was built on counted
#: *unblocks* against a timer it managed itself, which Screen Time was not
#: enforcing.
ALLOWANCE_WINDOWS_HOURS = (1, 24)
ALLOWANCE_MINUTES_RANGE = (5, 60)


def _allowance_for(
    mode: str, minutes: int | None, per_hours: int | None
) -> dict[str, int] | None:
    """Clamp an allowance to what Screen Time can actually enforce."""
    if mode == "off":
        return None
    if minutes is None and per_hours is None:
        # Only weekend metering by default; every other mode is a hard block.
        if mode != "weekend":
            return None
        minutes, per_hours = _DEFAULT_ALLOWANCE["minutes"], _DEFAULT_ALLOWANCE["per_hours"]

    low, high = ALLOWANCE_MINUTES_RANGE
    minutes = min(high, max(low, int(minutes or _DEFAULT_ALLOWANCE["minutes"])))
    per_hours = int(per_hours or _DEFAULT_ALLOWANCE["per_hours"])
    if per_hours not in ALLOWANCE_WINDOWS_HOURS:
        # Round to the nearest window the phone supports rather than dropping
        # the allowance entirely and turning a metered mode into a hard wall.
        per_hours = min(ALLOWANCE_WINDOWS_HOURS, key=lambda h: abs(h - per_hours))
    return {"minutes": minutes, "per_hours": per_hours}


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
        # Present only when the phone could not apply what was asked.
        "error": (str(payload["error"])[:300] if payload.get("error") else None),
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


#: No report in this long and the phone is treated as gone. The app polls every
#: 20s while foregrounded, so a few missed rounds is already meaningful.
STALE_AFTER_MINUTES = 5


def convergence() -> tuple[str, str]:
    """Has the phone actually done what Argon asked? ``(state, detail)``.

    A lock that fails on the phone is silent: the app's reconciler returns nil
    on error and its caller only reports on success, so "tried and failed" looks
    exactly like "phone is off". Comparing the version the phone last applied
    against the version Argon published catches it without trusting the app to
    confess — which matters, because the failure mode is Argon believing it
    locked a phone that is wide open.
    """
    desired, actual = get_mode(), get_actual()

    # The phone says outright that it could not apply this.
    if actual.get("error"):
        return "failed", str(actual["error"])

    if desired["version"] == actual["version"]:
        # Versions can agree while the shield is not up: the app refuses a
        # focus state it considers unsafe and still reports the version. A
        # requested lock with no shield is a failure, not convergence.
        if desired["mode"] != "off" and not actual.get("shielded"):
            return "failed", "the phone acknowledged the mode but is not shielded"
        return "converged", ""

    last_seen = actual.get("last_seen")
    if not last_seen:
        return "never_seen", "the phone has never checked in"

    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return "unknown", "unreadable last_seen"

    age_min = (clock.now() - seen).total_seconds() / 60
    if age_min > STALE_AFTER_MINUTES:
        return "stale", f"last heard from the phone {int(age_min)}m ago"

    since = desired.get("since")
    if since:
        try:
            # It reported *after* this mode was published and still did not
            # apply it — it saw the request and could not carry it out.
            if seen > datetime.fromisoformat(since):
                return "diverged", (
                    f"the phone is online but still on v{actual['version']} "
                    f"(asked for v{desired['version']}) — it could not apply this"
                )
        except ValueError:
            pass
    return "pending", "waiting for the phone to pick it up"


def snapshot() -> dict[str, Any]:
    """The ``ios`` block of ``/v1/status``: what Argon wants, what the phone did."""
    state, detail = convergence()
    active, until = override_status()
    return {
        "desired": get_mode(),
        "actual": get_actual(),
        "convergence": {"state": state, "detail": detail},
        "override": {"active": active, "until": until},
    }
