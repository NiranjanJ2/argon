"""Durable local dispositions for individual Google Classroom assignments."""

from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from argon.google.service import LOCAL_TZ
from argon.tools.base import Tool

try:
    import fcntl
except ImportError:  # pragma: no cover - Argon runs on Unix; thread lock is the fallback
    fcntl = None


_thread_lock = threading.RLock()


def assignment_key(course_id: str, coursework_id: str) -> str:
    """Identity for a Classroom item; course-work IDs are only course-local."""
    return f"{course_id}:{coursework_id}"


class ClassroomDispositionStore:
    """Persist the user's ignore decision without mutating Classroom or Tasks."""

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "google" / "classroom-dispositions.json"

    def _load(self, *, strict: bool = False) -> dict[str, dict[str, str]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            if strict:
                raise ValueError(f"Classroom disposition store is unreadable: {exc}") from exc
            return {}
        if not isinstance(raw, dict):
            if strict:
                raise ValueError("Classroom disposition store must contain a JSON object")
            return {}
        validated = {
            str(key): value for key, value in raw.items()
            if isinstance(value, dict) and value.get("state") in {"active", "ignored"}
        }
        if strict and len(validated) != len(raw):
            raise ValueError("Classroom disposition store contains unknown entries")
        return validated

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(f".{self._path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            temp.replace(self._path)
        finally:
            temp.unlink(missing_ok=True)

    @contextmanager
    def _locked(self):
        """Serialize read-modify-write across tool threads and Argon processes."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(f"{self._path.suffix}.lock")
        with _thread_lock, lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def is_ignored(self, key: str) -> bool:
        """Has he told Argon to drop this assignment?

        Strict on purpose. The lenient read turned an unreadable file into "no
        dispositions", so a corrupt or truncated store silently un-ignored
        everything: work he had explicitly dismissed came back as due, with
        nothing anywhere saying why. Refusing to answer is recoverable; a
        confident wrong answer is what costs him trust in the board.
        """
        return self._load(strict=True).get(key, {}).get("state") == "ignored"

    def ignore(self, key: str) -> None:
        with self._locked():
            data = self._load(strict=True)
            data[key] = {"state": "ignored", "updated_at": datetime.now(LOCAL_TZ).isoformat()}
            self._write(data)

    def restore(self, key: str) -> None:
        with self._locked():
            data = self._load(strict=True)
            data.pop(key, None)
            self._write(data)


class _ClassroomDispositionTool(Tool):
    action = ""

    def __init__(self, workspace: Path) -> None:
        self._store = ClassroomDispositionStore(workspace)

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "course_id": {"type": "string"},
                "assignment_id": {"type": "string"},
            },
            "required": ["course_id", "assignment_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        key = assignment_key(kwargs["course_id"], kwargs["assignment_id"])
        if self.action == "ignore":
            self._store.ignore(key)
            result = f"Ignored Classroom assignment: {key}"
        else:
            self._store.restore(key)
            result = f"Restored Classroom assignment: {key}"
        from argon import commitments
        from argon.services import agenda

        agenda.invalidate_schoolwork()
        # The board caches Classroom; without this an assignment he just told
        # Argon to ignore keeps being announced for another five minutes.
        commitments.invalidate()
        return result


class IgnoreClassroomAssignmentTool(_ClassroomDispositionTool):
    action = "ignore"

    @property
    def name(self) -> str:
        return "ignore_classroom_assignment"

    @property
    def description(self) -> str:
        return "Hide one Classroom assignment from Argon's active schoolwork views."


class RestoreClassroomAssignmentTool(_ClassroomDispositionTool):
    action = "restore"

    @property
    def name(self) -> str:
        return "restore_classroom_assignment"

    @property
    def description(self) -> str:
        return "Restore one ignored Classroom assignment to Argon's active schoolwork views."
