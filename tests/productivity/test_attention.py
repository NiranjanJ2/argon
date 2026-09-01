"""What the phone reports about where his attention went.

iOS never says which app is in the foreground, so everything here is a threshold
that was crossed some time ago. The tests are mostly about *when* a report stops
meaning anything.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from argon import clock
from argon.productivity import attention


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    from argon.core import store

    store.reset_for_tests()
    yield
    store.reset_for_tests()


def test_a_crossing_at_four_says_nothing_at_nine():
    attention.record("opened", label="Social")

    assert len(attention.since_minutes(20)) == 1
    later = clock.now() + timedelta(hours=5)
    assert attention.since_minutes(20, now=later) == []


def test_spent_thresholds_are_cumulative_not_additive():
    """Thresholds fire in order as usage accumulates. Summing them counts the
    first fifteen minutes four times over."""
    for m in (15, 30, 60):
        attention.record("spent", label="Social", minutes=m)

    assert attention.minutes_spent_today() == 60


def test_describe_is_empty_when_the_phone_has_said_nothing():
    assert attention.describe() == ""


def test_describe_carries_both_facts():
    attention.record("spent", label="Social", minutes=45)
    attention.record("opened", label="Social")

    line = attention.describe()
    assert "45+ minutes" in line
    assert "just opened" in line and "Social" in line


def test_an_unreadable_timestamp_is_skipped_not_fatal():
    attention.record("opened", label="Social")
    from argon.core import store

    with store.edit_doc("attention", {"days": {}}) as doc:
        doc["days"][clock.today_key()][0]["at"] = "not-a-time"

    assert attention.since_minutes(60) == []   # skipped, no exception
