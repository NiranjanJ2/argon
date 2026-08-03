"""A task cannot be twenty hours in progress.

``started_at`` lives in Google Tasks metadata and has no day boundary, so "SAT
prep", started at 1:42am and never completed, still read as work in flight the
next evening. Argon kept nudging him to start something it believed he had been
doing all day.
"""

from __future__ import annotations

from datetime import timedelta

from argon.clock import now
from argon.tools.tasks import STALE_START_HOURS, annotate_start


def _started(hours_ago: float) -> dict:
    return {"title": "SAT prep", "started_at": (now() - timedelta(hours=hours_ago)).isoformat()}


def test_a_task_never_started_is_untouched():
    task = {"title": "Chem pset", "started_at": None}
    assert annotate_start(task) == task


def test_a_recent_start_reports_elapsed_time_without_alarm():
    out = annotate_start(_started(1.5))
    assert out["running_hours"] == 1.5
    assert "stale_start" not in out


def test_an_overnight_start_is_called_out():
    out = annotate_start(_started(20))
    assert out["running_hours"] == 20.0
    assert "stale_start" in out
    # It must tell the model what to DO, not merely that a number is large.
    assert "Ask whether it is done" in out["stale_start"]


def test_the_threshold_is_the_boundary():
    assert "stale_start" not in annotate_start(_started(STALE_START_HOURS - 0.5))
    assert "stale_start" in annotate_start(_started(STALE_START_HOURS + 0.5))


def test_an_unparseable_timestamp_is_ignored_rather_than_raising():
    task = {"title": "x", "started_at": "not a timestamp"}
    assert annotate_start(task) == task


def test_the_original_task_is_not_mutated():
    """list_tasks annotates a store result; the store's dict must stay clean."""
    task = _started(20)
    annotate_start(task)
    assert "running_hours" not in task
