"""Starting a Classroom assignment that has no Google Task behind it.

Every commitment on the board can be Classroom-only — that is the normal state,
not an edge case — and none of them could be started, because there was no task
record to start. The board offered five things to work on and all five answered
"task not found".
"""

from typing import Any

import pytest

from argon.tools import tasks as tasks_module
from argon.tools.tasks import StartTaskTool, overlay_for_classroom

ASSIGNMENT = {
    "id": "871775830226:874660636840",
    "title": "Chapter 2 Key terms",
    "source": "classroom",
    "google_task_id": None,
    "priority": "medium",
    "due": "2026-08-19",
    "official_due": "2026-08-19T23:59:00",
    "subject": "APUSH PM",
    "classroom_id": "874660636840",
    "classroom_key": "871775830226:874660636840",
}


class FakeBoard:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def as_dicts(self) -> list[dict[str, Any]]:
        return self._rows


class FakeStore:
    workspace = "/tmp/does-not-matter"

    def __init__(self) -> None:
        self.added: list[dict[str, Any]] = []
        self.started: list[str] = []
        self.tasks: dict[str, dict[str, Any]] = {}

    def add_task(self, **kwargs: Any) -> dict[str, Any]:
        self.added.append(kwargs)
        task = {"id": "gtask-1", "title": kwargs["title"]}
        self.tasks["gtask-1"] = task
        return task

    def start_task(self, task_id: str) -> dict[str, Any] | None:
        if task_id in self.tasks:
            self.started.append(task_id)
            return self.tasks[task_id]
        return None


class _State:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []

    def start_session(self, **kwargs: Any) -> None:
        self.started.append(kwargs)


class _Log:
    def log_task_started(self, title: str) -> None:
        pass


@pytest.fixture
def board(monkeypatch):
    rows = [dict(ASSIGNMENT)]
    monkeypatch.setattr(
        "argon.commitments.load_board", lambda *a, **k: FakeBoard(rows)
    )
    return rows


class TestOverlayCreation:
    def test_an_assignment_gets_a_task_carrying_its_classroom_identity(self, board):
        store = FakeStore()

        overlay_for_classroom(store, ASSIGNMENT["id"])

        [created] = store.added
        # The key is what stops the board showing the assignment and its overlay
        # as two separate commitments.
        assert created["classroom_key"] == ASSIGNMENT["classroom_key"]
        assert created["source"] == "classroom"
        assert created["title"] == "Chapter 2 Key terms"

    def test_an_assignment_that_already_has_a_task_is_left_alone(self, board):
        board[0]["google_task_id"] = "gtask-existing"
        store = FakeStore()

        assert overlay_for_classroom(store, ASSIGNMENT["id"]) is None
        assert store.added == []

    def test_an_unknown_id_creates_nothing(self, board):
        store = FakeStore()

        assert overlay_for_classroom(store, "not-on-the-board") is None
        assert store.added == []

    def test_a_manual_task_is_not_given_an_overlay(self, board):
        board[0]["source"] = "tasks"
        store = FakeStore()

        assert overlay_for_classroom(store, ASSIGNMENT["id"]) is None
        assert store.added == []


class TestStartingOne:
    async def test_starting_a_classroom_assignment_works(self, board, monkeypatch):
        monkeypatch.setattr(tasks_module, "engage_task_focus", lambda task: None)
        store, state = FakeStore(), _State()

        result = await StartTaskTool(store, state, _Log()).execute(task_id=ASSIGNMENT["id"])

        assert "Started" in result
        assert store.started == ["gtask-1"]
        # The session must name the task that now exists, not the board row —
        # completing later looks the session up by the id it was started with.
        assert state.started[0]["task_id"] == "gtask-1"

    async def test_a_genuinely_missing_task_still_says_so(self, board, monkeypatch):
        monkeypatch.setattr(tasks_module, "engage_task_focus", lambda task: None)
        store = FakeStore()

        result = await StartTaskTool(store, _State(), _Log()).execute(task_id="nope")

        assert "No task matching" in result


class TestSettlingDone:
    """His "done" has to outlive a Classroom sync that never sees a submission."""

    def test_completing_records_a_done_disposition(self, tmp_path):
        from argon.google.classroom_dispositions import ClassroomDispositionStore
        from argon.tools.tasks import settle_classroom_done

        store = FakeStore()
        store.workspace = tmp_path

        settle_classroom_done(store, {"id": "gtask-1", "classroom_key": "c:1"})

        assert ClassroomDispositionStore(tmp_path).is_done("c:1")

    def test_a_plain_task_records_nothing(self, tmp_path):
        from argon.google.classroom_dispositions import ClassroomDispositionStore
        from argon.tools.tasks import settle_classroom_done

        store = FakeStore()
        store.workspace = tmp_path

        settle_classroom_done(store, {"id": "gtask-1"})

        assert ClassroomDispositionStore(tmp_path).settled("c:1") is None

    def test_done_and_ignored_stay_distinguishable(self, tmp_path):
        # Both suppress, but they are different decisions and he may undo either.
        from argon.google.classroom_dispositions import ClassroomDispositionStore

        store = ClassroomDispositionStore(tmp_path)
        store.complete("c:done")
        store.ignore("c:skip")

        assert store.settled("c:done") == "done"
        assert store.settled("c:skip") == "ignored"
        assert store.is_done("c:done") and not store.is_done("c:skip")
        assert store.is_ignored("c:skip") and not store.is_ignored("c:done")

    def test_restoring_puts_it_back_on_the_board(self, tmp_path):
        from argon.google.classroom_dispositions import ClassroomDispositionStore

        store = ClassroomDispositionStore(tmp_path)
        store.complete("c:1")
        store.restore("c:1")

        assert store.settled("c:1") is None
