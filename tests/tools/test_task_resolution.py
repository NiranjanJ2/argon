from __future__ import annotations

from googleapiclient.errors import HttpError

from argon.google.tasks_store import GoogleTasksStore
from argon.tools.tasks import CompleteTaskTool, StartTaskTool, UpdateTaskTool


class _Response:
    status = 404
    reason = "Not Found"

    def getheaders(self):
        return {}


class _Request:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class _Tasks:
    def __init__(self, items):
        self.items = {item["id"]: dict(item) for item in items}
        self.patch_calls = []
        self.list_calls = 0

    def get(self, *, task, **_kwargs):
        if task in self.items:
            return _Request(dict(self.items[task]))
        return _Request(error=HttpError(_Response(), b'{"error":{"message":"not found"}}'))

    def list(self, **_kwargs):
        self.list_calls += 1
        return _Request({"items": list(self.items.values())})

    def patch(self, *, task, body, **_kwargs):
        self.patch_calls.append((task, body))
        updated = {**self.items[task], **body}
        self.items[task] = updated
        return _Request(updated)


class _Service:
    def __init__(self, tasks):
        self._tasks = tasks

    def tasks(self):
        return self._tasks


def _store(tmp_path, items):
    tasks = _Tasks(items)
    store = GoogleTasksStore(tmp_path)
    store._service = _Service(tasks)
    store._tl_id = "list"
    return store, tasks


class _State:
    def __init__(self):
        self.started = []

    def get_session(self):
        return None

    def start_session(self, **kwargs):
        self.started.append(kwargs)


class _Log:
    def __init__(self):
        self.started = []
        self.completed = []

    def log_task_started(self, title):
        self.started.append(title)

    def log_task_done(self, title, actual_min):
        self.completed.append((title, actual_min))


async def test_start_uses_exact_id_without_title_search(tmp_path):
    store, tasks = _store(tmp_path, [{"id": "task-2", "title": "Report final"}])
    state, log = _State(), _Log()

    result = await StartTaskTool(store, state, log, auto_focus=False).execute(task_id="task-2")

    assert result == "Started: Report final"
    assert tasks.list_calls == 0
    assert state.started == [{"kind": "working", "task_id": "task-2", "title": "Report final"}]


async def test_start_prefers_one_exact_normalized_title_over_substrings(tmp_path):
    store, _tasks = _store(tmp_path, [
        {"id": "exact", "title": "Quarterly Report"},
        {"id": "substring", "title": "Quarterly Report outline"},
    ])
    state, log = _State(), _Log()

    result = await StartTaskTool(store, state, log, auto_focus=False).execute(
        task_id="quarterly-report"
    )

    assert result == "Started: Quarterly Report"
    assert state.started[0]["task_id"] == "exact"


async def test_start_surfaces_ambiguous_substring_without_starting_a_session(tmp_path):
    store, _tasks = _store(tmp_path, [
        {"id": "draft-id", "title": "Report draft"},
        {"id": "final-id", "title": "Report final"},
    ])
    state, log = _State(), _Log()

    result = await StartTaskTool(store, state, log).execute(task_id="report")

    assert result.success is False
    assert "Ambiguous task 'report'" in result
    assert "Report draft (draft-id)" in result
    assert "Report final (final-id)" in result
    assert state.started == []
    assert log.started == []


async def test_complete_surfaces_ambiguous_exact_title_without_patching_or_logging(tmp_path):
    store, tasks = _store(tmp_path, [
        {"id": "punctuated", "title": "Report!"},
        {"id": "plain", "title": "report"},
    ])
    state, log = _State(), _Log()

    result = await CompleteTaskTool(store, state, log, object()).execute(task_id="report")

    assert result.success is False
    assert "Ambiguous task 'report'" in result
    assert "Report! (punctuated)" in result
    assert "report (plain)" in result
    assert tasks.patch_calls == []
    assert log.completed == []


async def test_update_surfaces_ambiguous_substring_before_any_patch(tmp_path):
    store, tasks = _store(tmp_path, [
        {"id": "draft-id", "title": "Report draft"},
        {"id": "final-id", "title": "Report final"},
    ])

    result = await UpdateTaskTool(store).execute(
        task_id="report", priority="high", due="2026-08-20"
    )

    assert result.success is False
    assert "Ambiguous task 'report'" in result
    assert tasks.patch_calls == []
