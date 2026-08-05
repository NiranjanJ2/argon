#!/usr/bin/env python3
"""Argon desktop readout — one fetch, one view model, two front-ends.

Run bare it prints `SwiftBar <https://swiftbar.app>` plugin format; run with
``--json`` it prints the view model for the Übersicht widget.  Both render the
same object, so the menu bar and the desktop cannot disagree.

The split is deliberate.  ``build_view`` decides *what the readout says* — mode
wording, task grouping, sort order, every string that needs a clock.  The two
renderers only decide how it looks.  Übersicht runs in WebKit with no notion of
the server's timezone, and two front-ends computing their own countdowns is
exactly how two displays start disagreeing.

Presentation vocabulary is mirrored from the iOS app rather than reinvented:
``Foqos/Utils/ArgonDesign.swift`` for the palette, ``Views/ArgonDashboardView``
for the mode wording, task grouping and sort, ``Components/Dashboard/
ArgonStatusCard`` for the metric strip.  Changing a colour there means changing
PALETTE here.

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
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "argon" / "desktop.json"
TIMEOUT_S = 4.0
#: Writes go through Google Tasks, which is slower than reading a cached list.
ACTION_TIMEOUT_S = 12.0
SELF = Path(__file__).resolve()

# ---------------------------------------------------------------------------
# Design tokens — mirrored from Foqos/Utils/ArgonDesign.swift
# ---------------------------------------------------------------------------

PALETTE = {
    "canvas": "#040812",
    "canvasLifted": "#081326",
    "surface": "#0C1729",
    "surfaceRaised": "#12213A",
    "electricBlue": "#5DA9FF",
    "iceBlue": "#A9DDFF",
    "cobalt": "#275DFF",
    "cyan": "#65D8FF",
    "ink": "#F4F8FF",
    "mutedInk": "#9BAAC0",
    "warning": "#FF9F45",
    "danger": "#FF6B6B",
}

#: Session mode -> (hero label, status-card label, SF Symbol).
#: ArgonDashboardView.modeLabel and ArgonStatusCard.modeIcon, kept in step.
SESSION_MODE = {
    "lock_in": ("Locked in", "LOCKED IN", "lock.fill"),
    "working": ("In motion", "WORKING", "sparkles"),
    "napping": ("Recharging", "RECHARGING", "moon.zzz.fill"),
    "done": ("Day complete", "DAY COMPLETE", "checkmark.seal.fill"),
    "idle": ("At ease", "AT EASE", "moon.stars.fill"),
}

#: Desired Screen Time mode -> (label, SF Symbol).
FOCUS_MODE = {
    "off": ("Open", "bolt.slash.fill"),
    "school": ("School", "graduationcap.fill"),
    "homework": ("Homework", "book.fill"),
    "lock_in": ("Locked in", "lock.fill"),
    "sleep": ("Sleep", "moon.stars.fill"),
}

#: ArgonTaskRow.priorityColor.
PRIORITY_TINT = {
    "high": PALETTE["warning"],
    "medium": PALETTE["iceBlue"],
    "low": PALETTE["mutedInk"],
}

#: Convergence states meaning the phone is not doing what Argon asked.
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


def send(url, token, path, body, method="POST"):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=ACTION_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect():
    """Raw server state, or ``{"error": ...}``."""
    url, token = load_config()
    if not url or not token:
        return {"error": "Not configured — see " + str(CONFIG_PATH)}
    try:
        status = get(url, token, "/v1/status")
    except urllib.error.HTTPError as e:
        reason = "Token rejected" if e.code == 401 else "HTTP {}".format(e.code)
        return {"error": reason + " from /v1/status"}
    except Exception as e:  # noqa: BLE001 — offline is a state, not a crash
        return {"error": str(e)}

    # Tasks may fail on their own: a dead Google grant should not cost the
    # focus readout, which is served from local state and cannot be affected.
    try:
        payload = get(url, token, "/v1/tasks")
        status["tasks"] = payload.get("tasks", [])
        status["tasks_state"] = payload.get("state", {})
        status["tasks_cached"] = payload.get("cached", False)
        status["tasks_error"] = payload.get("error")
    except Exception as e:  # noqa: BLE001
        status["tasks"] = []
        status["tasks_state"] = {}
        status["tasks_cached"] = False
        status["tasks_error"] = str(e)
    return status


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
    return None if dt is None else span((now() - dt).total_seconds()) + " ago"


def left(stamp):
    """Time until *stamp*, or None if absent. ``expired`` once past."""
    dt = parse(stamp)
    if dt is None:
        return None
    delta = (dt - now()).total_seconds()
    return span(delta) + " left" if delta > 0 else "expired"


def due_bucket(stamp):
    """``(bucket, label)`` for a Google Tasks due date.

    Due dates are date-only, stored as midnight UTC. Localising them slides the
    day backwards anywhere west of London — a task due Aug 5 would read
    "Aug 4, 5pm". Compare calendar dates, never instants.
    """
    dt = parse(stamp)
    if dt is None:
        return "later", None
    days = (dt.date() - now().date()).days
    if days < 0:
        return "overdue", "overdue {}d".format(-days)
    if days == 0:
        return "today", "today"
    if days == 1:
        return "later", "tomorrow"
    if days < 7:
        return "later", dt.strftime("%a")
    return "later", dt.strftime("%b %-d")


# ---------------------------------------------------------------------------
# View model — the single source of what the readout says
# ---------------------------------------------------------------------------

def build_view(d):
    """Turn raw server state into everything both renderers display."""
    if d.get("error"):
        return {"ok": False, "error": d["error"], "updated": now().strftime("%-I:%M:%S %p")}

    ios = d.get("ios") or {}
    desired = ios.get("desired") or {}
    actual = ios.get("actual") or {}
    conv = ios.get("convergence") or {}

    session_key = d.get("mode") or "idle"
    hero_label, status_label, session_icon = SESSION_MODE.get(
        session_key, SESSION_MODE["idle"]
    )
    focus_key = desired.get("mode", "off")
    focus_label, focus_icon = FOCUS_MODE.get(focus_key, (focus_key, "questionmark"))

    remaining = left(desired.get("expires_at"))
    end = parse(desired.get("expires_at"))
    drift = conv.get("state") in BAD_CONVERGENCE

    view = {
        "ok": True,
        "hero": {
            "eyebrow": status_label,
            "title": d.get("current_task") or "Argon is standing by",
            "icon": session_icon,
            "mode": session_key,
            "label": hero_label,
        },
        "metrics": [
            {"value": "{}m".format(d.get("work_session_minutes") or 0),
             "label": "FOCUS", "icon": "timer"},
            {"value": "{}m".format(d.get("lock_in_minutes") or 0),
             "label": "LOCKED", "icon": "lock.fill"},
            {"value": str(len(d.get("tasks") or [])),
             "label": "OPEN", "icon": "checklist"},
        ],
        "focus": {
            "label": focus_label,
            "icon": focus_icon,
            "mode": focus_key,
            "version": desired.get("version"),
            "reason": desired.get("reason") or None,
            "until": ("{} · {}".format(end.strftime("%-I:%M %p"), remaining)
                      if end and remaining else None),
            "early_exit": "Allowed" if desired.get("allow_early_end") else "Blocked",
            "since": ago(desired.get("since")),
            "shielded": bool(actual.get("shielded")),
        },
        "phone": {
            "applied": "{} · v{}".format(actual.get("mode", "?"), actual.get("version", "?")),
            "convergence": conv.get("state") or "unknown",
            # On `stale` the server's detail is "last heard from the phone
            # 1213m ago" — the same fact as `last_seen`, in raw minutes. Drop
            # it there and keep it where it says something else, like
            # "answered after the request, still on v40".
            "detail": (conv.get("detail") or None) if conv.get("state") != "stale" else None,
            "error": actual.get("error") or None,
            "last_seen": ago(actual.get("last_seen")),
            "drift": drift,
        },
        "groups": group_tasks(d.get("tasks") or []),
        "notice": task_notice(d),
        "alert": phone_alert(conv, actual) if drift else None,
        "cached": bool(d.get("tasks_cached")),
        "updated": now().strftime("%-I:%M:%S %p"),
    }

    period = d.get("school_period") or {}
    if period.get("status") == "in_period":
        view["period"] = "{} · ends {} ({}m)".format(
            period.get("period"), period.get("ends_at"), period.get("minutes_remaining")
        )
    return view


def phone_alert(conv, actual):
    """One line stating a block did not land. Nothing subtle about it."""
    if actual.get("error"):
        return "Phone reported: {}".format(actual["error"])
    return "Phone has not applied this — {}".format(conv.get("state"))


def task_notice(d):
    if d.get("tasks_error"):
        return {"tone": "warning", "text": "Checklist unavailable — " + str(d["tasks_error"])}
    if not d.get("tasks"):
        return {"tone": "calm", "text": "Clear runway"}
    return None


def group_tasks(tasks):
    """Overdue / Today / Later, matching ArgonDashboardView's sections."""
    buckets = {"overdue": [], "today": [], "later": []}
    for task in tasks:
        bucket, label = due_bucket(task.get("due"))
        meta = [b for b in (task.get("subject"), label) if b]
        if task.get("time_estimate_min"):
            meta.append("~{}m".format(task["time_estimate_min"]))
        # "running" comes from the server's session now. It used to be derived
        # from a per-task start stamp that had no day boundary, so a task left
        # open overnight still showed as running the next evening.
        if task.get("running"):
            minutes = task.get("running_minutes")
            meta.append("running " + span(minutes * 60) if minutes else "running")
        buckets[bucket].append({
            "id": task.get("id"),
            "title": task.get("title") or "Untitled",
            "priority": (task.get("priority") or "medium"),
            "tint": PRIORITY_TINT.get(task.get("priority"), PALETTE["iceBlue"]),
            "meta": " · ".join(meta),
            "started": bool(started),
            "overdue": bucket == "overdue",
            "notes": (task.get("notes") or "").splitlines()[0] if task.get("notes") else None,
        })

    order = {"high": 0, "medium": 1, "low": 2}
    for items in buckets.values():
        # ArgonDashboardView.sorted: started first, then priority, due, title.
        items.sort(key=lambda t: (not t["started"], order.get(t["priority"], 3), t["title"]))

    tints = {"overdue": PALETTE["warning"], "today": PALETTE["iceBlue"],
             "later": PALETTE["mutedInk"]}
    return [
        {"title": name.capitalize(), "tint": tints[name], "tasks": buckets[name]}
        for name in ("overdue", "today", "later") if buckets[name]
    ]


