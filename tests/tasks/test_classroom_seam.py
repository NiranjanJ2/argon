"""Classroom assignments against Argon's own store.

An assignment has no task behind it until something needs one. Both halves of
that reason about google_task_id, which every local row now lacks — so this is
the seam most likely to have broken quietly in the migration.
"""

import pytest

from argon.commitments import SourceSnapshot, build_board
from argon.tasks.local_store import LocalTaskStore
from argon.tools.tasks import overlay_for_classroom

ASSIGNMENT = {
    "classroom_key": "871775830226:874660636840",
    "course_id": "871775830226",
    "course_name": "APUSH PM",
    "title": "Chapter 2 Key terms",
    "due": "2026-08-21",
    "id": "874660636840",
}


@pytest.fixture
def tasks(tmp_path):
    return LocalTaskStore(tmp_path)


def _board(tasks):
    return build_board(
        SourceSnapshot("classroom", (dict(ASSIGNMENT),), None, ()),
        SourceSnapshot("tasks", tuple(tasks.get_all()), None, ()),
    )


class TestOverlayRoundTrip:
    def test_an_assignment_with_no_task_is_addressed_by_its_key(self, tasks):
        [row] = _board(tasks).as_dicts()

        assert row["id"] == ASSIGNMENT["classroom_key"]
        assert row["source"] == "classroom"

    def test_starting_one_creates_exactly_one_overlay(self, tasks, monkeypatch):
        monkeypatch.setattr(
            "argon.commitments.load_board", lambda *a, **k: _board(tasks)
        )

        created = overlay_for_classroom(tasks, ASSIGNMENT["classroom_key"])

        assert created is not None
        assert len(tasks.get_all()) == 1
        assert tasks.get_all()[0]["classroom_key"] == ASSIGNMENT["classroom_key"]

    def test_it_does_not_create_a_second_one(self, tasks, monkeypatch):
        monkeypatch.setattr(
            "argon.commitments.load_board", lambda *a, **k: _board(tasks)
        )
        overlay_for_classroom(tasks, ASSIGNMENT["classroom_key"])

        again = overlay_for_classroom(tasks, ASSIGNMENT["classroom_key"])

        # The local row carries no google_task_id, so the "already has a task"
        # check has to fall back to the local id — otherwise every start makes
        # another overlay.
        assert again is None
        assert len(tasks.get_all()) == 1

    def test_the_assignment_stays_one_commitment(self, tasks, monkeypatch):
        monkeypatch.setattr(
            "argon.commitments.load_board", lambda *a, **k: _board(tasks)
        )
        overlay_for_classroom(tasks, ASSIGNMENT["classroom_key"])

        rows = _board(tasks).as_dicts()

        # One assignment, one row — not the assignment plus its overlay.
        assert len(rows) == 1
        assert rows[0]["title"] == "Chapter 2 Key terms"

    def test_the_board_id_still_resolves_after_the_overlay_exists(self, tasks, monkeypatch):
        monkeypatch.setattr(
            "argon.commitments.load_board", lambda *a, **k: _board(tasks)
        )
        overlay_for_classroom(tasks, ASSIGNMENT["classroom_key"])

        [row] = _board(tasks).as_dicts()
        assert tasks._resolve(row["id"]) is not None
