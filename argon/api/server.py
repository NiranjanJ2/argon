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


def _lockdown_file() -> Path:
    return get_runtime_subdir("daily") / "lockdown.json"


def _read_lockdown() -> dict[str, Any]:
    """Last state the phone reported, or a placeholder if it never has."""
    try:
        return json.loads(_lockdown_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "unknown", "since": None}


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
    from argon.productivity.state import DailyState
    from argon.tools.status import GetStatusTool

    from argon.ios import mode as ios_mode

    ws = _rt.config.workspace_path if _rt.config else argon_home()
    data = json.loads(asyncio.run(GetStatusTool(DailyState(ws), ws).execute()))
    data["lockdown"] = _read_lockdown()
    # The app decodes `ios.desired` and `ios.actual` into non-optional structs,
    # so both must always be complete objects — see argon/ios/mode.py.
    data["ios"] = ios_mode.snapshot()
    return jsonify(data)


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


@app.post("/v1/lockdown")
@require_token
def lockdown() -> Any:
    """Record the phone's lockdown state; with ``trigger`` also fire the Shortcut."""
    body = _body()
    state = body.get("state")
    if state not in ("lock", "unlock"):
        return jsonify({"error": "state must be 'lock' or 'unlock'"}), 400

    record = {"state": state, "since": _now().isoformat(), "source": "ios"}
    _lockdown_file().write_text(json.dumps(record, indent=2), encoding="utf-8")

    triggered = False
    if body.get("trigger") is True:
        cfg = _rt.config.lockdown if _rt.config else None
        if cfg is None or not cfg.configured:
            return jsonify({"error": "lockdown trigger not configured"}), 503
        from argon.tools.lockdown import send_trigger

        # send_trigger is the shared seam the model's tool goes through too, so
        # the phone and the agent can never disagree about how a lock is sent.
        result = send_trigger(cfg, "lockdown" if state == "lock" else "unlock")
        triggered = result.startswith("Trigger")
        if not triggered:
            logger.error("Lockdown trigger failed: {}", result)
            return jsonify({"error": "trigger failed"}), 502

    return jsonify({"ok": True, **record, "triggered": triggered})


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
