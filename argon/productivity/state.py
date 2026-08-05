"""Daily session state — the single answer to "what is Niranjan doing right now".

State file: workspace/daily/state.json. Resets at 4:00 AM.

That question used to have four independent answers and nothing reconciling
them: ``mode``, ``current_task``, ``work_session_start``, and a ``started_at``
stamp living in Google Tasks metadata. They drifted immediately, because the
transitions that should have kept them together did not exist.

``start_task`` set ``current_task`` but never touched ``mode``, so Argon
interrupted him with "free time, with things outstanding" nudges while he was
working. ``complete_task`` cleared ``current_task`` but left ``mode`` on
"working" with no ``work_session_start``, which made ``pick_occasion`` take the
mid-flow branch, measure a zero-minute session and fall silent for the rest of
the day. And ``started_at`` lived in the durable task store, which has no day
boundary — "SAT prep", started 1:42 AM on 2026-08-02, still read as running on
the evening of the 4th, so ``list_tasks`` reported an open session while
``get_status`` reported an idle day. The model believed the wrong one and texted
"You have an open SAT prep session" to someone who had finished hours earlier.

There is now exactly one record — ``session`` — and ``mode``, ``current_task``
and the durations are projected from it. A running session cannot outlive the
day, because this file is date-keyed and the durable store no longer holds
session state at all.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from argon.clock import now as _now
from argon.clock import today_key as _today_key

Mode = Literal["idle", "working", "napping", "lock_in", "done"]

#: Modes that mean "something is in flight". The rest are resting states.
SESSION_MODES = ("working", "lock_in")


class DailyState:
    """Persisted daily session state."""

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "daily" / "state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return self._defaults()
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return self._defaults()
        # A new day drops everything, the running session included. This is the
        # day boundary that Google Tasks metadata never had.
        if data.get("date") != _today_key():
            return self._defaults()
        return data

    def _defaults(self) -> dict[str, Any]:
        return {
            "date": _today_key(),
            "mode": "idle",
            "home_arrival": None,
            "session": None,
            "nap_start": None,
            "onboarding_done": False,
            "ready_to_work_times": [],  # ISO stamps of when he said he was ready
            "notes": [],
        }

    def _save(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    # ------------------------------------------------------------------
    # The session — one record; everything else is a view of it
    # ------------------------------------------------------------------

    @staticmethod
    def _elapsed_min(session: dict[str, Any] | None) -> int | None:
        if not session or not session.get("started_at"):
            return None
        try:
            started = datetime.fromisoformat(session["started_at"])
        except (TypeError, ValueError):
            return None
        return int((_now() - started).total_seconds() / 60)

    def get_session(self) -> dict[str, Any] | None:
        """The session in flight, with ``elapsed_min`` filled in, or None."""
        session = self._load().get("session")
        if not session:
            return None
        return {**session, "elapsed_min": self._elapsed_min(session)}

    def start_session(
        self,
        *,
        kind: str = "working",
        task_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Begin a session, replacing any already running.

        Starting a second task without finishing the first is a correction, not
        two parallel sessions — he has one attention.
        """
        data = self._load()
        now_iso = _now().isoformat()
        session = {
            "kind": kind if kind in SESSION_MODES else "working",
            "task_id": task_id,
            "title": title,
            "started_at": now_iso,
        }
        data["session"] = session
        data["mode"] = session["kind"]
        data["nap_start"] = None
        data.setdefault("ready_to_work_times", []).append(now_iso)
        self._save(data)
        return dict(session)

    def end_session(self) -> dict[str, Any] | None:
        """Finish the session and fall back to idle. Returns what ended."""
        data = self._load()
        session = data.get("session")
        data["session"] = None
        if data.get("mode") in SESSION_MODES:
            data["mode"] = "idle"
        self._save(data)
        if not session:
            return None
        return {**session, "elapsed_min": self._elapsed_min(session)}

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get(self) -> dict[str, Any]:
        """State with the legacy flat keys projected from the session.

        The status tool, the HTTP API, the check-in gate and the desktop
        readouts all index this dict directly. Projecting here means they see
        one consistent story without any of them knowing the session exists.
        """
        data = self._load()
        session = data.get("session") or {}
        kind = session.get("kind")
        data["current_task"] = session.get("title")
        data["work_session_start"] = session.get("started_at") if kind == "working" else None
        data["lock_in_start"] = session.get("started_at") if kind == "lock_in" else None
        return data

    def set_mode(self, mode: Mode) -> None:
        """Set the mode, starting or ending a session to match.

        A mode is a claim about whether something is in flight, so it cannot be
        set without settling that question. "working" with nothing running is
        the state that muted check-ins for an entire afternoon.
        """
        if mode in SESSION_MODES:
            data = self._load()
            if session := data.get("session"):
                # Already going: keep the elapsed time and the task, just move
                # between working and lock_in.
                session["kind"] = mode
                data["mode"] = mode
                data["nap_start"] = None
                self._save(data)
            else:
                self.start_session(kind=mode)
            return

        self.end_session()
        data = self._load()
        data["mode"] = mode
        data["nap_start"] = _now().isoformat() if mode == "napping" else None
        self._save(data)

    def set_home_arrival(self) -> None:
        data = self._load()
        data["home_arrival"] = _now().isoformat()
        self._save(data)

    def set_current_task(self, task: str | None) -> None:
        """Kept for callers that only have a title. Prefer ``start_session``."""
        if task is None:
            self.end_session()
            return
        session = self._load().get("session") or {}
        self.start_session(
            kind=session.get("kind", "working"),
            task_id=session.get("task_id"),
            title=task,
        )

    def mark_onboarding_done(self) -> None:
        data = self._load()
        data["onboarding_done"] = True
        self._save(data)

    def add_note(self, note: str) -> None:
        data = self._load()
        data.setdefault("notes", []).append({
            "time": _now().isoformat(),
            "text": note,
        })
        self._save(data)

    def get_mode(self) -> Mode:
        return self._load().get("mode", "idle")

    def get_current_task(self) -> str | None:
        session = self._load().get("session")
        return session.get("title") if session else None

    def get_work_session_duration_minutes(self) -> int | None:
        session = self._load().get("session")
        if not session or session.get("kind") != "working":
            return None
        return self._elapsed_min(session)

    def get_lock_in_duration_minutes(self) -> int | None:
        session = self._load().get("session")
        if not session or session.get("kind") != "lock_in":
            return None
        return self._elapsed_min(session)
