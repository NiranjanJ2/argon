"""Argon's HTTP surface — the WhatsApp bridge webhook and the iOS client API.

Flask runs in a daemon thread beside the agent's asyncio loop, so nothing here
may await.  Agent turns go through the sync bridge the gateway registers (see
``register_agent_handler``): an ``asyncio.run_coroutine_threadsafe`` wrapper.
"""

from __future__ import annotations

import asyncio
import functools
import hmac
import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from flask import Flask, jsonify, request
from loguru import logger
from werkzeug.exceptions import HTTPException

from argon.paths import argon_home, get_runtime_subdir

if TYPE_CHECKING:
    from argon.config import Config

# (message, session_key, timeout_s) -> reply. Blocks; raises TimeoutError on expiry.
AgentTurn = Callable[[str, str, float], str]
WhatsAppSink = Callable[[dict[str, Any]], None]

IOS_SESSION = "ios"
CHAT_TIMEOUT_S = 120.0
MAX_MESSAGE_CHARS = 8_000
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_LOOPBACK = {"127.0.0.1", "::1"}

#: How long a task list read may be reused before Google is asked again.
TASKS_TTL_S = 60.0
#: (fetched_at_monotonic, tasks) of the last successful read.
_tasks_cache: tuple[float, list[Any]] | None = None
_tasks_lock = threading.Lock()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1_000_000  # 1 MB ceiling on any request body


@dataclass
class _Runtime:
    """Wiring installed by ``start_api_server`` and by the channels."""

    config: Config | None = None
    agent: AgentTurn | None = None
    whatsapp: WhatsAppSink | None = None


_rt = _Runtime()


def register_agent_handler(handler: AgentTurn) -> None:
    """Register the bridge that runs one agent turn from inside a Flask thread."""
    _rt.agent = handler


def register_whatsapp_handler(handler: WhatsAppSink) -> None:
    """Register the sink for inbound WhatsApp payloads (name is a hard contract)."""
    _rt.whatsapp = handler


def _now() -> datetime:
    """Local wall clock — day buckets follow the configured timezone."""
    from argon import clock

    return clock.now()


def _body() -> dict[str, Any]:
    """Request body as a JSON object; ``{}`` for anything that is not one."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _text(body: dict[str, Any], *keys: str) -> str:
    """First non-empty string among ``keys``, trimmed and length-capped."""
    for key in keys:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:MAX_MESSAGE_CHARS]
    return ""


def require_token(view: Callable[..., Any]) -> Callable[..., Any]:
    """Gate a route behind ``Authorization: Bearer <token>``, compared constant-time."""
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        token = _rt.config.api.token if _rt.config else ""
        scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
        # An unset token authorises nothing; never echo either side of the compare.
        if not token or scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip().encode(), token.encode()
        ):
            logger.warning("Rejected {} {} from {}", request.method, request.path, request.remote_addr)
            return jsonify({"error": "unauthorized"}), 401
        return view(*args, **kwargs)
    return wrapped


def _run_turn(message: str, session_key: str) -> Any:
    """Run one agent turn, mapping bridge failures onto HTTP status codes."""
    if _rt.agent is None:
        return jsonify({"error": "agent not ready"}), 503
    try:
        reply = _rt.agent(message, session_key, CHAT_TIMEOUT_S)
    except TimeoutError:
        logger.warning("Agent turn timed out on session {}", session_key)
        return jsonify({"error": "timeout"}), 504
    except Exception:
        logger.exception("Agent turn failed on session {}", session_key)
        return jsonify({"error": "agent error"}), 502
    return jsonify({"reply": reply or "", "session": session_key})


def _screentime_file(day: str) -> Path:
    return get_runtime_subdir("screentime") / f"{day}.jsonl"


def read_screentime(day: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Return up to ``limit`` of a day's reports, oldest first. For future tools."""
    day = day or _now().strftime("%Y-%m-%d")
    if not _DATE_RE.match(day):
        raise ValueError("day must be YYYY-MM-DD")
    path = _screentime_file(day)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # Tolerate a torn final line rather than losing the day.
    return records[-limit:] if limit > 0 else []




