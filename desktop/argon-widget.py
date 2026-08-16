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
#: Generous because one call can still be slow: the board serves stale
#: Classroom data and re-crawls behind the request, but the very first read
#: after a server restart has no cache to serve and does the full crawl, which
#: is several seconds. Four seconds turned that into "read operation timed out"
#: once per restart and once per cache expiry.
TIMEOUT_S = 12.0
#: Writes go through Google Tasks, which is slower than reading a cached list.
ACTION_TIMEOUT_S = 12.0
SELF = Path(__file__).resolve()

# The activity gate lives beside this file, in the checkout and in
# ~/.config/argon alike, so the import works from either.
sys.path.insert(0, str(SELF.parent))
import argon_activity  # noqa: E402

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

#: Cloudflare answers "Python-urllib/3.x" with a 403 as a matter of course, so
#: going through the tunnel needs a User-Agent that is not the stdlib default.
#: On the LAN it makes no difference; through the tunnel it is the whole
#: difference between working and a bare "HTTP 403 from /v1/status".
USER_AGENT = "Argon-Widget/1.0 (+https://argon.agentneon.dev)"

#: How long to give the LAN address before deciding he is not at home. Short:
#: on a foreign network the connection is refused or dropped almost at once,
#: and every second here is a second the widget shows nothing.
REACH_TIMEOUT_S = 1.5

#: Remembers which base worked last time, so the common case is one request.
#: Process-local — SwiftBar runs a fresh process per refresh, Übersicht does
#: not, and neither wants a stale answer surviving a network change.
_reachable = None


def load_config():
    """``(bases, token)`` — every base to try, in preference order.

    The LAN address is direct and fast; the public one goes out through
    Cloudflare and back. Trying home first keeps the widget instant at his desk
    and still working from school, which is the entire point of exposing it.
    """
    url = os.environ.get("ARGON_URL", "")
    token = os.environ.get("ARGON_TOKEN", "")
    fallback = os.environ.get("ARGON_URL_REMOTE", "")
    if not (url and token):
        try:
            data = json.loads(CONFIG_PATH.read_text())
            url = url or data.get("url", "")
            token = token or data.get("token", "")
            fallback = fallback or data.get("remoteUrl", "")
        except (OSError, ValueError):
            pass
    bases = [b.rstrip("/") for b in (url, fallback) if b]
    return bases, token


def reach(bases, token, call):
    """Run ``call(base)`` against the first base that answers.

    Sticks to whichever worked last, so the usual refresh is a single request.
    On failure it falls back and re-pins, which is what happens when he opens
    the laptop at school or walks back in the door.
    """
    global _reachable
    ordered = list(bases)
    if _reachable in ordered:
        ordered.remove(_reachable)
        ordered.insert(0, _reachable)

    last = None
    for base in ordered:
        try:
            result = call(base)
        except Exception as exc:  # noqa: BLE001 — try the next base, then report
            last = exc
            continue
        _reachable = base
        return result
    raise last if last else RuntimeError("no server configured")


def get(url, token, path):
    req = urllib.request.Request(
        url + path,
        headers={"Authorization": "Bearer " + token, "User-Agent": USER_AGENT},
    )
    # A LAN address that is not on this network must fail fast, or the widget
    # stalls for the full timeout on every refresh away from home.
    timeout = REACH_TIMEOUT_S if _is_lan(url) else TIMEOUT_S
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_lan(url):
    return "://192.168." in url or "://10." in url or "://localhost" in url


