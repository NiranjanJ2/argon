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


class TestStartTime:
    """The time he says he will begin, and the two jobs that hang off it."""

    class FakeCron:
        """Mirrors CronService's real surface.

        The first version of this exposed a `.jobs` attribute, which the real
        service does not have — so the code read an empty list, never cleared
        anything, and the test passed anyway. A fake that invents a nicer API
        than the real one tests nothing.
        """

        class _Job:
            def __init__(self, job_id, name):
                self.id = job_id
                self.name = name

        def __init__(self):
            self._jobs = []
            self.added = []
            self.removed = []

        def list_jobs(self, include_disabled: bool = False):
            return list(self._jobs)

        def add_job(self, *, name, schedule, message, kind, delete_after_run):
            self.added.append({"name": name, "at_ms": schedule.at_ms, "kind": kind})
            self._jobs.append(self._Job(f"j{len(self._jobs)}", name))

        def remove_job(self, job_id):
            self.removed.append(job_id)
            self._jobs = [j for j in self._jobs if j.id != job_id]
            return True

    def test_it_arms_a_warning_and_a_block(self):
        cron = self.FakeCron()
        now = _at(16, 0)

        out = planner.schedule_start(cron, "18:00", now=now)

        names = [j["name"] for j in cron.added]
        assert names == [planner.NOTIFY_JOB, planner.BLOCK_JOB]
        assert out["start_at"] == "18:00"

    def test_the_warning_lands_half_an_hour_early(self):
        cron = self.FakeCron()

        planner.schedule_start(cron, "18:00", now=_at(16, 0))

        warn, block = cron.added
        gap_minutes = (block["at_ms"] - warn["at_ms"]) / 60000
        assert gap_minutes == planner.WARNING_MINUTES

    def test_a_start_inside_the_warning_window_still_blocks(self):
        # Chosen 18:00 at 17:45: there is no time to warn him, but the block
        # he asked for must still happen.
        cron = self.FakeCron()

        planner.schedule_start(cron, "18:00", now=_at(17, 45))

        assert [j["name"] for j in cron.added] == [planner.BLOCK_JOB]

    def test_a_time_that_has_already_passed_arms_nothing(self):
        cron = self.FakeCron()

        out = planner.schedule_start(cron, "09:00", now=_at(16, 0))

        assert cron.added == []
        assert out["note"] == "already passed"
        # Still recorded — it is what he intended, and the screen should show it.
        assert out["start_at"] == "09:00"

    def test_clearing_the_time_cancels_instead_of_leaving_a_block_armed(self):
        cron = self.FakeCron()
        planner.schedule_start(cron, "18:00", now=_at(16, 0))
        cron.added.clear()

        out = planner.schedule_start(cron, None, now=_at(16, 0))

        assert cron.added == []
        assert out["start_at"] is None
        assert planner.start_time() is None

    def test_the_start_time_belongs_to_today_only(self):
        planner.set_start_time("18:00", day="2026-08-18")

        # Yesterday's plan must not silently govern today.
        assert planner.start_time() is None


class TestUndatedClassroomWork:
    """Teachers post notices as coursework, and notices have no due date."""

    def test_an_undated_classroom_item_is_not_a_project(self):
        rows = [{
            "id": "notice",
            "title": "Reminder:  chapter 2 InQuizitive due tonight!",
            "due": None,
            "source": "classroom",
        }]

        view = planner.build(rows, now=_at(16, 0))

        assert view["long_term"] == []

    def test_his_own_undated_work_still_is(self):
        rows = [{"id": "sat", "title": "SAT reading study", "due": None, "source": "tasks"}]

        view = planner.build(rows, now=_at(16, 0))

        assert [i["id"] for i in view["long_term"]] == ["sat"]

    def test_dated_classroom_work_far_out_is_still_long_term(self):
        rows = [{"id": "essay", "title": "Research essay", "due": "2026-09-30",
                 "source": "classroom"}]

        view = planner.build(rows, now=_at(16, 0))

        assert [i["id"] for i in view["long_term"]] == ["essay"]


class TestWhatLongTermActuallyMeans:
    """Everything still on the list that today is not forcing.

    The first version was a fortnight horizon, which was invented rather than
    asked for: nearly everything sits a day or three out, so the list came back
    empty every single time.
    """

    ROWS = [
        {"id": "late", "title": "Old thing", "due": "2026-08-19"},
        {"id": "now", "title": "HW 5", "due": "2026-08-20"},
        {"id": "soon", "title": "HW 6", "due": "2026-08-21"},
        {"id": "sunday", "title": "InQuizitive", "due": "2026-08-23", "source": "classroom"},
        {"id": "sat", "title": "SAT prep", "due": None, "source": "tasks"},
        {"id": "notice", "title": "Reminder: picture day", "due": None, "source": "classroom"},
    ]

    def _view(self):
        return planner.build(self.ROWS, now=_at(17, 0, day=20))

    def test_tomorrows_work_is_offered(self):
        # A thing due Friday is the main candidate on a Wednesday.
        assert "soon" in {i["id"] for i in self._view()["long_term"]}

    def test_work_later_in_the_week_is_offered(self):
        assert "sunday" in {i["id"] for i in self._view()["long_term"]}

    def test_undated_work_of_his_own_is_offered(self):
        assert "sat" in {i["id"] for i in self._view()["long_term"]}

    def test_overdue_and_today_are_not_repeated_there(self):
        ids = {i["id"] for i in self._view()["long_term"]}
        assert "late" not in ids and "now" not in ids

    def test_an_undated_classroom_notice_is_still_excluded(self):
        assert "notice" not in {i["id"] for i in self._view()["long_term"]}
