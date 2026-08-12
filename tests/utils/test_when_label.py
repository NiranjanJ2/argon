"""Weekdays are computed, never inferred.

Asked "what's on my schedule for the week" on Tuesday 2026-08-11, Argon
answered with four dated lines and got three of the four weekdays wrong:

    08/12 Mon   — actually Wednesday
    08/14 Sat   — actually Friday
    08/16 Mon   — actually Sunday
    08/18 Tue   — correct

The dates were right every time. Nothing in the tool output named a weekday,
so the model did the arithmetic itself, and a confidently wrong weekday is
worse than none at all because he plans around it.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from argon.utils.helpers import when_label

LA = ZoneInfo("America/Los_Angeles")


class TestTheDaysItGotWrong:
    def test_every_line_of_that_answer(self):
        assert when_label("2026-08-12T08:00:00-07:00") == "Wed 08/12, 8:00 AM"
        assert when_label("2026-08-14T23:59:00-07:00") == "Fri 08/14, 11:59 PM"
        assert when_label("2026-08-16T20:00:00-07:00") == "Sun 08/16, 8:00 PM"
        assert when_label("2026-08-18T19:00:00-07:00") == "Tue 08/18, 7:00 PM"


class TestTheFormsTheseArriveIn:
    def test_a_google_utc_stamp(self):
        assert when_label("2026-08-12T00:00:00.000Z") == "Wed 08/12"

    def test_a_datetime(self):
        assert when_label(datetime(2026, 8, 14, 15, 30, tzinfo=LA)) == "Fri 08/14, 3:30 PM"

    def test_a_bare_date(self):
        assert when_label(date(2026, 8, 16)) == "Sun 08/16"

    def test_midnight_carries_no_clock_time(self):
        """A date-only due stamp is a day, not an appointment at 12:00 AM."""
        assert when_label("2026-08-12T00:00:00-07:00") == "Wed 08/12"


class TestItNeverRaises:
    def test_junk_is_none_rather_than_a_crash(self):
        for bad in ("", "nonsense", "2026-13-45", None, 42, {}):
            assert when_label(bad) is None


class TestEveryToolThatReportsATimeCarriesIt:
    def test_calendar_events(self):
        from argon.google.calendar import _fmt_event

        event = _fmt_event({"id": "x", "summary": "Sync",
                            "start": {"dateTime": "2026-08-18T19:00:00-07:00"}})
        assert event["when"] == "Tue 08/18, 7:00 PM"

    def test_tasks(self):
        from argon.google.tasks_store import _to_task

        task = _to_task({"id": "t", "title": "Math homework",
                         "due": "2026-08-16T00:00:00.000Z"})
        assert task["due_when"] == "Sun 08/16"

    def test_a_task_with_no_due_date(self):
        from argon.google.tasks_store import _to_task

        assert _to_task({"id": "t", "title": "SAT prep"})["due_when"] is None