def _task_dependencies() -> tuple[Any, Any, Any, Any]:
    """Build the same task-side dependencies used by the agent's tools."""
    from argon.google.tasks_store import GoogleTasksStore
    from argon.productivity.habits import HabitsTracker
    from argon.productivity.log import DailyLog
    from argon.productivity.state import DailyState

    ws = _rt.config.workspace_path if _rt.config else argon_home()
    return GoogleTasksStore(ws), DailyState(ws), DailyLog(ws), HabitsTracker(ws)


def _cached_tasks(store: Any, *, fresh: bool = False) -> tuple[list[Any], dict[str, Any]]:
    """The task list, reusing a recent read. Returns ``(tasks, meta)``.

    The desktop widgets poll every few seconds and Google Tasks is a
    rate-limited network round-trip, so the poll rate and the fetch rate are
    deliberately decoupled. Writes pass ``fresh=True`` — a dashboard that still
    showed the task you just completed would look broken.

    A failed refresh serves the last good list with ``error`` set rather than
    failing the request: a widget that blanks out is indistinguishable from one
    that is merely offline, and the difference is the whole point of the thing.
    """
    global _tasks_cache

    with _tasks_lock:
        cached = _tasks_cache
        if cached is not None and not fresh and (time.monotonic() - cached[0]) < TASKS_TTL_S:
            return cached[1], {"cached": True}
        try:
            tasks = store.get_all()
        except Exception as exc:  # noqa: BLE001 — a widget must still render
            if cached is None:
                raise
            logger.warning("Task refresh failed, serving the last good list: {}", exc)
            return cached[1], {"cached": True, "error": str(exc)}
        _tasks_cache = (time.monotonic(), tasks)
    return tasks, {"cached": False}


def _task_dashboard(store: Any, state: Any, *, fresh: bool = False) -> dict[str, Any]:
    """Shape the shared task store for the native iOS dashboard.

    ``state`` is read live even on a cache hit: it is local, and the work-session
    minute counter is one of the things the readouts are for.
    """
    tasks, meta = _cached_tasks(store, fresh=fresh)
    current = state.get()
    return {
        "tasks": tasks,
        **meta,
        "state": {
            "mode": current.get("mode", "idle"),
            "current_task": current.get("current_task"),
            "work_session_minutes": state.get_work_session_duration_minutes() or 0,
            "lock_in_minutes": state.get_lock_in_duration_minutes() or 0,
        },
    }


@app.get("/health")
def health() -> Any:
    """Liveness probe — deliberately unauthenticated."""
    return jsonify({"status": "ok", "service": "argon"})


@app.post("/whatsapp/incoming")
def whatsapp_incoming() -> Any:
    """Inbound hook for the whatsapp-web.js bridge: loopback only, no token."""
    if request.remote_addr not in _LOOPBACK:
        logger.warning("Dropped off-host WhatsApp webhook from {}", request.remote_addr)
        return jsonify({"error": "forbidden"}), 403
    payload = _body()
    if _rt.whatsapp is None:
        logger.warning("WhatsApp webhook fired before the channel registered — dropped.")
    elif payload:
        try:
            _rt.whatsapp(payload)
        except Exception:
            logger.exception("WhatsApp handler raised")
    return jsonify({"ok": True})


@app.post("/v1/chat")
@require_token
def chat() -> Any:
    """One synchronous agent turn on the dedicated iOS session."""
    message = _text(_body(), "message")
    if not message:
        return jsonify({"error": "message required"}), 400
    return _run_turn(message, IOS_SESSION)


@app.post("/v1/webhook/<name>")
@require_token
def webhook(name: str) -> Any:
    """Generic trigger (Shortcuts, Pushcut): a turn on session ``webhook:<name>``."""
    if not _NAME_RE.match(name):
        return jsonify({"error": "bad webhook name"}), 400
    message = _text(_body(), "message", "input")
    if not message:
        return jsonify({"error": "message required"}), 400
    return _run_turn(message, f"webhook:{name}")