# ---------------------------------------------------------------------------
# SwiftBar renderer
# ---------------------------------------------------------------------------

def bar(text, **params):
    """One SwiftBar line. A pipe in the text would be read as a param split."""
    line = str(text).replace("|", "¦")
    # Filter before testing: callers pass color=None on the common path, and a
    # non-empty dict of Nones would still emit a trailing " | ".
    set_params = ["{}={}".format(k, v) for k, v in params.items() if v is not None]
    if set_params:
        line += " | " + " ".join(set_params)
    return line


def render_swiftbar(view):
    out = []

    if not view.get("ok"):
        out.append(bar("Argon", sfimage="bolt.slash.fill", sfcolor=PALETTE["warning"]))
        out.append("---")
        out.append(bar(view["error"], color=PALETTE["warning"], sfimage="exclamationmark.triangle"))
        out.append(bar("Refresh", refresh="true", sfimage="arrow.clockwise"))
        print("\n".join(out))
        return

    hero, focus, phone = view["hero"], view["focus"], view["phone"]

    # -- menu bar title ----------------------------------------------------
    title_bits = []
    if focus["mode"] != "off":
        title_bits.append(focus["until"].split(" · ")[-1].replace(" left", "")
                          if focus["until"] else focus["label"])
    open_tasks = sum(len(g["tasks"]) for g in view["groups"])
    if open_tasks:
        title_bits.append("{}".format(open_tasks))
    out.append(bar(
        " ".join(title_bits),
        sfimage=focus["icon"] if focus["mode"] != "off" else hero["icon"],
        sfcolor=PALETTE["warning"] if view.get("alert") else PALETTE["iceBlue"],
    ))
    out.append("---")

    # -- hero --------------------------------------------------------------
    out.append(bar(hero["eyebrow"], color=PALETTE["iceBlue"], size=10))
    out.append(bar(hero["title"], color=PALETTE["ink"], size=15, font="Georgia"))
    out.append(bar(
        "  ".join("{} {}".format(m["value"], m["label"].lower()) for m in view["metrics"]),
        color=PALETTE["mutedInk"], size=11,
    ))
    if view.get("period"):
        out.append(bar(view["period"], color=PALETTE["mutedInk"], size=11))
    if view.get("alert"):
        out.append(bar(view["alert"], color=PALETTE["warning"], size=11,
                       sfimage="exclamationmark.triangle.fill", sfcolor=PALETTE["warning"]))

    # -- checklist ---------------------------------------------------------
    out.append("---")
    notice = view.get("notice")
    if notice:
        out.append(bar(notice["text"], size=12,
                       color=PALETTE["warning"] if notice["tone"] == "warning"
                       else PALETTE["mutedInk"]))
    for group in view["groups"]:
        out.append(bar("{}  {}".format(group["title"], len(group["tasks"])),
                       color=group["tint"], size=12, font="Georgia"))
        for task in group["tasks"]:
            # The row is a submenu parent, so its actions need a deliberate
            # second click. Completing on a stray click would be unrecoverable
            # from here — there is no un-complete in the task store.
            out.append(bar(
                "   " + task["title"],
                color=PALETTE["ink"], size=13, font="Georgia",
                sfimage="play.fill" if task["started"] else "circle",
                sfcolor=task["tint"],
            ))
            detail = task["priority"].upper()
            if task["meta"]:
                detail += " · " + task["meta"]
            out.append(bar("--" + detail, color=PALETTE["mutedInk"], size=11))
            out.append("-----")
            if not task["started"]:
                out.append(bar("--Start", sfimage="play.fill",
                               **action_params("start", task["id"])))
            out.append(bar("--Complete", sfimage="checkmark.circle",
                           **action_params("complete", task["id"], task["title"])))
            out.append(bar("--Due tomorrow", sfimage="calendar.badge.clock",
                           **action_params("tomorrow", task["id"])))
            out.append(bar("--Priority", sfimage="flag"))
            for level in ("high", "medium", "low"):
                out.append(bar("----" + level.capitalize(),
                               **action_params("priority", task["id"], level)))

    # -- focus and phone, one level down -----------------------------------
    out.append("---")
    out.append(bar("{}  ·  {}".format(focus["label"], phone["convergence"]),
                   color=PALETTE["iceBlue"], size=12,
                   sfimage=focus["icon"], sfcolor=PALETTE["iceBlue"]))
    if focus["reason"]:
        out.append(bar("--" + focus["reason"], color=PALETTE["mutedInk"], size=11))
    for label, value in (
        ("Mode", "{} (v{})".format(focus["label"], focus["version"])
                 if focus["version"] is not None else focus["label"]),
        ("Until", focus["until"]),
        ("Early exit", focus["early_exit"]),
        ("Set", focus["since"]),
        ("Phone", phone["applied"]),
        ("Converged", phone["convergence"]),
        ("Detail", phone["detail"]),
        ("Error", phone["error"]),
        ("Last seen", phone["last_seen"]),
    ):
        if value:
            out.append(bar("--{}: {}".format(label, value),
                           color=PALETTE["danger"] if label == "Error" else None))

    out.append("---")
    out.append(bar("Add a task…", sfimage="plus.circle", size=12,
                   **action_params("add")))
    # Always offered, never conditional on a lock being visible: an escape
    # hatch you can only reach when the UI agrees you are locked is not one.
    out.append(bar("Release blocks", sfimage="lock.open", size=12,
                   **action_params("unlock")))
    out.append(bar("Refresh", refresh="true", sfimage="arrow.clockwise", size=12))
    out.append(bar("Updated {}{}".format(view["updated"], " · cached" if view["cached"] else ""),
                   color=PALETTE["mutedInk"], size=10))
    print("\n".join(out))


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def osa(script):
    """Run one AppleScript, returning stdout. Empty on any failure."""
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else ""


