"""What happens to "remind me at 5" when Argon is not running at 5.

The old answer was: the job is deleted at startup and nothing is said. A
one-shot whose time had passed was erased during reconciliation, so a restart at
4:58 turned an explicit promise into no reminder, no error, and no record that
anything had ever been scheduled. The first version of this file asserted that
deletion as though it were the specification.

The promise now takes one of two honest paths. Recent enough to still be useful:
it fires, late, and says so. Too old: it stays as a visibly missed job, because
"I did not manage to tell you" is a true thing a secretary can say and silence
is not.
"""

from __future__ import annotations

import pytest

from argon.services import cron as cron_mod
from argon.services.cron import CronSchedule, CronService

MINUTE = 60_000
HOUR = 60 * MINUTE


@pytest.mark.asyncio
async def test_a_reminder_missed_by_minutes_is_delivered_late_not_deleted(
    tmp_path, monkeypatch
):
    """The restart case. Downtime must not consume an explicit reminder."""
    now = 1_000 * HOUR
    monkeypatch.setattr(cron_mod, "_now_ms", lambda: now)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        "syllabus reminder",
        CronSchedule(kind="at", at_ms=now + HOUR),
        "Sign the AP English syllabus agreement",
        deliver=True,
        channel="discord",
        to="chat-1",
        delete_after_run=True,
        kind="reminder",
    )

    ran: list[str] = []

    async def on_job(j):
        ran.append(j.id)
        return cron_mod.JobResult(status="ok", delivered=True)

    service.on_job = on_job

    # Argon was down for ninety minutes, straddling the reminder.
    now = now + HOUR + 30 * MINUTE
    await service.start()

    # Still live and due immediately, rather than deleted.
    recovered = service.get_job(job.id)
    assert recovered is not None
    assert recovered.state.next_run_at_ms == now

    await service._on_timer()
    assert ran == [job.id]
    service.stop()


@pytest.mark.asyncio
async def test_a_reminder_missed_by_hours_stays_visible_as_missed(tmp_path, monkeypatch):
    """Too late to be a reminder, too important to erase."""
    now = 1_000 * HOUR
    monkeypatch.setattr(cron_mod, "_now_ms", lambda: now)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        "old reminder",
        CronSchedule(kind="at", at_ms=now + HOUR),
        "remind me",
        delete_after_run=True,
    )

    now = now + HOUR + 6 * HOUR   # well past the grace window
    await service.start()

    missed = service.get_job(job.id)
    assert missed is not None, "a missed reminder must not vanish"
    assert missed.enabled is False
    assert missed.state.last_status == "missed"
    assert missed.state.next_run_at_ms is None
    assert [j.id for j in service.unkept()] == [job.id]
    assert service.status()["unkept"][0]["status"] == "missed"
    service.stop()


@pytest.mark.asyncio
async def test_a_one_shot_that_failed_to_deliver_is_not_deleted(tmp_path, monkeypatch):
    """`delete_after_run` used to mean "delete whatever happened".

    A channel outage therefore erased the evidence that Argon had promised
    anything, and the run history went with it.
    """
    now = 1_000 * HOUR
    monkeypatch.setattr(cron_mod, "_now_ms", lambda: now)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        "reminder",
        CronSchedule(kind="at", at_ms=now + MINUTE),
        "start math homework",
        deliver=True, channel="discord", to="chat-1",
        delete_after_run=True, kind="reminder",
    )

    async def on_job(_j):
        return cron_mod.JobResult(status="error", error="discord refused", delivered=False)

    service.on_job = on_job

    now = now + 2 * MINUTE
    await service.start()
    await service._on_timer()

    failed = service.get_job(job.id)
    assert failed is not None, "a reminder that never arrived must stay visible"
    assert failed.state.last_status == "error"
    assert failed.state.last_error == "discord refused"
    assert service.unkept() and service.unkept()[0].id == job.id
    service.stop()


@pytest.mark.asyncio
async def test_a_delivered_one_shot_is_cleaned_up(tmp_path, monkeypatch):
    now = 1_000 * HOUR
    monkeypatch.setattr(cron_mod, "_now_ms", lambda: now)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        "reminder",
        CronSchedule(kind="at", at_ms=now + MINUTE),
        "start math homework",
        deliver=True, channel="discord", to="chat-1",
        delete_after_run=True, kind="reminder",
    )

    async def on_job(_j):
        return cron_mod.JobResult(status="ok", delivered=True)

    service.on_job = on_job

    now = now + 2 * MINUTE
    await service.start()
    await service._on_timer()

    assert service.get_job(job.id) is None
    service.stop()


@pytest.mark.asyncio
async def test_a_job_whose_model_said_nothing_is_not_recorded_as_successful(
    tmp_path, monkeypatch
):
    """`ok` used to mean "the callback returned", not "anything happened"."""
    now = 1_000 * HOUR
    monkeypatch.setattr(cron_mod, "_now_ms", lambda: now)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job(
        "check the build", CronSchedule(kind="every", every_ms=HOUR), "check the build"
    )

    async def on_job(_j):
        return cron_mod.JobResult(status="skipped", error="no output from the model")

    service.on_job = on_job
    await service._execute_job(service.get_job(job.id))

    assert service.get_job(job.id).state.last_status == "skipped"


def test_a_past_one_shot_is_rejected_before_it_can_become_an_inert_live_job(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cron_mod, "_now_ms", lambda: 2_000)
    service = CronService(tmp_path / "jobs.json")

    with pytest.raises(ValueError, match="future"):
        service.add_job("too late", CronSchedule(kind="at", at_ms=1_500), "remind me")

    assert service.list_jobs(include_disabled=True) == []


def test_an_expired_disabled_one_shot_cannot_be_reenabled_inert(tmp_path, monkeypatch):
    monkeypatch.setattr(cron_mod, "_now_ms", lambda: 1_000)
    service = CronService(tmp_path / "jobs.json")
    job = service.add_job("later", CronSchedule(kind="at", at_ms=1_500), "remind me")
    service.enable_job(job.id, enabled=False)

    monkeypatch.setattr(cron_mod, "_now_ms", lambda: 2_000 + 6 * HOUR)
    result = service.enable_job(job.id, enabled=True)
    assert result is not None
    assert result.enabled is False
    assert result.state.next_run_at_ms is None
    assert result.state.last_status == "missed"