@app.get("/v1/status")
@require_token
def status() -> Any:
    """Widget payload, assembled by the same tool the agent calls."""
    from argon.ios import mode as ios_mode
    from argon.productivity.state import DailyState
    from argon.tools.status import GetStatusTool

    ws = _rt.config.workspace_path if _rt.config else argon_home()
    data = json.loads(asyncio.run(GetStatusTool(DailyState(ws), ws).execute()))
    # The app decodes `ios.desired` and `ios.actual` into non-optional structs,
    # so both must always be complete objects — see argon/ios/mode.py.
    data["ios"] = ios_mode.snapshot()
    return jsonify(data)


@app.get("/v1/tasks")
@require_token
def tasks_get() -> Any:
    """Return the same Google Tasks-backed list and work state Argon uses.

    Served from a short cache; ``?fresh=1`` forces a Google read.
    """
    try:
        store, state, _, _ = _task_dependencies()
        return jsonify(_task_dashboard(store, state, fresh=request.args.get("fresh") == "1"))
    except Exception:
        logger.exception("Could not load the iOS task dashboard")
        return jsonify({"error": "task store unavailable", "tasks": []}), 503


@app.post("/v1/tasks")
@require_token
def tasks_add() -> Any:
    """Add a task through Argon's task tool so logging and metadata stay shared."""
    from argon.tools.tasks import AddTaskTool

    body = _body()
    title = _text(body, "title")
    if not title:
        return jsonify({"error": "title required"}), 400

    priority = body.get("priority", "medium")
    source = body.get("source", "manual")
    due = body.get("due")
    estimate = body.get("time_estimate_min")
    if priority not in {"high", "medium", "low"}:
        return jsonify({"error": "bad priority"}), 400
    if source not in {"manual", "classroom", "ucla", "club"}:
        return jsonify({"error": "bad source"}), 400
    if due is not None and (not isinstance(due, str) or not _DATE_RE.match(due)):
        return jsonify({"error": "due must be YYYY-MM-DD"}), 400
    if estimate is not None and (
        isinstance(estimate, bool) or not isinstance(estimate, int) or estimate <= 0
    ):
        return jsonify({"error": "time_estimate_min must be a positive integer"}), 400

    try:
        store, state, log, _ = _task_dependencies()
        kwargs = {
            "title": title,
            "priority": priority,
            "source": source,
            "due": due,
            "subject": _text(body, "subject") or None,
            "notes": _text(body, "notes") or None,
            "time_estimate_min": estimate,
        }
        asyncio.run(AddTaskTool(store, log).execute(**kwargs))
        return jsonify(_task_dashboard(store, state, fresh=True))
    except Exception:
        logger.exception("Could not add a task from the iOS dashboard")
        return jsonify({"error": "task store unavailable"}), 503


@app.patch("/v1/tasks/<task_id>")
@require_token
def tasks_update(task_id: str) -> Any:
    """Start, complete, reprioritize, or reschedule a shared Argon task."""
    from argon.tools.tasks import CompleteTaskTool, StartTaskTool, UpdateTaskTool

    body = _body()
    action = body.get("action")
    priority = body.get("priority")
    due = body.get("due")
    if action not in {None, "start", "complete"}:
        return jsonify({"error": "bad action"}), 400
    if priority is not None and priority not in {"high", "medium", "low"}:
        return jsonify({"error": "bad priority"}), 400
    if due is not None and (not isinstance(due, str) or not _DATE_RE.match(due)):
        return jsonify({"error": "due must be YYYY-MM-DD"}), 400
    if action is None and priority is None and due is None:
        return jsonify({"error": "no task mutation requested"}), 400

    try:
        store, state, log, habits = _task_dependencies()
        result = ""
        if action == "start":
            result = asyncio.run(StartTaskTool(store, state, log).execute(task_id=task_id))
        elif action == "complete":
            result = asyncio.run(
                CompleteTaskTool(store, state, log, habits).execute(task_id=task_id)
            )
        if result.startswith("No task matching") or result.startswith("No pending task matching"):
            return jsonify({"error": "task not found"}), 404

        if priority is not None or due is not None:
            result = asyncio.run(
                UpdateTaskTool(store).execute(task_id=task_id, priority=priority, due=due)
            )
            if result.startswith("No task matching"):
                return jsonify({"error": "task not found"}), 404
        return jsonify(_task_dashboard(store, state, fresh=True))
    except Exception:
        logger.exception("Could not update a task from the iOS dashboard")
        return jsonify({"error": "task store unavailable"}), 503