def notify(text, title="Argon"):
    """A macOS notification is the only way an action can report anything.

    Both hosts run these detached — SwiftBar discards a plugin's stdout when it
    is invoked as an action, and Übersicht throws away the result of run().
    Without this a failed write is indistinguishable from a successful one.
    """
    osa("display notification {} with title {}".format(json.dumps(text), json.dumps(title)))


def do_action(argv):
    """Perform one mutation. ``argv`` is everything after ``--do``.

    Deliberately the same HTTP surface the iOS app uses, which routes writes
    through Argon's own tool classes — so a task completed from the menu bar
    gets the same daily-log and habit side effects as one completed by asking.
    """
    if not argv:
        notify("No action given")
        return 2

    verb = argv[0]
    url, token = load_config()
    if not url or not token:
        notify("Not configured — see " + str(CONFIG_PATH))
        return 1

    def task_path(task_id):
        return "/v1/tasks/" + urllib.parse.quote(task_id, safe="")

    try:
        if verb in ("start", "complete") and len(argv) > 1:
            send(url, token, task_path(argv[1]), {"action": verb}, "PATCH")
            if verb == "complete":
                notify("Completed {}".format(argv[2] if len(argv) > 2 else "task"))
        elif verb == "tomorrow" and len(argv) > 1:
            due = (now() + timedelta(days=1)).strftime("%Y-%m-%d")
            send(url, token, task_path(argv[1]), {"due": due}, "PATCH")
        elif verb == "priority" and len(argv) > 2:
            send(url, token, task_path(argv[1]), {"priority": argv[2]}, "PATCH")
        elif verb == "add":
            title = osa(
                'text returned of (display dialog "Add a task" default answer "" '
                'with title "Argon" buttons {"Cancel", "Add"} default button "Add")'
            )
            if not title:
                return 0  # cancelled, which is not a failure
            send(url, token, "/v1/tasks", {"title": title, "priority": "medium"})
        elif verb == "unlock":
            minutes = int(argv[1]) if len(argv) > 1 else 120
            send(url, token, "/v1/ios/override",
                 {"minutes": minutes, "source": "desktop"})
            notify("Blocks released and held off for {} minutes".format(minutes))
        else:
            notify("Unknown action: " + verb)
            return 2
    except urllib.error.HTTPError as e:
        notify("{} failed — HTTP {}".format(verb, e.code))
        return 1
    except Exception as e:  # noqa: BLE001 — never leave a click unexplained
        notify("{} failed — {}".format(verb, e))
        return 1
    return 0


