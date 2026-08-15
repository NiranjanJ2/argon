from concurrent.futures import ThreadPoolExecutor

import pytest

from argon.google.classroom_dispositions import (
    ClassroomDispositionStore,
    IgnoreClassroomAssignmentTool,
    RestoreClassroomAssignmentTool,
)
from argon.google.service import google_tools


def test_ignore_and_restore_are_durable_per_composite_assignment_key(tmp_path):
    key = "course-a:42"
    first = ClassroomDispositionStore(tmp_path)

    first.ignore(key)

    reopened = ClassroomDispositionStore(tmp_path)
    assert reopened.is_ignored(key)
    assert not reopened.is_ignored("course-b:42")

    reopened.restore(key)

    assert not ClassroomDispositionStore(tmp_path).is_ignored(key)


def test_an_unreadable_disposition_file_does_not_silently_un_ignore_everything(tmp_path):
    """This used to "fail open", which means: quietly forget what he decided.

    An assignment he had explicitly told Argon to drop came back onto the board
    as due, with nothing anywhere explaining why — and the board is the thing
    he is supposed to be able to stop reconciling by hand. Refusing to answer
    is recoverable; a confident wrong answer is not.
    """
    path = tmp_path / "google" / "classroom-dispositions.json"
    path.parent.mkdir()
    path.write_text("not json")

    with pytest.raises(ValueError, match="unreadable"):
        ClassroomDispositionStore(tmp_path).is_ignored("course:42")
    assert path.read_text() == "not json", "and it is never overwritten"


def test_mutation_refuses_to_overwrite_an_invalid_disposition_file(tmp_path):
    path = tmp_path / "google" / "classroom-dispositions.json"
    path.parent.mkdir()
    path.write_text("not json")

    with pytest.raises(ValueError, match="disposition"):
        ClassroomDispositionStore(tmp_path).ignore("course:42")

    assert path.read_text() == "not json"


def test_mutation_preserves_file_with_unknown_disposition_entries(tmp_path):
    path = tmp_path / "google" / "classroom-dispositions.json"
    path.parent.mkdir()
    original = '{"course:known":{"state":"ignored"},"future":{"state":"snoozed"}}'
    path.write_text(original)

    with pytest.raises(ValueError, match="disposition"):
        ClassroomDispositionStore(tmp_path).restore("course:known")

    assert path.read_text() == original


def test_concurrent_ignores_do_not_lose_decisions(tmp_path):
    keys = [f"course:{index}" for index in range(40)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(ClassroomDispositionStore(tmp_path).ignore, keys))

    reopened = ClassroomDispositionStore(tmp_path)
    assert all(reopened.is_ignored(key) for key in keys)


def test_disposition_tools_are_available_with_google_tools(tmp_path):
    names = {tool.name for tool in google_tools(tmp_path)}

    assert {"ignore_classroom_assignment", "restore_classroom_assignment"} <= names


async def test_disposition_tools_ignore_then_restore_the_same_assignment(tmp_path):
    await IgnoreClassroomAssignmentTool(tmp_path).execute(course_id="course", assignment_id="42")
    assert ClassroomDispositionStore(tmp_path).is_ignored("course:42")

    await RestoreClassroomAssignmentTool(tmp_path).execute(course_id="course", assignment_id="42")
    assert not ClassroomDispositionStore(tmp_path).is_ignored("course:42")


async def test_disposition_tool_invalidates_cached_schoolwork(tmp_path):
    from argon.services import agenda

    agenda._schoolwork = (0.0, [{"title": "stale"}])

    await IgnoreClassroomAssignmentTool(tmp_path).execute(
        course_id="course", assignment_id="42"
    )

    assert agenda._schoolwork is None
