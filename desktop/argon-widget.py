#!/usr/bin/env python3
"""Argon desktop readout — one fetch, two front-ends.

Run bare it prints `SwiftBar <https://swiftbar.app>` plugin format; run with
``--json`` it prints a flat JSON object for the Übersicht widget.  Both call
this same file, so the menu bar and the desktop can never disagree about what
Argon thinks is happening.

SwiftBar reads its refresh interval out of the plugin's *filename*, so install.sh
symlinks this file in as ``argon.10s.py``; renaming that symlink is how you slow
the menu bar down. Übersicht's interval lives in ``argon.jsx`` instead.

Config, first match wins:

    ARGON_URL / ARGON_TOKEN in the environment
    ~/.config/argon/desktop.json   {"url": "http://host:3995", "token": "..."}

Deliberately stdlib-only and Python 3.9-compatible: SwiftBar runs plugins from
launchd, whose PATH usually finds /usr/bin/python3 (3.9) rather than Homebrew's.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "argon" / "desktop.json"
TIMEOUT_S = 4.0

# Menu bar glyph per desired focus mode. `off` is deliberately quiet.
MODE_ICON = {
    "off": "○", "school": "🎓", "homework": "📓", "lock_in": "🔒", "sleep": "🌙",
}
PRIORITY_ICON = {"high": "🔴", "medium": "🟡", "low": "⚪️"}
# Convergence states that mean "what Argon asked for is not what the phone did".
BAD_CONVERGENCE = {"diverged", "failed"}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def load_config():
    url = os.environ.get("ARGON_URL", "")
    token = os.environ.get("ARGON_TOKEN", "")
    if not (url and token):
        try:
            data = json.loads(CONFIG_PATH.read_text())
            url = url or data.get("url", "")
            token = token or data.get("token", "")
        except (OSError, ValueError):
            pass
    return url.rstrip("/"), token


def get(url, token, path):
    req = urllib.request.Request(
        url + path, headers={"Authorization": "Bearer " + token}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect():
    """Everything both front-ends render, or ``{"error": ...}``."""
    url, token = load_config()
    if not url or not token:
        return {"error": "not configured — see " + str(CONFIG_PATH)}
    try:
        status = get(url, token, "/v1/status")
    except urllib.error.HTTPError as e:
        return {"error": "HTTP {} from /v1/status".format(e.code)}
    except Exception as e:  # noqa: BLE001 — offline is the common case, not a crash
        return {"error": str(e)}

    # Tasks are allowed to fail on their own: a dead Google grant should not
    # cost you the focus-mode readout, which comes from local state only.
    try:
        tasks = get(url, token, "/v1/tasks")
    except Exception as e:  # noqa: BLE001
        tasks = {"tasks": [], "count": 0, "error": str(e)}

    status["tasks"] = tasks.get("tasks", [])
    status["tasks_error"] = tasks.get("error")
    status["tasks_cached"] = tasks.get("cached", False)
    status["fetched_at"] = now().isoformat(timespec="seconds")
    return enrich(status)


def enrich(d):
    """Add the fields that need a clock, so the front-ends only lay out.

    Übersicht renders in WebKit with no access to the server's timezone, and
    SwiftBar would otherwise compute the same strings a second time. Doing it
    once here is what keeps the two displays saying the same thing.
    """
    desired = d.get("ios", {}).get("desired", {}) or {}
    end, remaining = parse(desired.get("expires_at")), left(desired.get("expires_at"))
    desired["until"] = (
        "{} · {}".format(end.strftime("%-I:%M %p"), remaining) if end else None
    )
    desired["since_ago"] = ago(desired.get("since"))
    (d.get("ios", {}).get("actual", {}) or {})["last_seen_ago"] = ago(
        (d.get("ios", {}).get("actual") or {}).get("last_seen")
    )

    for task in d.get("tasks", []):
        due = due_label(task.get("due"))
        bits = [b for b in (task.get("subject"), due) if b]
        if task.get("time_estimate_min"):
            bits.append("~{}m".format(task["time_estimate_min"]))
        started = parse(task.get("started_at"))
        if started:
            bits.append("running " + span((now() - started).total_seconds()))
        task["meta"] = " · ".join(bits)
        task["overdue"] = bool(due and due.startswith("overdue"))
    return d


# ---------------------------------------------------------------------------
# Time formatting
# ---------------------------------------------------------------------------

def now():
    return datetime.now(timezone.utc).astimezone()


def parse(stamp):
    """ISO 8601 -> aware datetime, or None. Tolerates a trailing ``Z``."""
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=now().tzinfo)


def span(seconds):
    """Coarse duration: ``2d``, ``3h14m``, ``42m``, ``18s``."""
    seconds = int(abs(seconds))
    if seconds >= 86400:
        return "{}d".format(seconds // 86400)
    if seconds >= 3600:
        return "{}h{:02d}m".format(seconds // 3600, (seconds % 3600) // 60)
    if seconds >= 60:
        return "{}m".format(seconds // 60)
    return "{}s".format(seconds)


def ago(stamp):
    dt = parse(stamp)
    return "—" if dt is None else span((now() - dt).total_seconds()) + " ago"


def left(stamp):
    """Time until *stamp*, or None if it is absent or already past."""
    dt = parse(stamp)
    if dt is None:
        return None
    delta = (dt - now()).total_seconds()
    return span(delta) + " left" if delta > 0 else "expired"


def due_label(stamp):
    """Google Tasks due dates are date-only, stored as midnight UTC.

    Localising them would shift the day backwards for anyone west of London —
    a task due Aug 5 would read "Aug 4, 5pm". Compare calendar dates instead.
    """
    dt = parse(stamp)
    if dt is None:
        return None
    days = (dt.date() - now().date()).days
    if days < 0:
        return "overdue {}d".format(-days)
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days < 7:
        return dt.strftime("%a")
    return dt.strftime("%b %-d")


# ---------------------------------------------------------------------------
# SwiftBar
# ---------------------------------------------------------------------------

def bar(text, **params):
    """One SwiftBar line. Pipes in the text would be read as a parameter split."""
    line = str(text).replace("|", "¦")
    # Filter before testing: every caller passes color=None on the common path,
    # which is a non-empty dict and would otherwise emit a trailing " | ".
    set_params = ["{}={}".format(k, v) for k, v in params.items() if v is not None]
    if set_params:
        line += " | " + " ".join(set_params)
    return line


def render_swiftbar(d):
    out = []
    if d.get("error"):
        out.append(bar("Argon ⚠︎", color="orange"))
        out.append("---")
        out.append(bar(d["error"], color="orange"))
        out.append(bar("Refresh", refresh="true"))
        print("\n".join(out))
        return

    desired = d.get("ios", {}).get("desired", {}) or {}
    actual = d.get("ios", {}).get("actual", {}) or {}
    conv = d.get("ios", {}).get("convergence", {}) or {}
    mode = desired.get("mode", "off")
    tasks = d.get("tasks", [])

    # -- title -------------------------------------------------------------
    title = MODE_ICON.get(mode, "?")
    remaining = left(desired.get("expires_at"))
    if mode != "off" and remaining:
        title += " " + remaining.replace(" left", "")
    if tasks:
        title += "  ✓{}".format(len(tasks))
    if conv.get("state") in BAD_CONVERGENCE:
        title += " ⚠︎"
    out.append(bar(title, color="orange" if conv.get("state") in BAD_CONVERGENCE else None))
    out.append("---")

    # -- focus -------------------------------------------------------------
    out.append(bar("Focus", size=13, color="gray"))
    out.append(bar("Mode: {}  (v{})".format(mode, desired.get("version", "?"))))
    if desired.get("reason"):
        out.append(bar("Reason: " + desired["reason"]))
    if desired.get("until"):
        out.append(bar("Until: " + desired["until"]))
    out.append(bar("Early exit: {}".format(
        "allowed" if desired.get("allow_early_end") else "blocked")))
    out.append(bar("Since: " + desired.get("since_ago", "—")))

    # -- phone -------------------------------------------------------------
    out.append("---")
    out.append(bar("Phone", size=13, color="gray"))
    state = conv.get("state", "?")
    out.append(bar("Applied: {}  (v{}){}".format(
        actual.get("mode", "?"), actual.get("version", "?"),
        "  shielded" if actual.get("shielded") else ""),
        color="orange" if state in BAD_CONVERGENCE else None))
    out.append(bar("Convergence: " + state,
                   color="orange" if state in BAD_CONVERGENCE else None))
    if conv.get("detail"):
        out.append(bar(conv["detail"], size=11, color="gray"))
    if actual.get("error"):
        out.append(bar("Error: " + str(actual["error"]), color="red"))
    battery = actual.get("battery")
    if isinstance(battery, (int, float)) and battery >= 0:
        out.append(bar("Battery: {}%".format(int(battery * 100))))
    out.append(bar("Last seen: " + actual.get("last_seen_ago", "—")))

    # -- session -----------------------------------------------------------
    out.append("---")
    out.append(bar("Session", size=13, color="gray"))
    out.append(bar("State: " + str(d.get("mode", "idle"))))
    if d.get("current_task"):
        out.append(bar("Doing: " + d["current_task"]))
    if d.get("work_session_minutes"):
        out.append(bar("Working: {}m".format(d["work_session_minutes"])))
    if d.get("lock_in_minutes"):
        out.append(bar("Locked in: {}m".format(d["lock_in_minutes"])))
    if d.get("home_arrival"):
        out.append(bar("Home since: " + ago(d["home_arrival"])))
    period = d.get("school_period") or {}
    if period.get("status") == "in_period":
        out.append(bar("{} · ends {} ({}m)".format(
            period.get("period"), period.get("ends_at"),
            period.get("minutes_remaining"))))

    # -- checklist ---------------------------------------------------------
    out.append("---")
    out.append(bar("Checklist ({}){}".format(
        len(tasks), " · cached" if d.get("tasks_cached") else ""),
        size=13, color="gray"))
    if d.get("tasks_error"):
        out.append(bar("Unavailable: " + str(d["tasks_error"]), color="orange"))
    for task in tasks:
        label = "{} {}".format(PRIORITY_ICON.get(task.get("priority"), "⚪️"),
                               task.get("title", "?"))
        if task.get("meta"):
            label += "  · " + task["meta"]
        out.append(bar(label, color="red" if task.get("overdue") else None))
        if task.get("notes"):
            out.append(bar("-- " + task["notes"].splitlines()[0], size=11, color="gray"))
    if not tasks and not d.get("tasks_error"):
        out.append(bar("Nothing pending", color="gray"))

    out.append("---")
    out.append(bar("Updated " + now().strftime("%-I:%M:%S %p"), size=11, color="gray"))
    out.append(bar("Refresh", refresh="true"))
    print("\n".join(out))


# ---------------------------------------------------------------------------

def selftest():
    """Smallest thing that fails if the formatting logic breaks. No network."""
    from datetime import timedelta

    assert span(45) == "45s" and span(60 * 42) == "42m"
    assert span(3600 * 3 + 60 * 14) == "3h14m" and span(86400 * 2) == "2d"

    # A trailing Z is not ISO 8601 as far as Python 3.9 is concerned.
    assert parse("2026-08-02T08:39:32Z") is not None
    assert parse("2026-08-02T01:39:32-07:00") is not None
    assert parse("") is None and parse("not a date") is None

    # Google Tasks stores due dates as midnight UTC. Localising them west of
    # London slides the day backwards — this is the assertion that catches it.
    today = now().date()
    assert due_label(today.strftime("%Y-%m-%dT00:00:00.000Z")) == "today"
    assert due_label((today + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00.000Z")) == "tomorrow"
    assert due_label((today - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00.000Z")) == "overdue 2d"

    # A pipe in a task title would otherwise be read as SwiftBar's param separator.
    assert bar("a | b") == "a ¦ b"
    assert bar("x", color="red") == "x | color=red"
    assert bar("x", color=None) == "x"

    soon = (now() + timedelta(hours=1, minutes=30)).isoformat()
    d = enrich({
        "ios": {"desired": {"expires_at": soon, "since": None}, "actual": {}},
        "tasks": [{"title": "t", "due": (today - timedelta(days=1)).strftime(
            "%Y-%m-%dT00:00:00.000Z"), "subject": "AP Chem", "time_estimate_min": 45}],
    })
    # "9:04 PM · 1h29m left" — asserting the exact remainder would race the
    # clock, since `left` is measured microseconds after `soon` is built.
    assert d["ios"]["desired"]["until"].endswith("left")
    assert " · " in d["ios"]["desired"]["until"]
    assert d["tasks"][0]["overdue"] is True
    assert d["tasks"][0]["meta"] == "AP Chem · overdue 1d · ~45m"

    # Both renderers must survive a payload with nothing in it.
    render_swiftbar(enrich({"ios": {}, "tasks": []}))
    print("ok")


def main():
    if "--selftest" in sys.argv:
        selftest()
    elif "--json" in sys.argv:
        print(json.dumps(collect()))
    else:
        render_swiftbar(collect())


if __name__ == "__main__":
    main()
