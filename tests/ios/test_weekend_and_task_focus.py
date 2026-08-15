"""Metered weekend mode, and blocks that ride in on a started task.

The property worth protecting here is ownership: a block Niranjan set himself
must survive him ticking a task off. Task focus and manual focus write to the
same desired-mode file, so without a source tag the only thing distinguishing
them is who happened to write last.
"""

from datetime import datetime

from argon.ios import mode as ios_mode
from argon.tools.tasks import (
    TASK_FOCUS_WINDOW_MIN,
    engage_task_focus,
    release_task_focus,
    renew_task_focus,
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


class TestLockUntilDone:
    def test_the_block_ignores_the_estimate_entirely(self):
        # A block sized to a guess lifts exactly when the work is running long,
        # which is when the distraction is most tempting.
        engage_task_focus({"id": "t1", "title": "Essay", "time_estimate_min": 10})

        expiry = datetime.fromisoformat(ios_mode.get_mode()["expires_at"])
        minutes = (expiry - datetime.fromisoformat(ios_mode.get_mode()["since"])).seconds / 60
        assert minutes > 10
        assert round(minutes) == TASK_FOCUS_WINDOW_MIN

    def test_renewing_pushes_the_expiry_out(self):
        engage_task_focus({"id": "t1", "title": "Essay"})
        first = ios_mode.get_mode()["expires_at"]

        with _time_advanced_by_minutes(5):
            renew_task_focus()
            assert ios_mode.get_mode()["expires_at"] > first

    def test_renewing_does_not_bump_the_version(self):
        # A version bump makes the phone re-apply the whole block. At one renewal
        # every twenty seconds that is a re-lock three times a minute.
        engage_task_focus({"id": "t1", "title": "Essay"})
        version = ios_mode.get_mode()["version"]

        renew_task_focus()

        assert ios_mode.get_mode()["version"] == version

    def test_renewing_will_not_extend_a_block_he_set_himself(self):
        ios_mode.set_mode("lock_in", duration_min=30, source="argon")
        before = ios_mode.get_mode()["expires_at"]

        renew_task_focus()

        assert ios_mode.get_mode()["expires_at"] == before

    def test_nothing_is_renewed_once_the_block_is_off(self):
        assert ios_mode.renew(TASK_FOCUS_WINDOW_MIN, source="task") is None


def _time_advanced_by_minutes(minutes: int):
    """Move argon's clock forward for the duration of a block."""
    from contextlib import contextmanager
    from datetime import timedelta
    from unittest.mock import patch

    from argon import clock

    @contextmanager
    def _shifted():
        real_now = clock.now()
        with patch.object(clock, "now", lambda: real_now + timedelta(minutes=minutes)):
            yield

    return _shifted()
