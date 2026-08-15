"""Cron scheduling: job types + service."""

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal

from loguru import logger


@dataclass
class CronSchedule:
    """Schedule definition for a cron job."""
    kind: Literal["at", "every", "cron"]
    # For "at": timestamp in ms
    at_ms: int | None = None
    # For "every": interval in ms
    every_ms: int | None = None
    # For "cron": cron expression (e.g. "0 9 * * *")
    expr: str | None = None
    # Timezone for cron expressions
    tz: str | None = None


@dataclass
class CronPayload:
    """What to do when the job runs.

    ``reminder`` is text Niranjan asked for, delivered verbatim. ``agent_turn``
    is an instruction that needs a model turn to carry out. The distinction is
    the difference between "I'll remind you at 5" and "check the build at 5",
    and it decides whether a model is allowed anywhere near the outcome.
    """
    kind: Literal["system_event", "agent_turn", "reminder"] = "agent_turn"
    message: str = ""
    # Deliver response to channel
    deliver: bool = False
    channel: str | None = None  # e.g. "whatsapp"
    to: str | None = None  # e.g. phone number


#: ``missed`` means the moment passed while Argon was down or unreachable and is
#: now too old to deliver. It is a real outcome and must stay visible; the old
#: code deleted such jobs at startup, so the promise disappeared without trace.
JobStatus = Literal["ok", "error", "skipped", "missed"]


@dataclass
class JobResult:
    """What actually happened when a job ran.

    A job used to be marked ``ok`` because its callback returned without
    raising — regardless of whether the model said anything, an evaluator
    silenced it, or the channel refused the send. Success now has to be claimed
    explicitly by whoever knows.
    """
    status: JobStatus = "ok"
    error: str | None = None
    delivered: bool = False


@dataclass
class CronRunRecord:
    """A single execution record for a cron job."""
    run_at_ms: int
    status: JobStatus
    duration_ms: int = 0
    error: str | None = None


@dataclass
class CronJobState:
    """Runtime state of a job."""
    next_run_at_ms: int | None = None
    last_run_at_ms: int | None = None
    last_status: JobStatus | None = None
    last_error: str | None = None
    run_history: list[CronRunRecord] = field(default_factory=list)


@dataclass
class CronJob:
    """A scheduled job."""
    id: str
    name: str
    enabled: bool = True
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    payload: CronPayload = field(default_factory=CronPayload)
    state: CronJobState = field(default_factory=CronJobState)
    created_at_ms: int = 0
    updated_at_ms: int = 0
    delete_after_run: bool = False


@dataclass
class CronStore:
    """Persistent store for cron jobs."""
    version: int = 1
    jobs: list[CronJob] = field(default_factory=list)






#: How late a one-shot may be and still be worth firing after a restart. Matches
#: the outbox's grace window — the two are the same promise seen from two sides.
RESTART_GRACE_MS = 2 * 60 * 60 * 1000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """Compute next run time in ms."""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter
            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """Validate schedule fields that would otherwise create non-runnable jobs."""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


