"""Argon web dashboard — Flask app on port 3995."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from flask import Flask, Response, jsonify, request

if TYPE_CHECKING:
    pass

app = Flask(__name__)
_workspace: Path | None = None
_whatsapp_handler: "Callable[[dict], None] | None" = None
_chat_handler: "Callable[[str], str] | None" = None
_pushcut_handler: "Callable[[str], str] | None" = None
_pushcut_token: str | None = None

# ── SSE push ──────────────────────────────────────────────────────────────────
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()


def push_update(event: str, data: dict | None = None) -> None:
    """Push a named SSE event to all connected dashboard clients."""
    msg = f"event: {event}\ndata: {json.dumps(data or {})}\n\n"
    with _sse_lock:
        dead = [q for q in _sse_clients if not _try_put(q, msg)]
        for q in dead:
            _sse_clients.remove(q)


def _try_put(q: queue.Queue, msg: str) -> bool:
    try:
        q.put_nowait(msg)
        return True
    except queue.Full:
        return False


def set_workspace(workspace: Path) -> None:
    global _workspace
    _workspace = workspace


def _get_workspace() -> Path:
    if _workspace is None:
        return Path.home() / ".nanobot" / "workspace"
    return _workspace


def register_whatsapp_handler(handler: "Callable[[dict], None]") -> None:
    global _whatsapp_handler
    _whatsapp_handler = handler


def register_chat_handler(handler: "Callable[[str], str]") -> None:
    global _chat_handler
    _chat_handler = handler


def register_pushcut_handler(handler: "Callable[[str], str]", token: str | None = None) -> None:
    global _pushcut_handler, _pushcut_token
    _pushcut_handler = handler
    _pushcut_token = token


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route("/api/period")
def api_period():
    try:
        from nanobot.schedule.manager import ScheduleManager
        mgr = ScheduleManager(_get_workspace())
        return jsonify(mgr.get_current_period())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/schedule")
def api_schedule():
    try:
        from nanobot.schedule.manager import ScheduleManager
        mgr = ScheduleManager(_get_workspace())
        return jsonify(mgr.get_full_schedule_today())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/todo")
def api_todo():
    try:
        from nanobot.google.tasks_store import GoogleTasksStore
        store = GoogleTasksStore(_get_workspace())
        return jsonify({"tasks": store.get_pending()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/todo/<task_id>/complete", methods=["POST"])
def api_complete_task(task_id: str):
    try:
        from nanobot.google.tasks_store import GoogleTasksStore
        store = GoogleTasksStore(_get_workspace())
        completed = store.complete_task(task_id)
        if completed:
            push_update("todo")
            return jsonify({"ok": True, "id": completed["id"]})
        return jsonify({"ok": False, "error": "Task not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/state")
def api_state():
    try:
        from nanobot.daily.state import DailyState
        state = DailyState(_get_workspace())
        data = state.get()
        work_min = state.get_work_session_duration_minutes()
        lock_min = state.get_lock_in_duration_minutes()
        return jsonify({**data, "work_session_duration_minutes": work_min, "lock_in_duration_minutes": lock_min})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/daily")
def api_daily():
    try:
        from nanobot.daily.log import DailyLog
        log = DailyLog(_get_workspace())
        return Response(log.read(), mimetype="text/plain")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        body = request.get_json(force=True, silent=True) or {}
        message = body.get("message", "").strip()
        if not message:
            return jsonify({"error": "No message provided"}), 400
        if _chat_handler is not None:
            reply = _chat_handler(message)
            return jsonify({"response": reply})
        return jsonify({"response": "chat not wired up yet — use discord or whatsapp"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/stream")
def api_stream():
    """SSE endpoint — dashboard subscribes here for live pushes."""
    def generate():
        q: queue.Queue = queue.Queue(maxsize=64)
        with _sse_lock:
            _sse_clients.append(q)
        try:
            yield "data: connected\n\n"
            while True:
                try:
                    yield q.get(timeout=25)
                except queue.Empty:
                    yield ": ping\n\n"  # keepalive so connection stays open
        finally:
            with _sse_lock:
                if q in _sse_clients:
                    _sse_clients.remove(q)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/push", methods=["POST"])
def api_push():
    """Let any process push an SSE event by POSTing {event, data}."""
    body = request.get_json(force=True, silent=True) or {}
    event = body.get("event", "reload")
    data = body.get("data")
    push_update(event, data)
    return jsonify({"ok": True, "clients": len(_sse_clients)})


@app.route("/whatsapp/incoming", methods=["POST"])
def whatsapp_incoming():
    payload = request.get_json(force=True, silent=True) or {}
    if _whatsapp_handler is not None:
        try:
            _whatsapp_handler(payload)
        except Exception:
            pass
    return jsonify({"ok": True})


@app.route("/webhook/pushcut", methods=["POST"])
def pushcut_incoming():
    """Webhook endpoint for Pushcut iOS app triggers."""
    # Optional token auth — check query param or Authorization header
    if _pushcut_token:
        provided = request.args.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if provided != _pushcut_token:
            return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(force=True, silent=True) or {}
    # Pushcut sends {"input": "..."} or we accept {"message": "..."}
    # Also support a plain ?msg= query param for simple shortcut triggers
    message = (
        body.get("input")
        or body.get("message")
        or request.args.get("msg", "")
    ).strip()

    if not message:
        return jsonify({"error": "No message provided"}), 400

    if _pushcut_handler is not None:
        try:
            reply = _pushcut_handler(message)
            return jsonify({"ok": True, "response": reply})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Argon</title>
<style>
:root {
  --bg: #080c18;
  --navy: #0c1424;
  --card: rgba(12,20,40,0.9);
  --card-hi: rgba(16,28,58,0.95);
  --sep: rgba(100,140,255,0.1);
  --accent: #4a8eff;
  --glow: rgba(74,142,255,0.18);
  --glow-soft: rgba(74,142,255,0.09);
  --green: #34c759;
  --yellow: #ffd60a;
  --red: #ff453a;
  --purple: #bf5af2;
  --t1: rgba(230,238,255,0.92);
  --t2: rgba(180,200,240,0.50);
  --t3: rgba(140,165,220,0.28);
  --bar: 50px;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  background: var(--bg);
  color: var(--t1);
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  overflow: hidden;
}

body::before {
  content: '';
  position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background:
    radial-gradient(ellipse 80% 50% at 50% -10%, rgba(40,80,200,0.12) 0%, transparent 70%),
    radial-gradient(ellipse 60% 40% at 20% 80%, rgba(20,50,160,0.07) 0%, transparent 60%);
}

/* ── Top bar ─────────────────────────────────────────────────── */
.bar {
  position: fixed; top: 0; left: 0; right: 0; height: var(--bar);
  display: flex; align-items: center; padding: 0 24px; gap: 16px;
  background: rgba(7,10,22,0.9);
  backdrop-filter: blur(24px) saturate(160%);
  -webkit-backdrop-filter: blur(24px) saturate(160%);
  border-bottom: 1px solid var(--sep);
  z-index: 100;
}

.bar-logo {
  font-size: 16px; font-weight: 700;
  letter-spacing: -0.5px; color: var(--t1);
  flex-shrink: 0;
}

.bar-period {
  flex: 1; display: flex; justify-content: center;
}

.period-inner {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; color: var(--t2);
}

.period-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 8px rgba(10,132,255,0.6);
  animation: glow 2.5s ease-in-out infinite;
  flex-shrink: 0;
}

.period-dot.off { background: var(--t3); box-shadow: none; animation: none; }

@keyframes glow {
  0%,100% { opacity:1; } 50% { opacity:0.4; }
}

.period-name { font-weight: 600; color: var(--t1); }

.period-bar-wrap {
  width: 48px; height: 2px;
  background: rgba(255,255,255,0.08); border-radius: 1px;
}

.period-bar-fill {
  height: 100%; background: var(--accent); border-radius: 1px;
  transition: width 30s linear;
}

.bar-right {
  display: flex; align-items: center; gap: 12px; flex-shrink: 0;
}

.mode-tag {
  font-size: 11px; font-weight: 700; letter-spacing: 0.4px;
  padding: 4px 11px; border-radius: 20px;
}
.m-idle    { background:rgba(255,255,255,0.07); color:var(--t2); }
.m-working { background:rgba(48,209,88,0.15);   color:var(--green); }
.m-napping { background:rgba(255,214,10,0.13);  color:var(--yellow); }
.m-lock_in { background:rgba(191,90,242,0.15);  color:var(--purple); }
.m-done    { background:rgba(10,132,255,0.13);  color:var(--accent); }

.bar-clock {
  font-size: 13px; font-weight: 500;
  color: var(--t2); font-variant-numeric: tabular-nums;
}

/* ── Layout ──────────────────────────────────────────────────── */
.layout {
  position: fixed; top: var(--bar); bottom: 0; left: 0; right: 0;
  display: grid;
  grid-template-columns: 310px 1fr 360px;
}

.col {
  overflow-y: auto; overflow-x: hidden;
  scrollbar-width: none; padding: 36px 26px;
}
.col::-webkit-scrollbar { display: none; }

.col-left  { border-right: 1px solid var(--sep); }
.col-right { border-left: 1px solid var(--sep); padding: 0; }

/* ── Section label ───────────────────────────────────────────── */
.label {
  font-size: 13px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1px;
  color: var(--t3); margin-bottom: 24px;
}

/* ── Col backgrounds ─────────────────────────────────────────── */
.col-left, .col-center { background: transparent; }
.col-right { background: rgba(8,12,24,0.6); }

/* ── Schedule ────────────────────────────────────────────────── */
.sched-list { display: flex; flex-direction: column; }

.sched-row {
  display: flex; align-items: center; gap: 14px;
  padding: 18px 14px;
  border-radius: 12px;
  border-bottom: 1px solid var(--sep);
  position: relative;
}
.sched-row:last-child { border-bottom: none; }
.sched-row.past { opacity: 0.25; }
.sched-row.now  { opacity: 1; }

.sched-row.now {
  background: rgba(74,142,255,0.08);
  border-bottom-color: transparent;
  box-shadow:
    0 0 0 1px rgba(74,142,255,0.2),
    0 0 24px rgba(74,142,255,0.12),
    0 0 60px rgba(74,142,255,0.06);
}

.sched-pip {
  width: 5px; height: 5px; border-radius: 50%;
  background: var(--sep); flex-shrink: 0;
}
.sched-row.now .sched-pip {
  background: var(--accent);
  box-shadow: 0 0 8px rgba(74,142,255,0.9);
  width: 6px; height: 6px;
}

.sched-name {
  flex: 1; font-size: 20px; font-weight: 500; color: rgba(140,165,220,0.38);
}
.sched-row.now .sched-name { color: #ffffff; font-weight: 600; }

.sched-time { font-size: 15px; color: rgba(120,145,210,0.28); font-variant-numeric: tabular-nums; }
.sched-row.now .sched-time { color: rgba(180,210,255,0.65); }

.sched-row:not(.past):not(.now):hover {
  background: rgba(74,142,255,0.05);
  box-shadow: 0 0 0 1px rgba(74,142,255,0.15), 0 0 16px rgba(74,142,255,0.08);
}

/* ── Divider ─────────────────────────────────────────────────── */
.divider { height: 1px; background: var(--sep); margin: 40px 0; }

/* ── Focus timer ─────────────────────────────────────────────── */
.timer-block {
  display: flex; flex-direction: column; align-items: center;
  gap: 8px;
}

.timer-ring { position: relative; width: 180px; height: 180px; }

.ring-svg { transform: rotate(-90deg); width: 180px; height: 180px; }
.ring-track { fill:none; stroke:rgba(255,255,255,0.06); stroke-width:3; }
.ring-fill  {
  fill:none; stroke:var(--accent); stroke-width:3;
  stroke-linecap:round;
  stroke-dasharray:376.99;
  stroke-dashoffset:0;
  transition: stroke-dashoffset 1s linear, stroke 0.4s, filter 0.4s;
}
.ring-fill.brk { stroke:var(--green); }
.ring-fill.running {
  filter: drop-shadow(0 0 6px rgba(74,142,255,0.7));
}
.ring-fill.brk.running {
  filter: drop-shadow(0 0 6px rgba(52,199,89,0.7));
}

.ring-inside {
  position:absolute; inset:0;
  display:flex; flex-direction:column;
  align-items:center; justify-content:center; gap:2px;
}

.timer-num {
  font-size: 52px; font-weight: 300;
  letter-spacing: -3px; font-variant-numeric: tabular-nums;
  color: var(--t1);
}

.timer-sublabel {
  font-size: 12px; text-transform: uppercase;
  letter-spacing: 1.5px; color: var(--t3);
}

.timer-btns {
  display: flex; gap: 10px; margin-top: 8px;
}

.tbtn {
  height: 40px; padding: 0 20px;
  border-radius: 11px; border: 1px solid rgba(74,142,255,0.15);
  background: rgba(12,20,44,0.8); color: var(--t1);
  font-size: 15px; font-weight: 600; cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s; font-family: inherit;
}
.tbtn:hover { background: rgba(74,142,255,0.12); box-shadow: 0 0 12px rgba(74,142,255,0.1); }
.tbtn.go {
  background: var(--accent); border-color: transparent;
  box-shadow: 0 0 14px rgba(74,142,255,0.35);
}
.tbtn.go:hover { background: #5a9eff; box-shadow: 0 0 20px rgba(74,142,255,0.5); }

.tsel {
  height: 40px; padding: 0 12px;
  border-radius: 11px; border: 1px solid var(--sep);
  background: var(--card); color: var(--t2);
  font-size: 14px; cursor: pointer;
  -webkit-appearance: none; font-family: inherit;
}

/* ── Status hero ──────────────────────────────────────────────── */
.hero {
  background: var(--card);
  border-radius: 16px;
  padding: 30px 28px;
  margin-bottom: 32px;
  border: 1px solid rgba(74,142,255,0.08);
  position: relative;
  transition: box-shadow 0.4s ease, border-color 0.4s ease;
  display: flex; align-items: center; gap: 28px;
}

.hero-timer {
  display: none; flex-direction: row; align-items: center;
  flex-shrink: 0; padding-right: 28px;
  border-right: 1px solid rgba(74,142,255,0.18);
  gap: 16px;
}
.hero-timer.show { display: flex; }

.hero-timer-time {
  display: flex; flex-direction: column; align-items: flex-start;
}

.hero-timer-num {
  font-size: 76px; font-weight: 200;
  letter-spacing: -5px; font-variant-numeric: tabular-nums;
  color: #fff; line-height: 1;
}

.hero-timer-sub {
  font-size: 12px; text-transform: uppercase;
  letter-spacing: 1.2px; color: var(--accent);
  margin-top: 6px;
}

.hero-timer-btns {
  display: flex; flex-direction: column; gap: 8px;
}

.htbtn {
  width: 34px; height: 34px; border-radius: 10px;
  border: 1px solid rgba(74,142,255,0.2);
  background: rgba(12,20,44,0.6);
  color: var(--t2); cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background 0.15s, color 0.15s, box-shadow 0.15s;
  flex-shrink: 0;
}
.htbtn:hover { background: rgba(74,142,255,0.15); color: var(--t1); box-shadow: 0 0 10px rgba(74,142,255,0.15); }

.hero-content { flex: 1; min-width: 0; }

.hero.active {
  border-color: rgba(74,142,255,0.22);
  box-shadow:
    0 0 0 1px rgba(74,142,255,0.12),
    0 0 30px rgba(74,142,255,0.14),
    0 0 80px rgba(74,142,255,0.07);
}

.hero.active::before {
  content: '';
  position: absolute; inset: -2px; z-index: -1;
  border-radius: 18px;
  background: radial-gradient(ellipse 90% 70% at 50% 40%, rgba(74,142,255,0.1) 0%, transparent 70%);
  pointer-events: none;
}

.hero-mode {
  font-size: 14px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1.2px;
  margin-bottom: 12px;
}
.hm-idle    { color:var(--t3); }
.hm-working { color:var(--green); }
.hm-napping { color:var(--yellow); }
.hm-lock_in { color:var(--purple); }
.hm-done    { color:var(--accent); }

.hero-task {
  font-size: 36px; font-weight: 600;
  line-height: 1.2; color: var(--t1);
  margin-bottom: 10px;
  letter-spacing: -1px;
}

.hero-meta {
  font-size: 18px; color: var(--t2);
}

/* ── Tasks ───────────────────────────────────────────────────── */
.task-list { display: flex; flex-direction: column; }

.task {
  display: flex; align-items: flex-start; gap: 16px;
  padding: 22px 10px;
  border-bottom: 1px solid var(--sep);
  cursor: pointer;
  border-radius: 10px;
  margin: 0 -10px;
  transition: background 0.15s, box-shadow 0.15s;
  position: relative;
}
.task:last-child { border-bottom: none; }
.task.done-t { opacity: 0.3; }

.task:hover {
  background: rgba(74,142,255,0.05);
  box-shadow: 0 0 0 1px rgba(74,142,255,0.2), 0 0 22px rgba(74,142,255,0.1);
  z-index: 1;
}

.task-circle {
  width: 26px; height: 26px; border-radius: 50%;
  border: 1.5px solid rgba(255,255,255,0.18);
  flex-shrink: 0; margin-top: 2px;
  display: flex; align-items: center; justify-content: center;
  transition: border-color 0.15s, background 0.15s;
}
.task:hover .task-circle { border-color: var(--accent); }
.task.done-t .task-circle { background: var(--accent); border-color: var(--accent); }

.chk { display:none; }
.task.done-t .chk { display:block; }

.task-body { flex:1; min-width:0; }

.task-name {
  font-size: 22px; font-weight: 500;
  color: var(--t1); line-height: 1.3;
}

.task-sub {
  font-size: 14px; color: var(--t2);
  margin-top: 7px; display:flex; gap:8px; flex-wrap:wrap; align-items:center;
}

.dot-sep { color:var(--t3); }

.tag {
  font-size: 12px; font-weight: 600;
  padding: 3px 9px; border-radius: 6px;
}
.tag-h   { background:rgba(74,142,255,0.22); color:rgba(200,225,255,0.95); }
.tag-m   { background:rgba(74,142,255,0.12); color:rgba(170,205,255,0.75); }
.tag-l   { background:rgba(74,142,255,0.06); color:rgba(140,180,255,0.52); }
.tag-cls { background:rgba(90,155,255,0.14); color:rgba(180,215,255,0.82); }
.tag-ucla{ background:rgba(110,165,255,0.11); color:rgba(165,205,255,0.70); }

.done-label {
  font-size: 12px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.8px;
  color: var(--t3); padding: 20px 0 10px;
}

/* ── Chat ────────────────────────────────────────────────────── */
.chat-wrap {
  height: 100%; display: flex; flex-direction: column;
}

.chat-msgs {
  flex: 1; overflow-y: auto; padding: 20px 20px 10px;
  display: flex; flex-direction: column; gap: 10px;
  scrollbar-width: thin; scrollbar-color: var(--sep) transparent;
}
.chat-msgs::-webkit-scrollbar { width: 3px; }
.chat-msgs::-webkit-scrollbar-thumb { background: var(--sep); border-radius: 2px; }

.bub {
  max-width: 86%; padding: 13px 16px;
  border-radius: 18px; font-size: 17px; line-height: 1.5;
  word-break: break-word;
}
.bub.u {
  align-self: flex-end;
  background: var(--accent); color: #fff;
  border-bottom-right-radius: 5px;
}
.bub.a {
  align-self: flex-start;
  background: rgba(255,255,255,0.07);
  border-bottom-left-radius: 5px;
}
.bt { font-size: 12px; opacity:0.4; margin-top:5px; }
.bub.u .bt { text-align:right; }

.typing {
  align-self:flex-start; display:none;
  padding: 12px 16px;
  background: rgba(255,255,255,0.07);
  border-radius: 18px; border-bottom-left-radius: 5px;
  gap:4px; align-items:center;
}
.typing.on { display:flex; }

.td {
  width:6px; height:6px; border-radius:50%;
  background:var(--t2);
  animation: tdot 1.3s ease infinite;
}
.td:nth-child(2) { animation-delay:.18s; }
.td:nth-child(3) { animation-delay:.36s; }

@keyframes tdot {
  0%,60%,100% { transform:translateY(0); opacity:.35; }
  30% { transform:translateY(-5px); opacity:1; }
}

.chat-foot {
  padding: 12px 16px;
  border-top: 1px solid var(--sep);
  display: flex; gap: 10px; align-items: flex-end;
  background: rgba(10,10,14,0.7);
  flex-shrink: 0;
}

.chat-ta {
  flex:1; background: rgba(12,20,44,0.7);
  border: 1px solid rgba(74,142,255,0.12);
  border-radius: 14px; color: var(--t1);
  padding: 12px 16px; font-size: 16px;
  font-family: inherit; resize:none; outline:none;
  min-height:46px; max-height:120px; line-height:1.42;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.chat-ta:focus {
  border-color: rgba(74,142,255,0.4);
  box-shadow: 0 0 0 3px rgba(74,142,255,0.08);
}
.chat-ta::placeholder { color:var(--t3); }

.send {
  width:44px; height:44px; border-radius:13px;
  border:none; background:var(--accent); color:#fff;
  display:flex; align-items:center; justify-content:center;
  cursor:pointer; flex-shrink:0;
  transition: opacity 0.15s, transform 0.1s;
}
.send:hover { opacity:0.85; }
.send:active { transform:scale(0.93); }


/* ── Mobile ──────────────────────────────────────────────────── */
@media (max-width:800px) {
  body { overflow:auto; }
  .layout { position:static; display:block; padding-top:var(--bar); }
  .col { border:none; padding:20px 16px; }
  .col-right { padding:0; }
  .chat-wrap { height:480px; }
  .bar-clock { display:none; }
}
</style>
</head>
<body>

<!-- top bar -->
<header class="bar">
  <span class="bar-logo">Argon</span>

  <div class="bar-period">
    <div class="period-inner">
      <span class="period-dot" id="pDot"></span>
      <span class="period-name" id="pName">—</span>
      <span id="pSub" style="color:var(--t3)"></span>
      <div class="period-bar-wrap" id="pBarWrap" style="display:none">
        <div class="period-bar-fill" id="pBar" style="width:0%"></div>
      </div>
    </div>
  </div>

  <div class="bar-right">
    <span class="mode-tag m-idle" id="modeTag">idle</span>
    <span class="bar-clock" id="clock"></span>
  </div>
</header>

<!-- main layout -->
<div class="layout">

  <!-- left: schedule + timer -->
  <div class="col col-left" id="colLeft">
    <div class="label">Schedule</div>
    <div class="sched-list" id="schedList">
      <div style="color:var(--t3);font-size:13px;padding:12px 0">Loading…</div>
    </div>

    <div class="divider" id="timerDivider"></div>

    <div id="timerWrap">
      <div class="label">Focus Timer</div>
      <div class="timer-block">
        <div class="timer-ring">
          <svg class="ring-svg" width="130" height="130" viewBox="0 0 130 130">
            <circle class="ring-track" cx="65" cy="65" r="60"/>
            <circle class="ring-fill" id="ringFg" cx="65" cy="65" r="60"/>
          </svg>
          <div class="ring-inside">
            <div class="timer-num" id="tNum">25:00</div>
            <div class="timer-sublabel" id="tLbl">Work</div>
          </div>
        </div>
        <div class="timer-btns">
          <button class="tbtn go" id="tBtn" onclick="pomoToggle()">Start</button>
          <button class="tbtn" onclick="pomoReset()">Reset</button>
          <select class="tsel" id="tDur" onchange="pomoReset()">
            <option value="25">25 min</option>
            <option value="45">45 min</option>
            <option value="50">50 min</option>
            <option value="90">90 min</option>
          </select>
        </div>
      </div>
    </div>
  </div>

  <!-- center: status + tasks -->
  <div class="col col-center" id="colCenter">

    <!-- status hero -->
    <div class="hero" id="hero">
      <div class="hero-timer" id="heroTimer">
        <div class="hero-timer-time">
          <div class="hero-timer-num" id="heroTimerNum">25:00</div>
          <div class="hero-timer-sub" id="heroTimerSub">Focus</div>
        </div>
        <div class="hero-timer-btns">
          <button class="htbtn" id="htPause" onclick="pomoToggle()" title="Pause">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="3" width="4" height="18" rx="1.5"/><rect x="15" y="3" width="4" height="18" rx="1.5"/></svg>
          </button>
          <button class="htbtn" onclick="pomoReset()" title="Reset">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-4.65"/></svg>
          </button>
        </div>
      </div>
      <div class="hero-content">
        <div class="hero-mode hm-idle" id="heroMode">Idle</div>
        <div class="hero-task" id="heroTask">Nothing active</div>
        <div class="hero-meta" id="heroMeta"></div>
      </div>
    </div>

    <div class="label">Today</div>
    <div class="task-list" id="taskList">
      <div style="color:var(--t3);font-size:13px;padding:14px 0">Loading…</div>
    </div>
  </div>

  <!-- right: chat -->
  <div class="col col-right">
    <div class="chat-wrap">
      <div class="chat-msgs" id="msgs">
        <div class="bub a">
          <div>hey. what's up?</div>
          <div class="bt">now</div>
        </div>
      </div>

      <div class="typing" id="typing">
        <span class="td"></span><span class="td"></span><span class="td"></span>
      </div>

      <div class="chat-foot">
        <textarea class="chat-ta" id="chatIn" placeholder="Message Argon…" rows="1"
          oninput="grow(this)" onkeydown="chatKey(event)"></textarea>
        <button class="send" onclick="sendMsg()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>
        </button>
      </div>
    </div>
  </div>
</div>

<script>
// ── Clock ──────────────────────────────────────────────────────
(function tick(){
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  setTimeout(tick,1000);
})();

// ── Utils ──────────────────────────────────────────────────────
const esc = s => (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const toMin = t => { const [h,m]=t.split(':').map(Number); return h*60+m; };
const fmt12 = t => { const [h,m]=t.split(':').map(Number); return (h%12||12)+':'+(m<10?'0':'')+m+' '+(h<12?'AM':'PM'); };
const now12 = () => new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit'});

function fmtDue(iso){
  try {
    const d=new Date(iso), t=new Date(); t.setHours(0,0,0,0);
    const diff=Math.floor((d-t)/86400000);
    if(diff<0) return 'overdue';
    if(diff===0) return 'due today';
    if(diff===1) return 'due tmrw';
    return 'due '+d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
  } catch { return ''; }
}

// ── Schedule ───────────────────────────────────────────────────
let _sched=[];

async function loadSched(){
  try {
    const d=await fetch('/api/schedule').then(r=>r.json());
    _sched=d.periods||[];
    renderSched();
  } catch { document.getElementById('schedList').innerHTML='<div style="color:var(--t3);font-size:13px">No schedule</div>'; }
}

function renderSched(){
  const nm=new Date().getHours()*60+new Date().getMinutes();
  document.getElementById('schedList').innerHTML=_sched.map(p=>{
    const s=toMin(p.start), e=toMin(p.end);
    const isNow=nm>=s&&nm<e, isPast=nm>=e;
    return '<div class="sched-row'+(isNow?' now':isPast?' past':'')+'">'+
      '<span class="sched-pip"></span>'+
      '<span class="sched-name">'+esc(p.label)+'</span>'+
      '<span class="sched-time">'+fmt12(p.start)+'</span>'+
    '</div>';
  }).join('');
}

function updatePeriod(){
  fetch('/api/period').then(r=>r.json()).then(d=>{
    renderSched();
    const dot=document.getElementById('pDot');
    const nm=document.getElementById('pName');
    const sb=document.getElementById('pSub');
    const bw=document.getElementById('pBarWrap');
    const bf=document.getElementById('pBar');
    if(d.status==='in_period'){
      dot.classList.remove('off');
      nm.textContent=d.period;
      sb.textContent=d.minutes_remaining+'m left';
      bw.style.display='block';
      const nowMin=new Date().getHours()*60+new Date().getMinutes();
      const row=_sched.find(p=>toMin(p.start)<=nowMin&&nowMin<toMin(p.end));
      if(row){ const tot=toMin(row.end)-toMin(row.start), el=nowMin-toMin(row.start);
        bf.style.width=Math.min(100,el/tot*100)+'%'; }
    } else if(d.status==='between_periods'){
      dot.classList.remove('off');
      nm.textContent='→ '+d.next_period;
      sb.textContent='in '+d.minutes_until+'m';
      bw.style.display='none';
    } else {
      dot.classList.add('off');
      nm.textContent='After school';
      sb.textContent=''; bw.style.display='none';
    }
  }).catch(()=>{});
}

loadSched().then(updatePeriod);
setInterval(updatePeriod,30000);

// ── State ──────────────────────────────────────────────────────
function updateState(){
  fetch('/api/state').then(r=>r.json()).then(d=>{
    const mode=d.mode||'idle';
    const mt=document.getElementById('modeTag');
    mt.textContent=mode; mt.className='mode-tag m-'+mode;

    const hm=document.getElementById('heroMode');
    hm.textContent=mode.replace('_',' ');
    hm.className='hero-mode hm-'+mode;

    document.getElementById('heroTask').textContent=
      d.current_task || (mode==='idle'?'Nothing active':mode==='done'?'Session complete':'—');

    const wm=d.work_session_duration_minutes, lm=d.lock_in_duration_minutes;
    const mins=mode==='lock_in'?lm:wm;
    const showMins=mins!=null&&mins>0&&(mode==='working'||mode==='lock_in');
    document.getElementById('heroMeta').textContent=showMins?mins+' minutes in':'';

    const hero=document.getElementById('hero');
    if(mode==='working'||mode==='lock_in') hero.classList.add('active');
    else hero.classList.remove('active');

  }).catch(()=>{});
}

updateState();
setInterval(updateState,15000);

// ── Tasks ──────────────────────────────────────────────────────
async function loadTasks(){
  try {
    const d=await fetch('/api/todo').then(r=>r.json());
    const pend=d.pending||[], done=d.done||[];
    let h='';
    if(!pend.length&&!done.length){ h='<div style="color:var(--t3);font-size:13px;padding:14px 0">No tasks yet.</div>'; }
    else {
      pend.forEach(t=>h+=taskRow(t,false));
      if(done.length){
        h+='<div class="done-label">Completed ('+done.length+')</div>';
        done.forEach(t=>h+=taskRow(t,true));
      }
    }
    document.getElementById('taskList').innerHTML=h;
  } catch { document.getElementById('taskList').innerHTML='<div style="color:var(--t3);font-size:13px;padding:14px 0">Failed to load.</div>'; }
}

function taskRow(t, isDone){
  const sub=[];
  if(t.priority){
    const cls={high:'tag-h',medium:'tag-m',low:'tag-l'}[t.priority]||'';
    sub.push('<span class="tag '+cls+'">'+t.priority+'</span>');
  }
  if(t.source&&t.source!=='manual'){
    const cls={classroom:'tag-cls',ucla:'tag-ucla'}[t.source]||'';
    sub.push('<span class="tag '+cls+'">'+t.source+'</span>');
  }
  if(t.due){ const d=fmtDue(t.due); if(d) sub.push('<span>'+d+'</span>'); }
  if(t.time_estimate_min) sub.push('<span>~'+t.time_estimate_min+'m</span>');
  const onclick=isDone?'':' onclick="doneTask(\''+t.id+'\')"';
  return '<div class="task'+(isDone?' done-t':'')+'"'+onclick+'>'+
    '<div class="task-circle">'+
      '<svg class="chk" width="10" height="8" viewBox="0 0 10 8" fill="none">'+
        '<path d="M1 4l2.5 2.5 6-5.5" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'+
      '</svg>'+
    '</div>'+
    '<div class="task-body">'+
      '<div class="task-name">'+esc(t.title)+'</div>'+
      (sub.length?'<div class="task-sub">'+sub.join('<span class="dot-sep">·</span>')+'</div>':'')+
    '</div>'+
  '</div>';
}

async function doneTask(id){
  await fetch('/api/todo/'+id+'/complete',{method:'POST'});
  loadTasks();
}

loadTasks();
setInterval(loadTasks,30000);

// ── Pomodoro ───────────────────────────────────────────────────
let _pi=null, _ps=25*60, _pr=false, _pm='work', _ta=false;
const RC=376.99;

function setRingGlow(on){
  const r=document.getElementById('ringFg');
  if(on) r.classList.add('running'); else r.classList.remove('running');
}

function syncTimerTop(){
  const ht=document.getElementById('heroTimer');
  const hn=document.getElementById('heroTimerNum');
  const hs=document.getElementById('heroTimerSub');
  const wrap=document.getElementById('timerWrap');
  const div=document.getElementById('timerDivider');
  if(_ta){
    ht.classList.add('show');
    wrap.style.display='none';
    div.style.display='none';
    const dur=document.getElementById('tDur').value;
    hs.textContent=(_pm==='work'?'Focus':'Break')+' · '+dur+'m';
    const m=Math.floor(_ps/60), s=_ps%60;
    hn.textContent=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
    const pb=document.getElementById('htPause');
    if(pb) pb.innerHTML=_pr
      ? '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="3" width="4" height="18" rx="1.5"/><rect x="15" y="3" width="4" height="18" rx="1.5"/></svg>'
      : '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
  } else {
    ht.classList.remove('show');
    wrap.style.display='';
    div.style.display='';
  }
}

function pomoToggle(){
  if(_pr){
    // pause
    clearInterval(_pi); _pr=false;
    document.getElementById('tBtn').textContent='Resume';
    setRingGlow(false);
    syncTimerTop();
  } else {
    // start / resume
    if(!_ta){ _ta=true; }
    _pr=true;
    document.getElementById('tBtn').textContent='Pause';
    setRingGlow(true);
    syncTimerTop();
    _pi=setInterval(()=>{
      _ps--; renderTimer();
      if(_ps<=0){
        clearInterval(_pi); _pr=false;
        document.getElementById('tBtn').textContent='Start';
        if(_pm==='work'){
          _pm='break'; _ps=5*60;
          document.getElementById('tLbl').textContent='Break';
          document.getElementById('ringFg').classList.add('brk');
          if(Notification.permission==='granted') new Notification('Break — 5 min');
        } else {
          _pm='work'; _ps=parseInt(document.getElementById('tDur').value)*60;
          document.getElementById('tLbl').textContent='Work';
          document.getElementById('ringFg').classList.remove('brk');
          if(Notification.permission==='granted') new Notification('Back to work.');
        }
        syncTimerTop();
        renderTimer();
      }
    },1000);
  }
}

function pomoReset(){
  clearInterval(_pi); _pr=false; _pm='work'; _ta=false;
  _ps=parseInt(document.getElementById('tDur').value)*60;
  document.getElementById('tBtn').textContent='Start';
  document.getElementById('tLbl').textContent='Work';
  document.getElementById('ringFg').classList.remove('brk');
  setRingGlow(false);
  syncTimerTop();
  renderTimer();
}

function renderTimer(){
  const m=Math.floor(_ps/60), s=_ps%60;
  const str=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
  document.getElementById('tNum').textContent=str;
  if(_ta) document.getElementById('heroTimerNum').textContent=str;
  const total=_pm==='work'?parseInt(document.getElementById('tDur').value)*60:5*60;
  document.getElementById('ringFg').style.strokeDashoffset=(RC*(_ps/total)).toString();
}

renderTimer();
if('Notification' in window && Notification.permission==='default') Notification.requestPermission();

// ── Chat ───────────────────────────────────────────────────────
function grow(el){ el.style.height='auto'; el.style.height=el.scrollHeight+'px'; }
function chatKey(e){ if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg();} }

async function sendMsg(){
  const inp=document.getElementById('chatIn');
  const txt=inp.value.trim(); if(!txt) return;
  inp.value=''; inp.style.height='auto';
  addBub(txt,'u');
  document.getElementById('typing').classList.add('on');
  scrollMsgs();
  try {
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:txt})});
    const d=await r.json();
    document.getElementById('typing').classList.remove('on');
    addBub(d.response||d.error||'…','a');
  } catch {
    document.getElementById('typing').classList.remove('on');
    addBub('connection lost — is the server running?','a');
  }
  scrollMsgs();
}

function addBub(txt,role){
  const div=document.createElement('div');
  div.className='bub '+role;
  div.innerHTML='<div>'+esc(txt)+'</div><div class="bt">'+now12()+'</div>';
  document.getElementById('msgs').appendChild(div);
}

function scrollMsgs(){ const el=document.getElementById('msgs'); el.scrollTop=el.scrollHeight; }

// ── Live updates via SSE ───────────────────────────────────────
(function(){
  function connect(){
    const es=new EventSource('/api/stream');
    es.addEventListener('state',    ()=>updateState());
    es.addEventListener('todo',     ()=>loadTasks());
    es.addEventListener('schedule', ()=>loadSched().then(updatePeriod));
    es.addEventListener('period',   ()=>updatePeriod());
    es.addEventListener('reload',   ()=>location.reload());
    es.onerror=()=>{ es.close(); setTimeout(connect, 3000); };
  }
  if(typeof EventSource!=='undefined') connect();
})();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(_DASHBOARD_HTML, mimetype="text/html")


# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------


def start_dashboard(host: str = "0.0.0.0", port: int = 3995, workspace: Path | None = None) -> None:
    """Start the Flask dashboard in a background daemon thread."""
    import logging

    if workspace:
        set_workspace(workspace)

    log = logging.getLogger("werkzeug")
    log.setLevel(logging.WARNING)

    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, debug=False, use_reloader=False),
        daemon=True,
        name="argon-dashboard",
    )
    t.start()
