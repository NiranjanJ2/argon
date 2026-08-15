"""Argon has to notice what's on the calendar without being asked.

It could always *read* the calendar — ``get_daily_overview`` has existed the
whole time — but nothing ever looked unprompted. The gate decided from mode,
tasks and the clock, and its prompt never mentioned events, so a meeting booked
in chat at noon produced silence at 6:45 PM.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from argon import clock
from argon.services import agenda
from argon.services.reminder import OCCASIONS, ReminderService


def _event(minutes_out: float, *, id: str = "evt-1", summary: str = "All Project Sync"):
    return {
        "id": id,
        "summary": summary,
        "start": clock.now() + timedelta(minutes=minutes_out),
        "end": clock.now() + timedelta(minutes=minutes_out + 30),
        "location": None,
    }


#: Pinned so the suite means the same thing at 5 PM and at 11 PM. Run for real,
#: these failed after 23:00 because quiet hours silence every occasion — a
#: result that depends on when you run it is not a result.
PINNED_HOUR = 17


@pytest.fixture
def service(tmp_path, monkeypatch):
    async def _never_called(_prompt):  # pragma: no cover - the gate is what is under test
        raise AssertionError("the model must not be woken by these tests")

    from argon.productivity import state as state_mod
    from argon.services import reminder as reminder_mod

    now = clock.now().replace(hour=PINNED_HOUR, minute=0, second=0, microsecond=0)
    monkeypatch.setattr(clock, "now", lambda: now)
    monkeypatch.setattr(agenda.clock, "now", lambda: now)
    monkeypatch.setattr(reminder_mod.clock, "now", lambda: now)
    monkeypatch.setattr(state_mod, "_now", lambda: now)

    svc = ReminderService(tmp_path, "America/Los_Angeles", on_check_in=_never_called,
                          unprompted_from_hour=16)
    monkeypatch.setattr(svc, "_now", lambda: now)
    return svc


@pytest.fixture
def calendar(monkeypatch):
    """Swap the Google round-trip for a list we control."""
    events: list = []
    monkeypatch.setattr(agenda, "today", lambda ws, **kw: list(events))
    return events


class TestDescribe:
    def test_soon_is_counted_in_minutes(self):
        assert "in 12 min" in agenda.describe(_event(12))

    def test_later_today_is_given_as_a_clock_time(self):
        line = agenda.describe(_event(200))
        assert "min" not in line and ("AM" in line or "PM" in line)

    def test_a_location_is_included_when_there_is_one(self):
        event = _event(10)
        event["location"] = "Room 204"
        assert "(Room 204)" in agenda.describe(event)


class TestTheGateNoticesEvents:
    def test_an_imminent_event_is_an_occasion(self, service, calendar):
        calendar.append(_event(10))
        assert service.pick_occasion() is OCCASIONS["upcoming"]

    def test_an_event_hours_away_is_not(self, service, calendar):
        calendar.append(_event(240))
        occasion = service.pick_occasion()
        assert occasion is None or occasion.kind != "upcoming"

    def test_it_beats_the_mid_session_guard(self, service, calendar):
        """Being deep in a task is exactly when you miss what you must leave for."""
        service._state.start_session(kind="working", title="SAT prep")
        calendar.append(_event(10))
        assert service.pick_occasion() is OCCASIONS["upcoming"]

    def test_an_event_is_announced_once(self, service, calendar):
        calendar.append(_event(10))
        assert service.pick_occasion() is OCCASIONS["upcoming"]
        service.ledger.record_announced("evt-1")
        occasion = service.pick_occasion()
        assert occasion is None or occasion.kind != "upcoming"

    def test_a_second_event_still_gets_through(self, service, calendar):
        """A cooldown on the occasion would have swallowed this one."""
        calendar.extend([_event(5), _event(12, id="evt-2", summary="Dentist")])
        service.ledger.record_announced("evt-1")
        assert service.pick_occasion() is OCCASIONS["upcoming"]
        assert service._pending["id"] == "evt-2"

    def test_a_snooze_still_wins(self, service, calendar):
        """"Rest day" has to mean it, calendar or no calendar."""
        from argon.services.reminder import snooze

        snooze(service.workspace, hours=5, reason="rest day")
        calendar.append(_event(10))
        assert service.pick_occasion() is None


class TestThePromptCarriesTheCalendar:
    def test_the_imminent_event_is_stated_outright(self, service, calendar):
        calendar.append(_event(10))
        service.pick_occasion()
        prompt = service.build_prompt(OCCASIONS["upcoming"])

        assert "COMING UP" in prompt and "All Project Sync" in prompt
        # A heads-up, not an announcement. "Your meeting has started" is useless
        # — by then he is in it or already late. The window is fifteen minutes
        # so that there is time to get ready, so the message is about that.
        assert "before it starts" in prompt
        assert "is set for it" in prompt
        assert "Do not say it has started" in prompt

    def test_every_check_in_sees_the_day(self, service, calendar):
        """Not just the upcoming one — any check-in can mention what's coming."""
        calendar.append(_event(200, summary="Dentist"))
        assert "Dentist" in service.build_prompt(OCCASIONS["daily_brief"])

    def test_an_empty_calendar_says_so_rather_than_nothing(self, service, calendar):
        assert "nothing else scheduled" in service.build_prompt(OCCASIONS["daily_brief"])

    def test_the_anti_invention_rule_still_stands(self, service, calendar):
        prompt = service.build_prompt(OCCASIONS["daily_brief"])
        assert "one hard rule" in prompt.lower()
        assert "never an assignment" in prompt
        assert "must have appeared in the tool output" in prompt


