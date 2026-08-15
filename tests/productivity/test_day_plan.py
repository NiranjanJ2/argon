"""The plan is the schedule — Argon reaches out at times he chose.

Before this it reached out on a timer: generic windows plus an `idle` nudge
every two hours whenever any task was open. From the receiving end that is
indistinguishable from random, and the natural response to a message that
arrives for no reason is to stop reading them.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from argon.productivity import plan as plan_mod
from argon.productivity.plan import BLOCK_GRACE_MINUTES, DayPlan, normalize_time

LA = ZoneInfo("America/Los_Angeles")


def _at(hour, minute=0):
    return datetime(2026, 7, 30, hour, minute, tzinfo=LA)


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-07-30")
    monkeypatch.setattr(plan_mod.clock, "now", lambda: _at(12))
    return DayPlan(tmp_path)


class TestReadingHisTimes:
    def test_the_forms_he_actually_says(self):
        assert normalize_time("14:00") == "14:00"
        assert normalize_time("2pm") == "14:00"
        assert normalize_time("2:30 PM") == "14:30"
        assert normalize_time("9am") == "09:00"

    def test_midnight_and_noon_are_the_awkward_ones(self):
        assert normalize_time("12am") == "00:00"
        assert normalize_time("12pm") == "12:00"

    def test_nonsense_is_refused_rather_than_guessed(self):
        for bad in ("", "later", "25:00", "13pm", "2:99pm", None):
            assert normalize_time(bad) is None


class TestSettingThePlan:
    def test_blocks_are_stored_in_time_order(self, plan):
        plan.set_blocks([
            {"start": "5pm", "what": "Gym"},
            {"start": "2pm", "end": "4pm", "what": "SAT prep"},
        ])
        assert [b.what for b in plan.blocks()] == ["SAT prep", "Gym"]

    def test_an_unreadable_block_is_dropped_not_guessed(self, plan):
        """A block silently mis-timed is worse than one he can see is missing."""
        stored = plan.set_blocks([
            {"start": "sometime", "what": "Gym"},
            {"start": "2pm", "what": "SAT prep"},
        ])
        assert [b.what for b in stored] == ["SAT prep"]

    def test_setting_a_plan_replaces_the_old_one(self, plan):
        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        plan.set_blocks([{"start": "3pm", "what": "Gym"}])
        assert [b.what for b in plan.blocks()] == ["Gym"]

    def test_saving_strips_legacy_planner_loop_state(self, plan, tmp_path):
        from argon.core import store
        from argon.productivity.plan import PLAN_DOC

        store.put_doc(PLAN_DOC, {
            "date": "2026-07-30",
            "blocks": [],
            "asked_count": 9,
            "declined": True,
            "seeded": True,
            "stated": False,
        })

        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])

        assert set(store.get_doc(PLAN_DOC)) == {"date", "blocks"}


class TestTheDayBoundary:
    def test_yesterdays_plan_is_not_todays(self, plan, tmp_path, monkeypatch):
        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        monkeypatch.setattr(plan_mod.clock, "today_key", lambda *a, **k: "2026-07-31")
        assert plan.blocks() == [] and plan.exists() is False

    def test_a_corrupt_plan_surfaces_instead_of_reading_as_no_plan(self, plan, tmp_path):
        """"No plan today" and "I cannot read your plan" are different sentences.

        The old code caught `JSONDecodeError` and returned an empty plan, so a
        torn write meant Argon calmly reported that he had nothing scheduled.
        """
        import pytest

        from argon.core import store
        from argon.productivity.plan import PLAN_DOC

        with store.txn() as conn:
            conn.execute(
                "INSERT INTO docs (key, value, version, updated_at) VALUES (?, ?, 1, 0) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (PLAN_DOC, "{not json"),
            )

        with pytest.raises(store.StoreCorrupt):
            plan.exists()


class TestTheMomentsItSpeaks:
    def test_a_block_starting_now(self, plan):
        plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        assert plan.starting_now(_at(14, 2)).what == "SAT prep"

    def test_not_forty_minutes_into_it(self, plan):
        """"Starting now" stops being true; a late nudge is just an interruption."""
        plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        assert plan.starting_now(_at(14, BLOCK_GRACE_MINUTES + 5)) is None

    def test_a_finished_block_is_left_alone(self, plan):
        stored = plan.set_blocks([{"start": "2pm", "end": "4pm", "what": "SAT prep"}])
        plan.mark(stored[0].id, "done")
        assert plan.starting_now(_at(14, 2)) is None


class TestScheduledWorkIsNotOutstanding:
    def test_a_planned_block_reads_as_a_commitment(self, plan):
        """Same rule as a calendar reminder: he already decided when."""
        from argon.tools.tasks import mark_scheduled, unscheduled

        plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        tasks = mark_scheduled([{"id": "a", "title": "SAT prep"}], plan.as_entries())

        assert tasks[0]["scheduled_for"] == "2:00 PM"
        assert unscheduled(tasks) == []

    def test_a_finished_block_stops_shielding_the_task(self, plan):
        from argon.tools.tasks import mark_scheduled, unscheduled

        stored = plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        plan.mark(stored[0].id, "done")
        tasks = mark_scheduled([{"id": "a", "title": "SAT prep"}], plan.as_entries())
        assert len(unscheduled(tasks)) == 1


class TestTheSummaryHeReadsBack:
    def test_it_says_where_he_is_in_the_day(self, plan):
        plan.set_blocks([
            {"start": "9am", "end": "10am", "what": "Gym"},
            {"start": "2pm", "end": "4pm", "what": "SAT prep"},
        ])
        lines = plan.summary(_at(15)).replace("\u2013", "-").splitlines()

        assert lines[0] == "- 9:00 AM-10:00 AM Gym (passed)"
        assert lines[1] == "- 2:00 PM-4:00 PM SAT prep (now)"

    def test_no_plan_says_so_plainly(self, plan):
        assert plan.summary(_at(12)) == "- no explicit plan"


class TestIdentitySurvivesAnEdit:
    """Ids used to be positions, handed out fresh on every write, after sorting.

    Two failures followed and both happened. Every edit had to resend the whole
    plan, so one block the model forgot to repeat vanished from his day with
    nothing saying so. And inserting an earlier block renumbered everything
    after it — a reminder already announced for `b1` then suppressed a
    *different* block, while the block that had been announced came back as `b2`
    and was announced a second time.
    """

    def test_inserting_an_earlier_block_does_not_disturb_an_announced_one(self, plan):
        [sat] = plan.set_blocks([{"start": "5pm", "what": "SAT prep"}])
        announced = sat.reminder_key()

        gym = plan.add_block("3pm", "Gym")

        after = {b.what: b for b in plan.blocks()}
        assert after["SAT prep"].id == sat.id, "identity survives the insert"
        assert after["SAT prep"].reminder_key() == announced, "so its reminder stays quiet"
        assert gym.reminder_key() != announced, "and the new block is its own thing"
        assert [b.what for b in plan.blocks()] == ["Gym", "SAT prep"]

    def test_a_delta_edit_does_not_require_resending_the_rest_of_the_day(self, plan):
        stored = plan.set_blocks([
            {"start": "9am", "what": "Gym"},
            {"start": "5pm", "what": "SAT prep"},
        ])
        plan.mark(stored[0].id, "done")

        plan.update_block(stored[1].id, start="6pm")

        blocks = plan.blocks()
        assert [b.what for b in blocks] == ["Gym", "SAT prep"], "nothing was dropped"
        assert blocks[0].status == "done", "and nothing was reset"
        assert blocks[1].start == "18:00"

    def test_moving_a_block_keeps_its_id_and_status_but_re_arms_its_reminder(self, plan):
        [sat] = plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        before = sat.reminder_key()

        moved = plan.update_block(sat.id, start="5pm")

        assert moved.id == sat.id
        assert moved.reminder_key() != before, "he moved it; the new start is worth marking"

    def test_a_move_preserves_a_status_he_already_set(self, plan):
        [block] = plan.set_blocks([{"start": "2pm", "what": "SAT prep"}])
        plan.mark(block.id, "skipped")

        moved = plan.update_block(block.id, start="7pm")

        assert moved.status == "skipped"

    def test_removing_one_block_leaves_the_others_untouched(self, plan):
        stored = plan.set_blocks([
            {"start": "9am", "what": "Gym"},
            {"start": "5pm", "what": "SAT prep"},
        ])

        gone = plan.remove_block(stored[0].id)

        assert gone.what == "Gym"
        assert [(b.id, b.what) for b in plan.blocks()] == [(stored[1].id, "SAT prep")]

    def test_an_unknown_id_changes_nothing(self, plan):
        plan.set_blocks([{"start": "5pm", "what": "SAT prep"}])

        assert plan.update_block("nosuchid", start="6pm") is None
        assert plan.remove_block("nosuchid") is None
        assert plan.mark("nosuchid", "done") is False
        assert [b.what for b in plan.blocks()] == ["SAT prep"]

    def test_two_blocks_at_the_same_minute_keep_separate_reminder_keys(self, plan):
        stored = plan.set_blocks([
            {"start": "5pm", "what": "SAT prep"},
            {"start": "5pm", "what": "Call Ravi"},
        ])

        assert stored[0].reminder_key() != stored[1].reminder_key()


class TestLegacyPlansStillLoad:
    def test_positional_ids_are_upgraded_without_losing_status(self, plan):
        from argon.core import store
        from argon.productivity.plan import PLAN_DOC

        store.put_doc(PLAN_DOC, {
            "date": "2026-07-30",
            "blocks": [
                {"id": "b0", "start": "09:00", "end": None, "what": "Gym",
                 "status": "done"},
                {"id": "b1", "start": "17:00", "end": None, "what": "SAT prep",
                 "status": "pending"},
            ],
        })

        blocks = plan.blocks()

        assert [b.what for b in blocks] == ["Gym", "SAT prep"]
        assert [b.status for b in blocks] == ["done", "pending"]
        assert not any(b.id.startswith("b") and b.id[1:].isdigit() for b in blocks)

    def test_the_upgrade_is_stable_so_reminders_do_not_all_re_fire(self, plan):
        from argon.core import store
        from argon.productivity.plan import PLAN_DOC

        document = {
            "date": "2026-07-30",
            "blocks": [{"id": "b0", "start": "17:00", "end": None,
                        "what": "SAT prep", "status": "pending"}],
        }
        store.put_doc(PLAN_DOC, document)
        first = plan.blocks()[0].reminder_key()

        store.put_doc(PLAN_DOC, document)
        assert plan.blocks()[0].reminder_key() == first

    def test_a_fresh_id_is_never_mistaken_for_a_positional_one(self):
        """One uuid4 prefix in ~270 is "b" followed by seven digits.

        `_POSITIONAL_ID` matched those, so the next read "upgraded" a perfectly
        good id into a different one: identity changed underneath a block
        nobody had touched, and its reminder fired again under the new key.
        Caught as a ~20% flake in this file before it could be one in his day.
        """
        from argon.productivity.plan import _POSITIONAL_ID, _fresh_id

        assert not any(_POSITIONAL_ID.match(_fresh_id()) for _ in range(20_000))
