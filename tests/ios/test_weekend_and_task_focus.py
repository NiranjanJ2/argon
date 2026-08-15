"""Metered weekend mode, and blocks that ride in on a started task.

The property worth protecting here is ownership: a block Niranjan set himself
must survive him ticking a task off. Task focus and manual focus write to the
same desired-mode file, so without a source tag the only thing distinguishing
them is who happened to write last.
"""

from argon.ios import mode as ios_mode
from argon.tools.tasks import (
    AUTO_FOCUS_DEFAULT_MIN,
    auto_focus_minutes,
    engage_task_focus,
    release_task_focus,
)


class TestWeekendAllowance:
    def test_weekend_meters_fifteen_minutes_an_hour_by_default(self):
        desired = ios_mode.set_mode("weekend")

        assert desired["allowance"] == {"minutes": 15, "per_hours": 1}

    def test_ordinary_modes_stay_hard_blocks(self):
        assert ios_mode.set_mode("lock_in", duration_min=30)["allowance"] is None

    def test_off_carries_no_allowance(self):
        assert ios_mode.set_mode("off")["allowance"] is None

    def test_an_unsupported_window_rounds_to_one_the_phone_enforces(self):
        # Screen Time only resets on [1, 6, 12, 24]. Advertising "every 3 hours"
        # would leave the server claiming an allowance the phone never applied.
        desired = ios_mode.set_mode("weekend", allowance_per_hours=3)

        assert desired["allowance"]["per_hours"] in ios_mode.ALLOWANCE_WINDOWS_HOURS

    def test_minutes_are_clamped_to_what_the_app_accepts(self):
        assert ios_mode.set_mode("weekend", allowance_minutes=999)["allowance"]["minutes"] == 60
        assert ios_mode.set_mode("weekend", allowance_minutes=1)["allowance"]["minutes"] == 5


class TestTaskFocusOwnership:
    def test_finishing_the_task_clears_the_block_it_raised(self):
        engage_task_focus({"id": "t1", "title": "APUSH reading"})
        assert ios_mode.get_mode()["mode"] == "lock_in"

        release_task_focus("t1")

        assert ios_mode.get_mode()["mode"] == "off"

    def test_finishing_a_task_leaves_his_own_block_alone(self):
        ios_mode.set_mode("lock_in", duration_min=90, source="argon")

        release_task_focus("t1")

        assert ios_mode.get_mode()["mode"] == "lock_in"

    def test_finishing_a_task_does_not_end_a_weekend_allowance(self):
        ios_mode.set_mode("weekend", source="argon")

        release_task_focus("t1")

        assert ios_mode.get_mode()["mode"] == "weekend"

    def test_an_emergency_override_refuses_the_block_without_failing_the_start(self):
        ios_mode.engage_override(minutes=30)

        # Returning None rather than raising is the point: the task still starts.
        assert engage_task_focus({"id": "t1", "title": "Essay"}) is None
        assert ios_mode.get_mode()["mode"] == "off"


class TestFocusDuration:
    def test_a_task_estimate_sizes_the_block(self):
        assert auto_focus_minutes({"time_estimate_min": 25}) == 25

    def test_no_estimate_falls_back_to_the_default(self):
        assert auto_focus_minutes({}) == AUTO_FOCUS_DEFAULT_MIN

    def test_a_huge_estimate_does_not_shield_the_phone_all_night(self):
        assert auto_focus_minutes({"time_estimate_min": 10_000}) == 180

    def test_an_unparseable_estimate_does_not_crash_the_start(self):
        assert auto_focus_minutes({"time_estimate_min": "soon"}) == AUTO_FOCUS_DEFAULT_MIN