def send(url, token, path, body, method="POST"):
    req = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode("utf-8"),
        method=method,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json",
                 "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=ACTION_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def collect():
    """Raw server state, or ``{"error": ...}``."""
    bases, token = load_config()
    if not bases or not token:
        return {"error": "Not configured — see " + str(CONFIG_PATH)}
    try:
        status = reach(bases, token, lambda b: get(b, token, "/v1/status"))
    except urllib.error.HTTPError as e:
        reason = "Token rejected" if e.code == 401 else "HTTP {}".format(e.code)
        return {"error": reason + " from /v1/status"}
    except Exception as e:  # noqa: BLE001 — offline is a state, not a crash
        return {"error": str(e)}

    # Tasks may fail on their own: a dead Google grant should not cost the
    # focus readout, which is served from local state and cannot be affected.
    try:
        payload = reach(bases, token, lambda b: get(b, token, "/v1/tasks"))
        status["tasks"] = payload.get("tasks", [])
        status["tasks_state"] = payload.get("state", {})
        status["tasks_cached"] = payload.get("cached", False)
        status["tasks_error"] = payload.get("error")
    except Exception as e:  # noqa: BLE001
        status["tasks"] = []
        status["tasks_state"] = {}
        status["tasks_cached"] = False
        status["tasks_error"] = str(e)

    # Argon's open questions. Also allowed to fail alone: an older server has no
    # /v1/inbox at all, and the readout predates it, so a 404 here must leave
    # everything else on screen.
    try:
        payload = reach(bases, token, lambda b: get(b, token, "/v1/inbox"))
        status["inbox"] = payload.get("items", [])
    except Exception:  # noqa: BLE001
        status["inbox"] = []
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
        "now": now_panel(d),
        # Argon's open questions, drawn above everything else. This is the only
        # thing on the desktop waiting on him rather than informing him, and it
        # was the one thing the readout could not show at all.
        "inbox": [
            {"id": i.get("id"), "text": i.get("text") or "",
             "actions": [
                 {"label": a.get("label") or a.get("action") or "?",
                  "action": a.get("action") or "",
                  "task_id": a.get("task_id") or ""}
                 for a in (i.get("actions") or [])
             ]}
            for i in (d.get("inbox") or [])
            if not i.get("answered") and i.get("actions")
        ],
        # `plan` and `due` are gone, deliberately.
        #
        # Plan drew timed blocks. He does not work in blocks — that model was
        # abandoned — so the panel maintained a schedule nothing writes to and
        # sat there permanently reading "Not planned yet".
        #
        # Due listed schoolwork, which on this board *is* the task list: every
        # commitment is Classroom-sourced, so it reprinted `groups` directly
        # underneath `groups`. Two renderings of one list is how a readout stops
        # being read. Urgency already lives on the task row, which says
        # "tomorrow" and turns amber on its own.
        "agenda": [
            {"id": e.get("id"), "summary": e.get("summary") or "(untitled)",
             "when": e.get("when") or "", "location": e.get("location")}
            for e in (d.get("agenda") or [])
        ],
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


#: How many tasks the picker offers when nothing is running. Enough to choose
#: from, few enough that the panel stays a glance rather than a second list.
PICKER_LIMIT = 5


def now_panel(d):
    """What is running, or what could be — the model behind the Now widget.

    Built here rather than in JSX for the same reason everything else is: the
    two readouts render the same object, so they cannot drift apart. The
    running task comes from the server's session, which is the only thing
    entitled to answer "is this in progress".
    """
    tasks = d.get("tasks") or []
    running = next((t for t in tasks if t.get("running")), None)

    if running:
        minutes = running.get("running_minutes") or 0
        return {
            "state": "running",
            "title": running.get("title") or "Untitled",
            "id": running.get("id"),
            "elapsed": span(minutes * 60) if minutes else "just started",
            "subject": running.get("subject"),
            "estimate": ("~{}m".format(running["time_estimate_min"])
                         if running.get("time_estimate_min") else None),
            # A goal only exists if he set one; inventing a target is the kind
            # of made-up pressure that makes a readout worth ignoring.
            "over": bool(running.get("time_estimate_min")
                         and minutes > running["time_estimate_min"]),
        }

    order = {"high": 0, "medium": 1, "low": 2}
    pick = sorted(
        tasks,
        key=lambda t: (order.get(t.get("priority"), 3), t.get("due") or "9999"),
    )[:PICKER_LIMIT]
    return {
        "state": "idle" if pick else "empty",
        "title": "Nothing running",
        "picker": [
            {
                "id": t.get("id"),
                "title": t.get("title") or "Untitled",
                "priority": t.get("priority") or "medium",
                "tint": PRIORITY_TINT.get(t.get("priority"), PALETTE["iceBlue"]),
                "meta": due_bucket(t.get("due"))[1] or "",
            }
            for t in pick
        ],
    }


#: Block status -> (label, SF Symbol, whether it is still ahead of him).
BLOCK_MARK = {
    "pending": ("", "circle", True),
    "done": ("done", "checkmark.circle.fill", False),
    "skipped": ("skipped", "xmark.circle", False),
}


def hhmm_minutes(value):
    """"14:00" -> 840. None for anything unparseable."""
    try:
        hour, minute = str(value).split(":")
        return int(hour) * 60 + int(minute)
    except (AttributeError, TypeError, ValueError):
        return None


