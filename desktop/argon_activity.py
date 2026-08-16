#!/usr/bin/env python3
"""When the desktop readouts are allowed to touch the network.

One file, honoured by every surface on this Mac — the Übersicht widgets, the
SwiftBar plugin and the native menu bar app — so pausing means pausing, not
pausing one of three things that are all polling.

Why this exists: the readout costs about 0.06s of CPU per refresh, and at two
widgets on a five second timer plus a menu bar plugin that is 43,200 refreshes
a day, roughly 43 minutes of one core. The raw number understates it, because
waking the CPU every five seconds is what keeps a laptop out of the deep idle
states where battery is actually saved.

Stdlib only and Python 3.9-compatible, for the same reason as the readout:
SwiftBar runs plugins from launchd, whose PATH finds /usr/bin/python3.
"""

from __future__ import annotations

import json
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

ACTIVITY_PATH = Path.home() / ".config" / "argon" / "activity.json"

#: Argon's day runs from the end of school to the start of the next one. He is
#: not using it in class, and a laptop polling through the school day is paying
#: for a readout nobody reads.
DEFAULT_ACTIVE_FROM = "16:00"
DEFAULT_ACTIVE_TO = "07:30"


def _parse_hhmm(value, fallback):
    try:
        hour, minute = str(value).split(":")
        return dtime(int(hour), int(minute))
    except (ValueError, AttributeError):
        return fallback


def load(path=None):
    """The current settings, with defaults filled in. Never raises."""
    path = path or ACTIVITY_PATH
    try:
        data = json.loads(Path(path).read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    return {
        "active_from": data.get("active_from") or DEFAULT_ACTIVE_FROM,
        "active_to": data.get("active_to") or DEFAULT_ACTIVE_TO,
        "paused_until": data.get("paused_until"),
        "schedule_enabled": data.get("schedule_enabled", True),
    }


def save(settings, path=None):
    path = Path(path or ACTIVITY_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")


def in_window(now, start, end):
    """Is *now* inside a daily window that may wrap past midnight?

    16:00–07:30 is the normal case here, so the wrap is the rule rather than the
    edge case: a naive ``start <= now < end`` is false all evening, which would
    have paused Argon during precisely the hours it exists for.
    """
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def status(now=None, path=None):
    """``(active, reason)`` — whether to fetch, and what to say if not."""
    now = now or datetime.now()
    settings = load(path)

    paused_until = settings.get("paused_until")
    if paused_until:
        try:
            until = datetime.fromisoformat(paused_until)
            # Stamps are written with an offset so Swift can read them, but a
            # file from before that change is naive, and comparing the two
            # raises rather than returning False. Normalise to naive local.
            if until.tzinfo is not None:
                until = until.astimezone().replace(tzinfo=None)
            if until > now:
                return False, "Paused until " + until.strftime("%-I:%M %p")
        except ValueError:
            pass  # Unreadable stamp: treat as not paused rather than stuck off.

    if not settings.get("schedule_enabled", True):
        return True, ""

    start = _parse_hhmm(settings["active_from"], dtime(16, 0))
    end = _parse_hhmm(settings["active_to"], dtime(7, 30))
    if in_window(now.time(), start, end):
        return True, ""
    return False, "Asleep until " + start.strftime("%-I:%M %p")


def pause(minutes=None, path=None):
    """Stop fetching. ``minutes=None`` means until explicitly resumed."""
    settings = load(path)
    # A century is "indefinite" without needing a second representation for it,
    # and it still shows a real date if anything ever prints the raw file.
    delta = timedelta(minutes=minutes) if minutes else timedelta(days=36500)
    # astimezone() rather than a bare now(): a naive stamp has no offset, and
    # Swift's ISO8601DateFormatter refuses to parse one — so a pause set from
    # the desktop widget would be silently invisible to the menu bar app, which
    # would carry on polling while claiming to be paused.
    settings["paused_until"] = (
        (datetime.now() + delta).astimezone().replace(microsecond=0).isoformat()
    )
    save(settings, path)
    return settings


def resume(path=None):
    settings = load(path)
    settings["paused_until"] = None
    save(settings, path)
    return settings


def selftest():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "activity.json"

        # The overnight window is the whole point; a naive comparison fails here.
        assert in_window(dtime(18, 0), dtime(16, 0), dtime(7, 30)) is True
        assert in_window(dtime(2, 0), dtime(16, 0), dtime(7, 30)) is True
        assert in_window(dtime(7, 29), dtime(16, 0), dtime(7, 30)) is True
        assert in_window(dtime(7, 30), dtime(16, 0), dtime(7, 30)) is False
        assert in_window(dtime(11, 0), dtime(16, 0), dtime(7, 30)) is False
        assert in_window(dtime(15, 59), dtime(16, 0), dtime(7, 30)) is False

        # Default schedule: quiet during school, awake after it.
        assert status(datetime(2026, 8, 17, 10, 0), path)[0] is False
        assert status(datetime(2026, 8, 17, 16, 30), path)[0] is True
        assert status(datetime(2026, 8, 17, 23, 0), path)[0] is True

        # Pausing beats the schedule, even inside the active window. Checked
        # against the real clock, because pause() stamps the real one — asking
        # about a fake date two days out just reads as an expired pause.
        inside = datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
        pause(30, path)
        assert status(inside, path)[0] is False
        resume(path)
        assert status(inside, path)[0] is True

        # Indefinite pause: no argument, still off tomorrow.
        pause(None, path)
        assert status(inside + timedelta(days=1), path)[0] is False
        resume(path)

        # An expired pause is simply over. Both stamp and clock come from the
        # same instant here — pinning "now" to a fixed hour while stamping the
        # real one is how this assertion accidentally tested the opposite.
        moment = datetime.now().replace(hour=17, minute=0, second=0, microsecond=0)
        save({"paused_until": (moment - timedelta(minutes=1)).isoformat()}, path)
        assert status(moment, path)[0] is True

        # A naive stamp still parses: files written before offsets were added.
        save({"paused_until": (moment + timedelta(minutes=5)).isoformat()}, path)
        assert status(moment, path)[0] is False

        # And an offset-aware one, which is what pause() writes now.
        aware = (moment + timedelta(minutes=5)).astimezone().isoformat()
        save({"paused_until": aware}, path)
        assert status(moment, path)[0] is False

        # Schedule off means always awake.
        save({"schedule_enabled": False}, path)
        assert status(datetime(2026, 8, 17, 10, 0), path)[0] is True
    print("ok")


if __name__ == "__main__":
    selftest()