class CronService:
    """Service for managing and executing scheduled jobs."""

    _MAX_RUN_HISTORY = 20

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, "JobResult | str | None"]] | None = None,
    ):
        self.store_path = store_path
        self.on_job = on_job
        self._store: CronStore | None = None
        self._last_mtime: float = 0.0
        self._timer_task: asyncio.Task | None = None
        self._running = False

    def _load_store(self) -> CronStore:
        """Load jobs from disk. Reloads automatically if file was modified externally."""
        if self._store and self.store_path.exists():
            mtime = self.store_path.stat().st_mtime
            if mtime != self._last_mtime:
                logger.info("Cron: jobs.json modified externally, reloading")
                self._store = None
        if self._store:
            return self._store

        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                jobs = []
                for j in data.get("jobs", []):
                    jobs.append(CronJob(
                        id=j["id"],
                        name=j["name"],
                        enabled=j.get("enabled", True),
                        schedule=CronSchedule(
                            kind=j["schedule"]["kind"],
                            at_ms=j["schedule"].get("atMs"),
                            every_ms=j["schedule"].get("everyMs"),
                            expr=j["schedule"].get("expr"),
                            tz=j["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            kind=j["payload"].get("kind", "agent_turn"),
                            message=j["payload"].get("message", ""),
                            deliver=j["payload"].get("deliver", False),
                            channel=j["payload"].get("channel"),
                            to=j["payload"].get("to"),
                        ),
                        state=CronJobState(
                            next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                            last_status=j.get("state", {}).get("lastStatus"),
                            last_error=j.get("state", {}).get("lastError"),
                            run_history=[
                                CronRunRecord(
                                    run_at_ms=r["runAtMs"],
                                    status=r["status"],
                                    duration_ms=r.get("durationMs", 0),
                                    error=r.get("error"),
                                )
                                for r in j.get("state", {}).get("runHistory", [])
                            ],
                        ),
                        created_at_ms=j.get("createdAtMs", 0),
                        updated_at_ms=j.get("updatedAtMs", 0),
                        delete_after_run=j.get("deleteAfterRun", False),
                    ))
                self._store = CronStore(jobs=jobs)
            except Exception as e:
                # Do NOT quietly start from an empty job list. `start()` saves
                # immediately afterwards, so swallowing this used to overwrite
                # every scheduled reminder with `{"jobs": []}` — the promises
                # gone, one warning line, no copy kept.
                backup = self.store_path.with_suffix(
                    ".corrupt-{}.json".format(int(time.time()))
                )
                try:
                    self.store_path.rename(backup)
                    logger.error(
                        "Cron: {} is unreadable ({}). Kept a copy at {} — scheduled "
                        "jobs could not be restored and must be re-created.",
                        self.store_path.name, e, backup.name,
                    )
                except OSError as move_error:
                    logger.error(
                        "Cron: {} is unreadable ({}) and could not be set aside ({}). "
                        "Refusing to overwrite it.",
                        self.store_path.name, e, move_error,
                    )
                self._store = CronStore()
        else:
            self._store = CronStore()

        return self._store

    def _save_store(self) -> None:
        """Save jobs to disk."""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "runHistory": [
                            {
                                "runAtMs": r.run_at_ms,
                                "status": r.status,
                                "durationMs": r.duration_ms,
                                "error": r.error,
                            }
                            for r in j.state.run_history
                        ],
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ]
        }

        # Atomic replace: a torn write here is what makes the file unreadable in
        # the first place, and this file holds the reminders.
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        tmp = self.store_path.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.store_path)
        self._last_mtime = self.store_path.stat().st_mtime

    async def start(self) -> None:
        """Start the cron service."""
        self._running = True
        self._load_store()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        logger.info("Cron service started with {} jobs", len(self._store.jobs if self._store else []))

    def stop(self) -> None:
        """Stop the cron service."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        """Recompute next run times, recovering one-shots that came due while down.

        This used to *delete* a one-shot whose time had passed — so "remind me at
        5", plus a restart at 4:58, equalled no reminder and no record that one
        had ever been promised. A missed moment now takes one of two honest
        paths: recent enough to still be useful, so it fires immediately and
        says how late it is; or too old, in which case the job is kept, disabled
        and marked ``missed`` where it can be seen.
        """
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if not job.enabled:
                continue
            job.state.next_run_at_ms = _compute_next_run(job.schedule, now)
            if job.schedule.kind != "at" or job.state.next_run_at_ms is not None:
                continue
            late_by_ms = now - (job.schedule.at_ms or 0)
            if job.schedule.at_ms and late_by_ms <= RESTART_GRACE_MS:
                logger.warning(
                    "Cron: '{}' ({}) came due {:.0f}m ago while Argon was down — running it now",
                    job.name, job.id, late_by_ms / 60000,
                )
                job.state.next_run_at_ms = now
                continue
            logger.error(
                "Cron: '{}' ({}) was missed — due {:.0f}m ago, too late to deliver",
                job.name, job.id, late_by_ms / 60000,
            )
            job.enabled = False
            job.state.last_status = "missed"
            job.state.last_error = "Argon was not running when this was due"
            job.updated_at_ms = now

    def _get_next_wake_ms(self) -> int | None:
        """Get the earliest next run time across all jobs."""
        if not self._store:
            return None
        times = [j.state.next_run_at_ms for j in self._store.jobs
                 if j.enabled and j.state.next_run_at_ms]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """Schedule the next timer tick."""
        if self._timer_task:
            self._timer_task.cancel()

        next_wake = self._get_next_wake_ms()
        if not next_wake or not self._running:
            return

        delay_ms = max(0, next_wake - _now_ms())
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """Handle timer tick - run due jobs."""
        self._load_store()
        if not self._store:
            return

        now = _now_ms()
        due_jobs = [
            j for j in self._store.jobs
            if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
        ]

        for job in due_jobs:
            await self._execute_job(job)

        self._save_store()
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        """Execute a single job and record what really happened."""
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        try:
            result = await self.on_job(job) if self.on_job else JobResult()
            if not isinstance(result, JobResult):
                # A callback that only returns text cannot claim delivery.
                result = JobResult(status="ok" if result else "skipped")
            job.state.last_status = result.status
            job.state.last_error = result.error
            logger.info("Cron: job '{}' finished: {}", job.name, result.status)

        except Exception as e:
            result = JobResult(status="error", error=str(e))
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.error("Cron: job '{}' failed: {}", job.name, e)

        end_ms = _now_ms()
        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = end_ms

        job.state.run_history.append(CronRunRecord(
            run_at_ms=start_ms,
            status=job.state.last_status,
            duration_ms=end_ms - start_ms,
            error=job.state.last_error,
        ))
        job.state.run_history = job.state.run_history[-self._MAX_RUN_HISTORY:]

        # Handle one-shot jobs
        if job.schedule.kind == "at":
            # Only a job that actually did its work is forgotten. One that
            # failed to reach him stays, disabled and visibly failed, because
            # the alternative is deleting the evidence of a broken promise.
            if job.delete_after_run and job.state.last_status == "ok":
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # Compute next run
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """List all jobs."""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
        kind: Literal["system_event", "agent_turn", "reminder"] = "agent_turn",
    ) -> CronJob:
        """Add a new job."""
        store = self._load_store()
        _validate_schedule_for_add(schedule)
        now = _now_ms()
        if schedule.kind == "at" and (not schedule.at_ms or schedule.at_ms <= now):
            raise ValueError("one-shot schedules must be in the future")

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind=kind,
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )

        store.jobs.append(job)
        self._save_store()
        self._arm_timer()

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID."""
        store = self._load_store()
        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            self._save_store()
            self._arm_timer()
            logger.info("Cron: removed job {}", job_id)

        return removed

    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """Enable or disable a job."""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                now = _now_ms()
                next_run = _compute_next_run(job.schedule, now) if enabled else None
                if enabled and job.schedule.kind == "at" and next_run is None:
                    # Re-enabling something whose moment has passed does not
                    # bring it back. Say so, and keep the record — deleting it
                    # here was the same erasure as the startup path.
                    job.enabled = False
                    job.state.next_run_at_ms = None
                    job.state.last_status = "missed"
                    job.state.last_error = "the scheduled time has already passed"
                    job.updated_at_ms = now
                    self._save_store()
                    self._arm_timer()
                    return job
                job.enabled = enabled
                job.updated_at_ms = now
                job.state.next_run_at_ms = next_run
                self._save_store()
                self._arm_timer()
                return job
        return None

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """Manually run a job."""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                if not force and not job.enabled:
                    return False
                await self._execute_job(job)
                self._save_store()
                self._arm_timer()
                return True
        return False

    def get_job(self, job_id: str) -> CronJob | None:
        """Get a job by ID."""
        store = self._load_store()
        return next((j for j in store.jobs if j.id == job_id), None)

    def unkept(self) -> list[CronJob]:
        """Jobs that were missed or failed and never delivered. Kept visible."""
        store = self._load_store()
        return [j for j in store.jobs if j.state.last_status in ("missed", "error")]

    def status(self) -> dict:
        """Get service status."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
            "unkept": [
                {"id": j.id, "name": j.name, "status": j.state.last_status,
                 "error": j.state.last_error}
                for j in self.unkept()
            ],
        }