def span_label(start, end):
    """"14:00", "16:00" -> "2–4 PM", in the form he reads."""
    def clock12(value):
        total = hhmm_minutes(value)
        if total is None:
            return ""
        hour, minute = divmod(total, 60)
        suffix = "AM" if hour < 12 else "PM"
        display = hour % 12 or 12
        return "{}{} {}".format(display, ":{:02d}".format(minute) if minute else "", suffix)

    first, second = clock12(start), clock12(end)
    if not second:
        return first
    # "2 PM – 4 PM" collapses to "2–4 PM" when they share a half of the day.
    if first[-2:] == second[-2:]:
        return "{}–{}".format(first[:-3], second)
    return "{} – {}".format(first, second)


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
        running = bool(task.get("running"))
        if running:
            minutes = task.get("running_minutes")
            meta.append("running " + span(minutes * 60) if minutes else "running")
        buckets[bucket].append({
            "id": task.get("id"),
            "title": task.get("title") or "Untitled",
            "priority": (task.get("priority") or "medium"),
            "tint": PRIORITY_TINT.get(task.get("priority"), PALETTE["iceBlue"]),
            "meta": " · ".join(meta),
            "started": running,
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

    # -- now: start or stop without opening the checklist -------------------
    panel = view.get("now") or {}
    if panel.get("state") == "running":
        out.append("---")
        out.append(bar("Working on {} · {}".format(panel["title"], panel["elapsed"]),
                       color=PALETTE["cyan"], size=12, sfimage="play.fill",
                       sfcolor=PALETTE["cyan"]))
        out.append(bar("Mark done", size=12, sfimage="checkmark.circle",
                       **action_params("complete", panel["id"], panel["title"])))
        out.append(bar("Put it down", size=12, sfimage="pause.circle",
                       **action_params("stop", panel["id"])))
    elif panel.get("picker"):
        out.append("---")
        out.append(bar("Start working on", color=PALETTE["mutedInk"], size=11))
        for item in panel["picker"]:
            label = item["title"] + (" · " + item["meta"] if item["meta"] else "")
            out.append(bar(label, size=12, color=item["tint"], sfimage="play.circle",
                           **action_params("start", item["id"])))

    # -- Argon's open questions ---------------------------------------------
    # Above the task list, because this is the part waiting on an answer.
    if view.get("inbox"):
        out.append("---")
        out.append(bar("Argon asked", color=PALETTE["mutedInk"], size=11))
        for item in view["inbox"]:
            out.append(bar(item["text"][:80], size=12, color=PALETTE["ink"]))
            for action in item["actions"]:
                if action["task_id"]:
                    out.append(bar(
                        "--" + action["label"], size=11,
                        **action_params(action["action"], action["task_id"])
                    ))

    # -- agenda ------------------------------------------------------------
    if view.get("agenda"):
        out.append("---")
        out.append(bar("Today", color=PALETTE["mutedInk"], size=11))
        for event in view["agenda"]:
            out.append(bar("{} · {}".format(event["summary"], event["when"]),
                           color=PALETTE["ink"], size=12, sfimage="calendar"))

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

    # Handled before the config check: pausing must work even when the server
    # is unreachable, which is exactly when a laptop is burning battery
    # retrying and he most wants it to stop.
    if verb == "pause":
        minutes = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else None
        argon_activity.pause(minutes)
        notify("Paused" + (" for {}m".format(minutes) if minutes else ""))
        return 0
    if verb == "resume":
        argon_activity.resume()
        notify("Resumed")
        return 0

    bases, token = load_config()
    if not bases or not token:
        notify("Not configured — see " + str(CONFIG_PATH))
        return 1

    def post(path, body, method="POST"):
        return reach(bases, token, lambda b: send(b, token, path, body, method))

    def task_path(task_id):
        return "/v1/tasks/" + urllib.parse.quote(task_id, safe="")

    try:
        if verb in ("start", "complete", "stop") and len(argv) > 1:
            post(task_path(argv[1]), {"action": verb}, "PATCH")
            if verb == "complete":
                notify("Completed {}".format(argv[2] if len(argv) > 2 else "task"))
        elif verb == "tomorrow" and len(argv) > 1:
            due = (now() + timedelta(days=1)).strftime("%Y-%m-%d")
            post(task_path(argv[1]), {"due": due}, "PATCH")
        elif verb == "priority" and len(argv) > 2:
            post(task_path(argv[1]), {"priority": argv[2]}, "PATCH")
        elif verb == "add":
            title = osa(
                'text returned of (display dialog "Add a task" default answer "" '
                'with title "Argon" buttons {"Cancel", "Add"} default button "Add")'
            )
            if not title:
                return 0  # cancelled, which is not a failure
            post("/v1/tasks", {"title": title, "priority": "medium"})
        elif verb == "block" and len(argv) > 2:
            post("/v1/plan/" + urllib.parse.quote(argv[1], safe=""),
                 {"status": argv[2]}, "PATCH")
        elif verb == "unlock":
            minutes = int(argv[1]) if len(argv) > 1 else 120
            post("/v1/ios/override", {"minutes": minutes, "source": "desktop"})
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
             "running": True, "running_minutes": 12},
        ],
        "agenda": [{"id": "e1", "summary": "All Project Sync", "when": "in 12 min"}],
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

    # -- the Now panel -----------------------------------------------------
    # Whichever task the server says is running is the one that is running.
    # Deriving it here from a timestamp is what let the two readouts disagree.
    assert view["now"]["state"] == "running"
    assert view["now"]["title"] == "Running" and view["now"]["id"] == "c"
    assert view["now"]["elapsed"] == "12m" and view["now"]["over"] is False
    assert view["agenda"][0]["when"] == "in 12 min"

    # Nothing running: the picker offers work in the order it should be done,
    # and only ever real tasks — an empty list must not invent one.
    idle = build_view({"tasks": [
        {"id": "b", "title": "Low but due today", "priority": "low", "due": stamp(0)},
        {"id": "a", "title": "High", "priority": "high", "due": stamp(3)},
    ]})
    assert idle["now"]["state"] == "idle"
    assert [t["title"] for t in idle["now"]["picker"]] == ["High", "Low but due today"]
    assert build_view({"tasks": []})["now"]["state"] == "empty"
    assert build_view({"tasks": []})["now"]["picker"] == []

    # Over-estimate is only claimed when he set an estimate to exceed.
    over = build_view({"tasks": [{"id": "x", "title": "Long", "running": True,
                                  "running_minutes": 90, "time_estimate_min": 45}]})
    assert over["now"]["over"] is True
    plain = build_view({"tasks": [{"id": "x", "title": "Long", "running": True,
                                   "running_minutes": 900}]})
    assert plain["now"]["over"] is False

    # -- Argon's open questions --------------------------------------------
    # An answered item is history and must not sit there looking like a
    # question; one with no buttons is a statement, not a request.
    asked = build_view({
        "tasks": [],
        "inbox": [
            {"id": "k1", "text": "Have you started APUSH?",
             "actions": [{"label": "Starting now", "action": "start", "task_id": "t1"}]},
            {"id": "k2", "text": "Answered already", "answered": {"verb": "start"},
             "actions": [{"label": "Starting now", "action": "start", "task_id": "t2"}]},
            {"id": "k3", "text": "Your meeting starts in 15 minutes.", "actions": []},
        ],
    })["inbox"]
    assert [i["text"] for i in asked] == ["Have you started APUSH?"]
    assert asked[0]["actions"][0]["action"] == "start"

    # -- reaching the server -----------------------------------------------
    # Home is direct; school goes out through Cloudflare and back. Trying the
    # LAN first keeps it instant at his desk and still working when away.
    global _reachable
    _reachable = None
    tried = []

    def only(good):
        def call(base):
            tried.append(base)
            if base != good:
                raise OSError("no route to host")
            return {"base": base}
        return call

    lan, wan = "http://192.168.68.72:3995", "https://argon.agentneon.dev"
    assert reach([lan, wan], "t", only(lan))["base"] == lan
    assert tried == [lan]

    tried.clear()
    _reachable = None
    assert reach([lan, wan], "t", only(wan))["base"] == wan
    assert tried == [lan, wan]          # tries home, falls through

    tried.clear()
    assert reach([lan, wan], "t", only(wan))["base"] == wan
    assert tried == [wan]               # and stays pinned there

    try:
        reach([lan, wan], "t", only("nowhere"))
        raise AssertionError("both bases failed; that must propagate")
    except OSError:
        pass
    _reachable = None

    assert _is_lan(lan) and not _is_lan(wan)

    # Empty and broken payloads must both still render.
    render_swiftbar(build_view({"ios": {}, "tasks": []}))
    render_swiftbar(build_view({"error": "offline"}))
    render_swiftbar(idle)
    render_swiftbar(over)
    assert build_view({"tasks": []})["notice"]["text"] == "Clear runway"
    print("ok")