def action_params(*args):
    """SwiftBar's bash=/paramN= encoding for one ``--do`` call."""
    argv = [str(SELF), "--do"] + [str(a) for a in args]
    params = {"bash": argv[0], "terminal": "false", "refresh": "true"}
    for i, value in enumerate(argv[1:], start=1):
        params["param{}".format(i)] = json.dumps(value)
    return params


# ---------------------------------------------------------------------------

def selftest():
    """Smallest thing that fails if the view logic breaks. No network."""
    from datetime import timedelta

    assert span(45) == "45s" and span(60 * 42) == "42m"
    assert span(3600 * 3 + 60 * 14) == "3h14m" and span(86400 * 2) == "2d"

    # A trailing Z is not ISO 8601 as far as Python 3.9 is concerned.
    assert parse("2026-08-02T08:39:32Z") is not None
    assert parse("2026-08-02T01:39:32-07:00") is not None
    assert parse("") is None and parse("not a date") is None

    # Google's date-only due stamps are midnight UTC; localising them slides
    # the day backwards. This is the assertion that catches it.
    today = now().date()
    stamp = lambda d: (today + timedelta(days=d)).strftime("%Y-%m-%dT00:00:00.000Z")  # noqa: E731
    assert due_bucket(stamp(0)) == ("today", "today")
    assert due_bucket(stamp(1)) == ("later", "tomorrow")
    assert due_bucket(stamp(-2)) == ("overdue", "overdue 2d")
    assert due_bucket(None) == ("later", None)

    # A pipe in a task title would be read as SwiftBar's param separator.
    assert bar("a | b") == "a ¦ b"
    assert bar("x", color="red") == "x | color=red"
    assert bar("x", color=None) == "x"

    view = build_view({
        "mode": "lock_in",
        "current_task": "SAT prep",
        "work_session_minutes": None,
        "lock_in_minutes": 61,
        "ios": {
            "desired": {"mode": "lock_in", "version": 4, "allow_early_end": False,
                        "expires_at": (now() + timedelta(hours=1)).isoformat(),
                        "since": (now() - timedelta(minutes=5)).isoformat()},
            "actual": {"mode": "off", "version": 3, "shielded": False},
            "convergence": {"state": "diverged", "detail": "still on v3"},
        },
        "tasks": [
            {"id": "a", "title": "Late thing", "priority": "high", "due": stamp(-1)},
            {"id": "b", "title": "Due today", "priority": "low", "due": stamp(0),
             "time_estimate_min": 45, "subject": "AP Chem"},
            {"id": "c", "title": "Running", "priority": "medium", "due": stamp(0),
             "started_at": (now() - timedelta(minutes=12)).isoformat()},
        ],
    })

    assert view["hero"]["eyebrow"] == "LOCKED IN" and view["hero"]["icon"] == "lock.fill"
    assert [m["value"] for m in view["metrics"]] == ["0m", "61m", "3"]
    assert [g["title"] for g in view["groups"]] == ["Overdue", "Today"]
    # A started task sorts above a higher-priority one that has not begun.
    assert [t["title"] for t in view["groups"][1]["tasks"]] == ["Running", "Due today"]
    assert view["groups"][1]["tasks"][1]["meta"] == "AP Chem · today · ~45m"
    assert "running 12m" in view["groups"][1]["tasks"][0]["meta"]
    # Divergence must be stated, not implied by a colour.
    assert view["alert"] and "not applied" in view["alert"]
    assert view["focus"]["early_exit"] == "Blocked"

    # Actions: SwiftBar splits paramN on spaces unless each is quoted, so a
    # task titled "SAT prep" would otherwise arrive as two arguments.
    params = action_params("complete", "id-1", "SAT prep")
    assert params["bash"] == str(SELF)
    assert params["param1"] == '"--do"' and params["param2"] == '"complete"'
    assert params["param4"] == '"SAT prep"'
    assert params["terminal"] == "false" and params["refresh"] == "true"

    # Empty and broken payloads must both still render.
    render_swiftbar(build_view({"ios": {}, "tasks": []}))
    render_swiftbar(build_view({"error": "offline"}))
    assert build_view({"tasks": []})["notice"]["text"] == "Clear runway"
    print("ok")


def main():
    if "--do" in sys.argv:
        sys.exit(do_action(sys.argv[sys.argv.index("--do") + 1:]))
    if "--selftest" in sys.argv:
        selftest()
    elif "--json" in sys.argv:
        print(json.dumps(build_view(collect())))
    else:
        render_swiftbar(build_view(collect()))


if __name__ == "__main__":
    main()
