"""Adding a cron job from a thread that is not the loop's.

The HTTP API runs Flask in a daemon thread beside the agent's asyncio loop, so
every scheduling call from a request arrives off-loop. It used to reach
asyncio.create_task with no running loop in that thread and raise, which
surfaced as a 500 on the planner: the start time was saved, the day was never
marked planned, and the wizard reopened on every launch.
"""

import asyncio
import threading

import pytest

from argon.paths import get_cron_store
from argon.services.cron import CronSchedule, CronService


@pytest.fixture
def cron(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    return CronService(get_cron_store())


def _in_one_hour_ms() -> int:
    import time

    return int((time.time() + 3600) * 1000)


class TestArmingFromAnotherThread:
    def test_adding_a_job_while_running_does_not_raise(self, cron):
        # Running, but this thread is not its loop — exactly the API's position.
        cron._running = True

        job = cron.add_job(
            name="planner:block",
            schedule=CronSchedule(kind="at", at_ms=_in_one_hour_ms()),
            message="18:00",
            kind="system_event",
            delete_after_run=True,
        )

        assert job.id
        assert [j.name for j in cron.list_jobs()] == ["planner:block"]

    def test_it_survives_a_live_loop_on_another_thread(self, cron):
        """The real shape: a loop running elsewhere, the add coming from here."""
        started = threading.Event()
        loop_holder: dict = {}

        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_holder["loop"] = loop
            loop.call_soon(started.set)
            loop.run_forever()

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        started.wait(timeout=5)

        cron._running = True
        cron._loop = loop_holder["loop"]
        try:
            cron.add_job(
                name="planner:notify",
                schedule=CronSchedule(kind="at", at_ms=_in_one_hour_ms()),
                message="18:00",
                kind="system_event",
                delete_after_run=True,
            )
            assert [j.name for j in cron.list_jobs()] == ["planner:notify"]
        finally:
            loop_holder["loop"].call_soon_threadsafe(loop_holder["loop"].stop)

    def test_removing_a_job_off_loop_is_also_safe(self, cron):
        cron._running = True
        job = cron.add_job(
            name="planner:block",
            schedule=CronSchedule(kind="at", at_ms=_in_one_hour_ms()),
            message="18:00",
            kind="system_event",
            delete_after_run=True,
        )

        assert cron.remove_job(job.id) is True
        assert cron.list_jobs() == []