class TestFailureIsSilentNotFatal:
    def test_a_calendar_outage_does_not_mute_the_gate(self, service, monkeypatch):
        def boom(*_a, **_kw):
            raise RuntimeError("google work account needs re-authentication")

        monkeypatch.setattr(agenda, "today", boom)
        monkeypatch.setattr(agenda, "starting_soon", boom)

        with pytest.raises(RuntimeError):
            agenda.starting_soon(service.workspace)
        # The gate itself must survive it.
        assert "calendar unavailable" in service.build_prompt(OCCASIONS["daily_brief"])

    def test_an_all_day_event_has_no_start_to_warn_about(self):
        assert agenda._parse({"date": "2026-08-05"}) is None
        assert agenda._parse(None) is None
        assert agenda._parse({"dateTime": "nonsense"}) is None


class TestScheduledRemindersCountToo:
    """Told "remind me at 7", the model writes a cron job, not a calendar event.

    Those are just as much "things he scheduled today". They belong in the
    coming-up list — but not in the ``upcoming`` occasion, because a cron job
    already delivers its own message at its own time.
    """

    @staticmethod
    def _write_jobs(tmp_path, monkeypatch, minutes_out, *, enabled=True):
        """Write one cron job *minutes_out* from a pinned 9 AM.

        These used to offset from the real clock, so "90 minutes from now" fell
        past midnight when the suite ran after 22:30 and `reminders()` — which
        only reports jobs due before end of day — correctly returned nothing.
        The tests failed for the first time at 22:50 on a night the code had
        not changed. Pin the clock; a suite whose result depends on when you
        run it is not evidence of anything.
        """
        import json as _json

        from argon import paths

        pinned = clock.now().replace(hour=9, minute=0, second=0, microsecond=0)
        monkeypatch.setattr(clock, "now", lambda: pinned)
        monkeypatch.setattr(agenda.clock, "now", lambda: pinned)
        at_ms = int((pinned + timedelta(minutes=minutes_out)).timestamp() * 1000)
        store = tmp_path / "jobs.json"
        store.write_text(_json.dumps({"jobs": [{
            "id": "abc123", "name": "Start UCLA work", "enabled": enabled,
            "schedule": {"kind": "at", "atMs": at_ms},
            "payload": {"message": "Start UCLA work (2h)"},
        }]}))
        monkeypatch.setattr(paths, "get_cron_store", lambda: store)
        return store

    def test_a_scheduled_reminder_shows_up(self, tmp_path, monkeypatch, calendar):
        self._write_jobs(tmp_path, monkeypatch, 90)
        merged = agenda.upcoming(tmp_path)

        assert [e["summary"] for e in merged] == ["Start UCLA work (2h)"]
        assert merged[0]["kind"] == "reminder"

    def test_it_never_triggers_a_check_in(self, tmp_path, monkeypatch, service, calendar):
        """It would say the same thing twice — once early, once when it fires."""
        self._write_jobs(tmp_path, monkeypatch, 5)
        occasion = service.pick_occasion()
        assert occasion is None or occasion.kind != "upcoming"

    def test_a_disabled_job_is_not_promised(self, tmp_path, monkeypatch, calendar):
        self._write_jobs(tmp_path, monkeypatch, 90, enabled=False)
        assert agenda.upcoming(tmp_path) == []

    def test_tomorrows_job_is_not_todays_problem(self, tmp_path, monkeypatch, calendar):
        self._write_jobs(tmp_path, monkeypatch, 60 * 20)  # tomorrow, from 9 AM
        assert agenda.upcoming(tmp_path) == []

    def test_events_and_reminders_interleave_by_time(self, tmp_path, monkeypatch, calendar):
        self._write_jobs(tmp_path, monkeypatch, 90)
        calendar.append({**_event(30, summary="Standup"),
                         "start": clock.now() + timedelta(minutes=30)})
        assert [e["summary"] for e in agenda.upcoming(tmp_path)] == [
            "Standup", "Start UCLA work (2h)",
        ]

    def test_a_missing_cron_store_is_not_an_error(self, tmp_path, monkeypatch):
        from argon import paths

        monkeypatch.setattr(paths, "get_cron_store", lambda: tmp_path / "nope.json")
        assert agenda.reminders() == []
