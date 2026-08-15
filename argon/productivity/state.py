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

import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal

from argon.clock import now as _now
from argon.clock import today_key as _today_key
from argon.core import store

Mode = Literal["idle", "working", "napping", "lock_in", "done"]

#: Modes that mean "something is in flight". The rest are resting states.
SESSION_MODES = ("working", "lock_in")

#: The document holding today's state. See argon/core/store.py.
STATE_DOC = "daily_state"

_STATE_LOCK = threading.RLock()


class DailyState:
    """Persisted daily session state.

    This was a JSON file read whole, edited, and written back with no lock and
    no atomic replace — and every reader turned a malformed file into defaults.
    A torn write therefore did not read as a fault; it read as an idle day with
    nothing in it. It is now one transactional document, and unreadable state
    raises rather than quietly becoming an empty day.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._lock = _STATE_LOCK

    def _load(self) -> dict[str, Any]:
        data = store.get_doc(STATE_DOC)
        if not isinstance(data, dict):
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
            "session": None,
            "nap_start": None,
            "onboarding_done": False,
            "ready_to_work_times": [],  # ISO stamps of when he said he was ready
            "notes": [],
        }

    def _save(self, data: dict[str, Any]) -> None:
        store.put_doc(STATE_DOC, data)

    @contextmanager
    def _edit(self) -> Iterator[dict[str, Any]]:
        """Read-modify-write today's state as one transaction.

        Several mutators used to load, change one key and save outside the
        lock, so a Flask API request and an asyncio turn could each write a
        document the other had already superseded.
        """
        with self._lock, store.edit_doc(STATE_DOC, self._defaults()) as data:
            if data.get("date") != _today_key():
                data.clear()
                data.update(self._defaults())
            yield data

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
        with self._edit() as data:
            session = self._begin(data, kind=kind, task_id=task_id, title=title)
        return dict(session)

    @staticmethod
    def _begin(
        data: dict[str, Any], *, kind: str, task_id: str | None, title: str | None
    ) -> dict[str, Any]:
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
        return session

    def end_session(self) -> dict[str, Any] | None:
        """Finish the session and fall back to idle. Returns what ended."""
        with self._edit() as data:
            ended = self._clear_session(data)
        return ended

    def end_session_if_task(
        self, task_id: str, *, title: str | None = None
    ) -> dict[str, Any] | None:
        """End the active session only when it still belongs to ``task_id``.

        The check and the write are one transaction, so a stale dashboard
        request cannot end a session that another request just started.
        ``title`` supports state written before task ids were stored.
        """
        with self._edit() as data:
            session = data.get("session")
            matches_task = session and session.get("task_id") == task_id
            matches_legacy_title = (
                session and not session.get("task_id") and title and session.get("title") == title
            )
            if not matches_task and not matches_legacy_title:
                return None
            ended = self._clear_session(data)
        return ended

    def _clear_session(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """End whatever is running. Caller owns the transaction."""
        session = data.get("session")
        data["session"] = None
        if data.get("mode") in SESSION_MODES:
            data["mode"] = "idle"
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
        with self._edit() as data:
            if mode in SESSION_MODES:
                if session := data.get("session"):
                    # Already going: keep the elapsed time and the task, just
                    # move between working and lock_in.
                    session["kind"] = mode
                    data["mode"] = mode
                    data["nap_start"] = None
                else:
                    self._begin(data, kind=mode, task_id=None, title=None)
                return
            self._clear_session(data)
            data["mode"] = mode
            data["nap_start"] = _now().isoformat() if mode == "napping" else None

    def set_current_task(self, task: str | None) -> None:
        """Kept for callers that only have a title. Prefer ``start_session``."""
        with self._edit() as data:
            if task is None:
                self._clear_session(data)
                return
            session = data.get("session") or {}
            self._begin(
                data,
                kind=session.get("kind", "working"),
                task_id=session.get("task_id"),
                title=task,
            )

    def mark_onboarding_done(self) -> None:
        with self._edit() as data:
            data["onboarding_done"] = True

    def add_note(self, note: str) -> None:
        with self._edit() as data:
            data.setdefault("notes", []).append({
                "time": _now().isoformat(),
                "text": note,
            })

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
