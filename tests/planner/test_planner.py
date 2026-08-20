"""The afternoon planning moment.

The bug this exists for: anything overdue was assumed still outstanding, with
no way to learn otherwise, so work he had finished stayed on the board for days
and was asked about every evening.
"""

from datetime import datetime

import pytest

from argon import planner


@pytest.fixture(autouse=True)
def _clean():
    from argon.core import store
    store.put_doc("planner", {"last_planned": None})
    yield


def _at(hour, minute=0, day=19):
    return datetime(2026, 8, day, hour, minute)


class TestWhenItOpens:
    def test_not_before_school_is_out(self):
        # He cannot answer "did Chem assign today" at lunchtime.
        assert planner.is_due(_at(11, 0)) is False
        assert planner.is_due(_at(15, 35)) is False

    def test_once_the_lang_post_has_landed(self):
        assert planner.is_due(_at(15, 36)) is True
        assert planner.is_due(_at(19, 0)) is True

    def test_only_once_a_day(self):
        planner.mark_planned("2026-08-19")

        assert planner.is_due(_at(18, 0)) is False

    def test_a_new_day_asks_again(self):
        planner.mark_planned("2026-08-19")

        assert planner.is_due(_at(16, 0, day=20)) is True


class TestLangHomework:
    POST = {
        "posted_at": "2026-08-19T15:37:00-07:00",
        "text": "WEEK 2 - WED 8/19\n1. Read the thing\nHW:\n1. Personal Harper's Index\n2. Read p. 4-7",
    }

    def test_it_reads_the_hw_block_not_the_agenda(self):
        # The post lists classwork first; only what follows "HW:" is homework.
        items = planner.lang_homework([self.POST], "2026-08-19")

        assert items == ["Personal Harper's Index", "Read p. 4-7"]

    def test_none_is_a_real_answer(self):
        post = {"posted_at": "2026-08-19T15:37:00-07:00", "text": "WEEK 2\nHW:\n1. None :)"}

        # Must be empty, not a task called "None" — knowing it is a free night
        # is the point of reading the post at all.
        assert planner.lang_homework([post], "2026-08-19") == []

    def test_yesterdays_post_is_not_todays_homework(self):
        old = dict(self.POST, posted_at="2026-08-18T15:40:00-07:00")

        assert planner.lang_homework([old], "2026-08-19") == []

    def test_a_post_with_no_hw_block_yields_nothing(self):
        post = {"posted_at": "2026-08-19T15:37:00-07:00", "text": "Reminder: picture day"}

        assert planner.lang_homework([post], "2026-08-19") == []


class TestBuild:
    ROWS = [
        {"id": "a", "title": "SAT reading study", "due": "2026-08-15", "subject": "SAT"},
        {"id": "b", "title": "HW 3", "due": "2026-08-19", "subject": "Math"},
        {"id": "c", "title": "Later thing", "due": "2026-08-25", "subject": "Physics"},
        {"id": "d", "title": "Already done", "due": "2026-08-15", "done": True},
    ]

    def test_overdue_and_today_are_separated(self):
        view = planner.build(self.ROWS, now=_at(16, 0))

        assert [i["id"] for i in view["overdue"]] == ["a"]
        assert [i["id"] for i in view["today"]] == ["b"]

    def test_completed_work_is_not_offered_again(self):
        view = planner.build(self.ROWS, now=_at(16, 0))

        assert all(i["id"] != "d" for i in view["overdue"])

    def test_it_says_how_stale_an_overdue_item_is(self):
        view = planner.build(self.ROWS, now=_at(16, 0))

        assert view["overdue"][0]["days_overdue"] == 4

    def test_chem_is_always_asked_and_never_assumed(self):
        view = planner.build([], now=_at(16, 0))
        chem = next(s for s in view["suggestions"] if s["kind"] == "chem")

        # Ticked by default would be inventing work; absent would be asserting
        # a free night. Neither is something Argon can know.
        assert chem["default"] is False
        assert chem["estimate_min"] == planner.CHEM_MINUTES

    def test_lang_homework_arrives_as_a_suggestion(self):
        post = {
            "posted_at": "2026-08-19T15:37:00-07:00",
            "text": "WEEK 2\nHW:\n1. Personal Harper's Index",
        }

        view = planner.build([], lang_posts=[post], now=_at(16, 0))
        lang = [s for s in view["suggestions"] if s["kind"] == "lang"]

        assert [s["title"] for s in lang] == ["Personal Harper's Index"]
        assert lang[0]["default"] is True