@app.get("/v1/ios/mode")
@require_token
def ios_mode_get() -> Any:
    """Desired focus state on its own, for a client that doesn't need the rest."""
    from argon.ios import mode as ios_mode

    return jsonify(ios_mode.get_mode())


@app.post("/v1/ios/state")
@require_token
def ios_state() -> Any:
    """Record what the phone actually applied. Also its liveness heartbeat."""
    from argon.ios import mode as ios_mode

    body = _body()
    if not body:
        return jsonify({"error": "json object required"}), 400
    return jsonify({"ok": True, **ios_mode.record_actual(body)})


@app.post("/v1/ios/override")
@require_token
def ios_override() -> Any:
    """Emergency release: drop any block and refuse to impose one for a while.

    Deliberately the simplest endpoint here — an escape hatch that needs a
    working agent, a working model or a working phone is not an escape hatch.
    """
    from argon.ios import mode as ios_mode

    body = _body()
    if body.get("clear") is True:
        ios_mode.clear_override()
        return jsonify({"ok": True, "active": False})

    minutes = body.get("minutes")
    if not isinstance(minutes, int) or minutes <= 0:
        minutes = _rt.config.ios.override_minutes if _rt.config else 120
    record = ios_mode.engage_override(minutes, source=str(body.get("source") or "api"))
    return jsonify({"ok": True, "active": True, **record})


@app.post("/v1/ios/register")
@require_token
def ios_register() -> Any:
    """Store the APNs device token."""
    from argon.ios import mode as ios_mode

    body = _body()
    if not str(body.get("device_token") or "").strip():
        return jsonify({"error": "device_token required"}), 400
    device = ios_mode.record_device(body)
    logger.info("iOS device registered ({} build)", device["environment"])
    return jsonify({"ok": True, "environment": device["environment"]})


@app.post("/v1/screentime")
@require_token
def screentime_report() -> Any:
    """Append one usage report. The schema stays loose until the app defines it."""
    payload = _body()
    if not payload:
        return jsonify({"error": "json object required"}), 400
    stamp = _now()
    day = stamp.strftime("%Y-%m-%d")
    record = {"received_at": stamp.isoformat(), "payload": payload}
    with _screentime_file(day).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.debug("Screen-time report stored for {}", day)
    return jsonify({"ok": True, "date": day})


@app.get("/v1/screentime")
@require_token
def screentime_history() -> Any:
    """Read a day's reports back — ``?date=YYYY-MM-DD``, defaulting to today."""
    day = request.args.get("date") or _now().strftime("%Y-%m-%d")
    if not _DATE_RE.match(day):
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    records = read_screentime(day)
    return jsonify({"date": day, "count": len(records), "records": records})




@app.errorhandler(Exception)
def _on_error(exc: Exception) -> Any:
    """Answer in JSON always, and never leak a traceback or a config value."""
    if isinstance(exc, HTTPException):
        return jsonify({"error": (exc.name or "error").lower()}), exc.code or 500
    logger.exception("Unhandled error on {} {}", request.method, request.path)
    return jsonify({"error": "internal error"}), 500


def start_api_server(config: Config) -> None:
    """Serve the API from a daemon thread. Returns immediately."""
    import logging

    if not config.api.enabled:
        logger.info("HTTP API disabled (api.enabled = false).")
        return

    _rt.config = config
    if not config.api.token:
        logger.warning("api.token is unset — every /v1 request will be rejected.")

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    threading.Thread(
        target=lambda: app.run(
            host=config.api.host, port=config.api.port, debug=False, use_reloader=False
        ),
        daemon=True,
        name="argon-api",
    ).start()
    logger.info("HTTP API on http://{}:{}", config.api.host, config.api.port)
