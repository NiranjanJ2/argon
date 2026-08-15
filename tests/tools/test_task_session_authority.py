"""Task completion must not end a newer session it did not start."""

from __future__ import annotations

import pytest

from argon.tools.tasks import CompleteTaskTool


@pytest.mark.asyncio
async def test_completing_a_task_cannot_end_a_replacement_session_started_during_completion():
    class State:
        def __init__(self):
            self.session = {"task_id": "old", "title": "Old", "elapsed_min": 12}
            self.compare_calls = []

        def get_session(self):
            return dict(self.session)

        def start_replacement(self):
            self.session = {"task_id": "new", "title": "New", "elapsed_min": 0}

        def end_session(self):
            raise AssertionError("completion must compare-and-end, never unconditionally end")

        def end_session_if_task(self, task_id, *, title=None):
            self.compare_calls.append((task_id, title))
            if self.session.get("task_id") == task_id:
                ended, self.session = self.session, None
                return ended
            return None

    class Store:
        def get_all(self):
            return [{"id": "old", "title": "Old", "priority": "medium"}]

        def complete_task(self, _task_id, *, actual_min):
            assert actual_min == 12
            state.start_replacement()
            return {"id": "old", "title": "Old", "subject": None}

    class Log:
        def log_task_done(self, _title, _actual_min):
            pass

    state = State()
    result = await CompleteTaskTool(Store(), state, Log(), object()).execute(task_id="old")

    assert result == "Done: Old (12min)"
    assert state.compare_calls == [("old", "Old")]
    assert state.session["task_id"] == "new"
