"""Google Tasks-backed task store — single source of truth for Niranjan's tasks.

Tasks live in a dedicated "Argon" task list on the work Google account.
Argon-specific metadata (priority, subject, source, time tracking) is encoded
in the task's notes field as a compact JSON line prefixed with ``~argon~``.

Notes format (what Google Tasks / Calendar shows):
    ~argon~{"p":"h","s":"cl","sub":"AP Chem","e":45,"cid":"abc123"}
    Actual human-readable notes go here

Metadata keys (short to minimize notes token cost):
    p   — priority code: h=high, m=medium, l=low  (omitted when medium)
    s   — source code: cl=classroom, ma=manual, uc=ucla, cb=club  (omitted when manual)
    sub — subject/course name
    e   — estimated minutes
    act — actual minutes (written on completion)
    cid — Google Classroom assignment ID (dedup key)
    sat — legacy start stamp; session state now lives in DailyState, and any
          surviving `sat` is stripped on completion
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from argon.google.classroom import classroom_due
from argon.google.service import LOCAL_TZ

_TZ = LOCAL_TZ
_MARKER = "~argon~"
_TASKLIST_NAME = "Argon"

_PRIORITY_ENCODE = {"high": "h", "medium": "m", "low": "l"}
_PRIORITY_DECODE = {"h": "high", "m": "medium", "l": "low"}
_SOURCE_ENCODE = {"classroom": "cl", "manual": "ma", "ucla": "uc", "club": "cb"}
_SOURCE_DECODE = {"cl": "classroom", "ma": "manual", "uc": "ucla", "cb": "club"}


def _now() -> datetime:
    return datetime.now(_TZ)


# ---------------------------------------------------------------------------
# Notes encoding/decoding
# ---------------------------------------------------------------------------

def _encode_meta(meta: dict[str, Any], user_notes: str | None) -> str:
    compact = {k: v for k, v in meta.items() if v is not None}
    parts: list[str] = []
    if compact:
        parts.append(f"{_MARKER}{json.dumps(compact, separators=(',', ':'))}")
    if user_notes:
        parts.append(user_notes)
    return "\n".join(parts)


def _decode_meta(raw: str | None) -> tuple[dict[str, Any], str]:
    if not raw:
        return {}, ""
    parts = raw.split("\n", 1)
    first = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    if first.startswith(_MARKER):
        try:
            return json.loads(first[len(_MARKER):]), rest
        except Exception:
            pass
    return {}, raw


# ---------------------------------------------------------------------------
# Task dict conversion
# ---------------------------------------------------------------------------

def _when(stamp: str | None) -> str | None:
    from argon.utils.helpers import when_label

    return when_label(stamp)


def _to_task(gt: dict[str, Any]) -> dict[str, Any]:
    """Convert a Google Tasks API item to an Argon task dict."""
    meta, notes = _decode_meta(gt.get("notes"))
    return {
        "id": gt["id"],
        "title": gt.get("title", ""),
        "done": gt.get("status") == "completed",
        "done_at": gt.get("completed"),
        "priority": _PRIORITY_DECODE.get(meta.get("p", "m"), "medium"),
        "source": _SOURCE_DECODE.get(meta.get("s", "ma"), "manual"),
        "subject": meta.get("sub"),
        "notes": notes or None,
        "due": gt.get("due"),
        # Weekday spelled out; the model invents them otherwise.
        "due_when": _when(gt.get("due")),
        "classroom_id": meta.get("cid"),
        "time_estimate_min": meta.get("e"),
        "time_actual_min": meta.get("act"),
        "started_at": meta.get("sat"),
        "google_task_id": gt["id"],
    }


# ---------------------------------------------------------------------------
# Classroom helpers
# ---------------------------------------------------------------------------

def _due_stamp(dt: datetime) -> str:
    """Google Tasks stores dates only — normalize to midnight RFC3339."""
    return dt.strftime("%Y-%m-%dT00:00:00.000Z")


def _infer_priority(assignment: dict[str, Any]) -> str:
    """High if due within a day, medium within three, else low."""
    due = classroom_due(assignment)
    if due is None:
        return "medium"
    delta_h = (due - _now()).total_seconds() / 3600
    if delta_h < 24:
        return "high"
    if delta_h < 72:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# GoogleTasksStore
# ---------------------------------------------------------------------------

class GoogleTasksStore:
    """Google Tasks-backed replacement for DailyTodo.

    All task operations go directly to Google Tasks — no local JSON, no sync step.
    Tasks are stored in an "Argon" task list on the work account so they appear
    in Google Calendar's task panel and are visible on all devices.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._service = None       # cached API client
        self._tl_id: str | None = None  # cached task list ID

    def _svc(self):
        """Google Tasks client for the work account, built once per store."""
        if self._service is None:
            from argon.google.service import build_google_service
            self._service = build_google_service(self._workspace, "tasks", "v1", "work")
        return self._service

    def _tl(self) -> str:
        """Get (or create) the Argon task list ID, cached for the session."""
        if self._tl_id:
            return self._tl_id
        svc = self._svc()
        for tl in svc.tasklists().list(maxResults=100).execute().get("items", []):
            if tl.get("title") == _TASKLIST_NAME:
                self._tl_id = tl["id"]
                return self._tl_id
        created = svc.tasklists().insert(body={"title": _TASKLIST_NAME}).execute()
        self._tl_id = created["id"]
        return self._tl_id

    def _resolve(self, svc, task_id: str) -> dict[str, Any] | None:
        """Find a task by exact Google Tasks ID or by title substring match."""
        from googleapiclient.errors import HttpError

        tl = self._tl()
        try:
            return svc.tasks().get(tasklist=tl, task=task_id).execute()
        except HttpError:
            pass  # not an ID — fall through to a title search
        items = svc.tasks().list(
            tasklist=tl, showCompleted=False, maxResults=100
        ).execute().get("items", [])
        needle = task_id.lower()
        for t in items:
            if needle in t.get("title", "").lower():
                return t
        return None

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_all(self, *, include_done: bool = False) -> list[dict[str, Any]]:
        """Return tasks sorted by priority then due date (pending only by default)."""
        items = self._svc().tasks().list(
            tasklist=self._tl(), showCompleted=include_done,
            showHidden=include_done, maxResults=100,
        ).execute().get("items", [])
        tasks = [_to_task(t) for t in items]
        _p = {"high": 0, "medium": 1, "low": 2}
        tasks.sort(key=lambda t: (_p.get(t["priority"], 1), t["due"] or "9999"))
        return tasks

    def get_pending(self) -> list[dict[str, Any]]:
        return self.get_all()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_task(
        self,
        title: str,
        *,
        source: str = "manual",
        priority: str = "medium",
        due: str | None = None,
        subject: str | None = None,
        notes: str | None = None,
        carry_over: bool = False,
        classroom_id: str | None = None,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        if priority != "medium":
            meta["p"] = _PRIORITY_ENCODE.get(priority, "m")
        if source != "manual":
            meta["s"] = _SOURCE_ENCODE.get(source, "ma")
        if subject:
            meta["sub"] = subject
        if classroom_id:
            meta["cid"] = classroom_id

        body: dict[str, Any] = {"title": title}
        encoded = _encode_meta(meta, notes)
        if encoded:
            body["notes"] = encoded
        if due:
            try:
                body["due"] = _due_stamp(datetime.fromisoformat(due))
            except ValueError:
                logger.warning(f"add_task: unparseable due date {due!r}, task created without one")

        created = self._svc().tasks().insert(tasklist=self._tl(), body=body).execute()
        return _to_task(created)

    def set_time_estimate(self, task_id: str, minutes: int) -> bool:
        svc = self._svc()
        target = self._resolve(svc, task_id)
        if not target:
            return False
        meta, notes = _decode_meta(target.get("notes"))
        meta["e"] = minutes
        svc.tasks().patch(
            tasklist=self._tl(), task=target["id"],
            body={"notes": _encode_meta(meta, notes)},
        ).execute()
        return True

    def start_task(self, task_id: str) -> dict[str, Any] | None:
        """Resolve the task being started. Returns the task dict or None.

        This used to stamp ``sat`` into the task's own metadata, which made the
        durable store the owner of session state — and the durable store has no
        day boundary. ``DailyState`` owns the running session now; this only
        confirms the task exists.
        """
        target = self._resolve(self._svc(), task_id)
        return _to_task(target) if target else None

    def complete_task(
        self, task_id: str, *, actual_min: int | None = None
    ) -> dict[str, Any] | None:
        """Complete a task. Returns the completed task dict, or None.

        ``actual_min`` comes from the caller's session — this store cannot know
        how long he worked, only what he worked on.
        """
        svc = self._svc()
        target = self._resolve(svc, task_id)
        if not target:
            return None

        meta, notes = _decode_meta(target.get("notes"))
        if actual_min is not None:
            meta["act"] = actual_min
        meta.pop("sat", None)  # heal tasks still carrying a legacy start stamp

        body: dict[str, Any] = {"status": "completed"}
        encoded = _encode_meta(meta, notes)
        if encoded:
            body["notes"] = encoded
        updated = svc.tasks().patch(
            tasklist=self._tl(), task=target["id"], body=body
        ).execute()

        result = _to_task(updated)
        result["time_actual_min"] = actual_min
        return result

    def update_priority(self, task_id: str, priority: str) -> bool:
        svc = self._svc()
        target = self._resolve(svc, task_id)
        if not target:
            return False
        meta, notes = _decode_meta(target.get("notes"))
        if priority == "medium":
            meta.pop("p", None)
        else:
            meta["p"] = _PRIORITY_ENCODE.get(priority, "m")
        svc.tasks().patch(
            tasklist=self._tl(), task=target["id"],
            body={"notes": _encode_meta(meta, notes)},
        ).execute()
        return True

    def carry_over_task(self, task_id: str) -> bool:
        """Push due date to tomorrow."""
        return self.update_due(task_id, (_now() + timedelta(days=1)).isoformat())

    def update_due(self, task_id: str, due_iso: str) -> bool:
        """Set an arbitrary due date (ISO 8601 string)."""
        try:
            due_dt = datetime.fromisoformat(due_iso)
        except ValueError:
            logger.warning(f"update_due: unparseable due date {due_iso!r}")
            return False
        svc = self._svc()
        target = self._resolve(svc, task_id)
        if not target:
            return False
        svc.tasks().patch(
            tasklist=self._tl(), task=target["id"], body={"due": _due_stamp(due_dt)},
        ).execute()
        return True

    def bulk_add_from_classroom(self, assignments: list[dict[str, Any]]) -> int:
        """Add Classroom assignments as tasks, skipping duplicates by classroom_id."""
        # Completed tasks count as duplicates too, or finished work reappears.
        existing_cids = {
            t["classroom_id"]
            for t in self.get_all(include_done=True)
            if t.get("classroom_id")
        }
        added = 0
        for a in assignments:
            cid = a.get("id")
            if cid and cid in existing_cids:
                continue
            due = classroom_due(a)
            self.add_task(
                title=a.get("title", "Untitled"),
                source="classroom",
                due=due.isoformat() if due else None,
                subject=a.get("course_name"),
                classroom_id=cid,
                priority=_infer_priority(a),
            )
            if cid:
                existing_cids.add(cid)
            added += 1
        return added