def dormant_view(reason):
    """What a paused readout says. No fetch, no view model, no network.

    Still says something rather than going blank: a widget that disappears
    reads as broken, and the one thing worth showing while asleep is why, and
    how to wake it.
    """
    return {"ok": True, "dormant": True, "reason": reason,
            "updated": now().strftime("%-I:%M:%S %p")}


def render_dormant(reason):
    out = [bar("", sfimage="moon.zzz", sfcolor=PALETTE["mutedInk"]), "---"]
    out.append(bar(reason, color=PALETTE["mutedInk"], size=12))
    out.append(bar("Resume", sfimage="play.fill", size=12,
                   **action_params("resume")))
    print("\n".join(out))


def main():
    if "--do" in sys.argv:
        sys.exit(do_action(sys.argv[sys.argv.index("--do") + 1:]))
    if "--selftest" in sys.argv:
        selftest()
        return

    # Checked before anything touches the network. A refresh while asleep costs
    # a process start and nothing else — no TLS handshake, no server load.
    active, reason = argon_activity.status()
    if not active:
        if "--json" in sys.argv:
            print(json.dumps(dormant_view(reason)))
        else:
            render_dormant(reason)
        return

    if "--json" in sys.argv:
        print(json.dumps(build_view(collect())))
    else:
        render_swiftbar(build_view(collect()))


if __name__ == "__main__":
    main()
