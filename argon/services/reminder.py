"""Check-in service — the part of Argon that starts conversations.

The first version was a veto chain: a list of reasons to stay quiet, defaulting
to silence. Between "nothing before noon", "idle needs a home-arrival stamp"
and a prompt that opened with *"staying silent is the normal outcome"*, it could
go days without a word.

This version inverts it. Local state names an **occasion** — a reason it might
be worth reaching out — and only then is a model call spent. Each occasion
carries its own cooldown, so different reasons can land close together while the
same one never repeats. A ledger records what was actually said, which is what
makes talking more often safe: the model is told what it already said today and
told not to repeat it. Frequency without that is just nagging.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from loguru import logger

from argon import clock
from argon.productivity.state import DailyState

# How often the gate is evaluated. Cheap — it is local state only, no LLM.
TICK_MINUTES = 10

# Minimum minutes an active work session must run before it is worth remarking on.
SESSION_FLOOR_MINUTES = 25

# For `ambient`: how long since the last word before a no-agenda text is welcome.
AMBIENT_QUIET_MINUTES = 180

#: How the model declines to say anything.
SKIP_TOKEN = "SKIP"

#: Short answers that are the model replying *about* the decision instead of
#: writing a message. Delivering one of these would send Niranjan the word "No."
_NON_MESSAGES = {
    "skip", "no", "no.", "none", "nothing", "n/a", "pass", "silence",
    "no message", "nothing to say", "stay silent", "yes", "yes.", "ok", "okay",
}


def is_silence(text: str) -> bool:
    """Is this a refusal rather than a message worth sending?"""
    stripped = (text or "").strip().strip('"').strip()
    if not stripped:
        return True
    if stripped.upper().startswith(SKIP_TOKEN):
        return True
    return stripped.lower().rstrip(".!") in _NON_MESSAGES


@dataclass(frozen=True)
class Occasion:
    """A reason to consider reaching out."""

    kind: str
    blurb: str        # told to the model, so it knows why it woke up
    cooldown_min: int  # 0 means once per day


OCCASIONS: dict[str, Occasion] = {
    o.kind: o
    for o in (
        Occasion("upcoming", "something on his calendar starts shortly", 0),
        Occasion("morning", "the day is just starting", 0),
        Occasion("after_school", "school just let out", 0),
        Occasion("session", "a work session has been running a while", 45),
        Occasion("idle", "free time, with things outstanding", 120),
        Occasion("evening", "the day is winding down", 0),
        Occasion("ambient", "no particular reason — it has just been a while", 0),
    )
}


class CheckInLedger:
    """What fired and what was actually said, today. Survives a restart.

    Cooldowns lived in memory before, so every gateway restart reset them and a
    crash loop could produce a burst of messages.
    """

    def __init__(self, workspace: Path) -> None:
        self._path = workspace / "daily" / "checkins.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self, today: str) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data, dict) or data.get("date") != today:
            return {"date": today, "fired": {}, "said": [], "announced": []}
        data.setdefault("fired", {})
        data.setdefault("said", [])
        data.setdefault("announced", [])
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record_fired(self, kind: str, now: datetime) -> None:
        """A check-in ran. Starts the cooldown whether or not it spoke."""
        data = self._load(clock.today_key())
        data["fired"][kind] = now.isoformat()
        self._save(data)

    def record_said(self, kind: str, text: str, now: datetime) -> None:
        data = self._load(clock.today_key())
        data["said"].append({"at": now.isoformat(), "occasion": kind, "text": text[:400]})
        self._save(data)

    def minutes_since_fired(self, kind: str, now: datetime) -> float | None:
        stamp = self._load(clock.today_key())["fired"].get(kind)
        if not stamp:
            return None
        return (now - datetime.fromisoformat(stamp)).total_seconds() / 60

    def minutes_since_said(self, now: datetime) -> float:
        said = self._load(clock.today_key())["said"]
        if not said:
            return float("inf")
        return (now - datetime.fromisoformat(said[-1]["at"])).total_seconds() / 60

    def said_today(self) -> list[str]:
        return [item["text"] for item in self._load(clock.today_key())["said"]]

    def announced(self, event_id: str) -> bool:
        """Has this calendar event already been mentioned today?

        A cooldown on the occasion would be wrong in both directions: it would
        re-announce a 7 PM event at 7:30, and it would swallow a second event
        that happens to start soon after the first. The event id is the thing
        that must not repeat.
        """
        return event_id in self._load(clock.today_key()).get("announced", [])

    def record_announced(self, event_id: str) -> None:
        data = self._load(clock.today_key())
        announced = data.setdefault("announced", [])
        if event_id not in announced:
            announced.append(event_id)
        self._save(data)

    def spoken_count(self) -> int:
        return len(self._load(clock.today_key())["said"])


def _snooze_file(workspace: Path) -> Path:
    return workspace / "daily" / "snooze.json"


def snooze_until(workspace: Path) -> datetime | None:
    """When check-ins may resume, or None."""
    try:
        stamp = json.loads(_snooze_file(workspace).read_text(encoding="utf-8"))["until"]
        until = datetime.fromisoformat(stamp)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    return until if until > clock.now() else None


def snooze(workspace: Path, hours: float, reason: str = "") -> datetime:
    """Stop starting conversations for a while.

    Niranjan said "tomorrow is a rest day", Argon replied "Rest day noted" —
    and then nudged him five times the next day. It had written a note and
    never changed any state the check-in gate reads, because ``set_mode`` was
    never called. Telling Argon to back off has to land somewhere the gate
    actually looks.
    """
    until = clock.now() + timedelta(hours=max(0.1, float(hours)))
    path = _snooze_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"until": until.isoformat(), "reason": reason}, indent=2),
        encoding="utf-8",
    )
    logger.info("Check-ins snoozed until {} ({})", until, reason or "no reason given")
    return until


def clear_snooze(workspace: Path) -> None:
    _snooze_file(workspace).write_text("{}", encoding="utf-8")


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 3}


def is_near_duplicate(text: str, previous: list[str], threshold: float = 0.5) -> bool:
    """Is this just a rewording of something already sent?

    The prompt already lists everything said today and says not to repeat it.
    gpt-oss-20b ignored that and sent "Project due next week—let's lock in a
    session to start it", then "Ready to lock in a session for the project due
    next week?" two hours later. An instruction the model can ignore is not a
    guard, so this checks mechanically.
    """
    current = _words(text)
    if not current:
        return False
    for older in previous:
        other = _words(older)
        if not other:
            continue
        overlap = len(current & other) / len(current | other)
        if overlap >= threshold:
            return True
    return False


class ReminderService:
    """Decides when Argon has a reason to start a conversation."""

    def __init__(
        self,
        workspace: Path,
        timezone: str,
        on_check_in: Callable[[str], Awaitable[Any]],
        *,
        on_day_rollover: Callable[[], Awaitable[Any]] | None = None,
        enabled: bool = True,
        max_per_day: int = 8,
        min_gap_minutes: int = 25,
        quiet_start_hour: int = 23,
        quiet_end_hour: int = 7,
    ) -> None:
        self.workspace = workspace
        # Explicit tz wins (tests); otherwise the process-wide clock.
        self.tz = ZoneInfo(timezone) if timezone else clock.tz()
        self.on_check_in = on_check_in
        self.on_day_rollover = on_day_rollover
        self.enabled = enabled
        self.max_per_day = max_per_day
        self.min_gap_minutes = min_gap_minutes
        self.quiet_start_hour = quiet_start_hour
        self.quiet_end_hour = quiet_end_hour
        self._state = DailyState(workspace)
        self.ledger = CheckInLedger(workspace)
        #: The event that caused an `upcoming` occasion, handed to build_prompt.
        self._pending: dict[str, Any] | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    # -- policy ------------------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(self.tz)

    def _in_quiet_hours(self, now: datetime) -> bool:
        """Overnight window, which wraps past midnight."""
        start, end = self.quiet_start_hour, self.quiet_end_hour
        if start == end:
            return False
        if start < end:
            return start <= now.hour < end
        return now.hour >= start or now.hour < end

    def _ready(self, kind: str, now: datetime) -> bool:
        """Has this occasion's own cooldown elapsed?"""
        elapsed = self.ledger.minutes_since_fired(kind, now)
        if elapsed is None:
            return True
        cooldown = OCCASIONS[kind].cooldown_min
        return False if cooldown == 0 else elapsed >= cooldown

    def _is_school_day(self) -> bool:
        try:
            from argon.productivity.bell import ScheduleManager

            return ScheduleManager(self.workspace).is_school_day()
        except Exception:  # noqa: BLE001 — a bad schedule file must not mute Argon
            return False

    def pending_task_count(self) -> int:
        """How many real, open tasks exist. -1 when it cannot be determined."""
        try:
            from argon.google.tasks_store import GoogleTasksStore

            return len(GoogleTasksStore(self.workspace).get_all())
        except Exception:  # noqa: BLE001 — offline must not become a guess
            return -1

    def _agenda_lines(self) -> str:
        """Today's remaining events as prompt lines. Never raises."""
        from argon.services import agenda

        try:
            events = agenda.upcoming(self.workspace)
        except Exception:  # noqa: BLE001 — a calendar outage must not mute Argon
            return "- (calendar unavailable)"
        if not events:
            return "- nothing else scheduled"
        return "\n".join("- " + agenda.describe(e) for e in events[:6])

    def _pending_event(self) -> dict[str, Any] | None:
        """An event starting soon that has not been mentioned yet."""
        from argon.services import agenda

        event = agenda.starting_soon(self.workspace, ignore=self.ledger.announced)
        return event if event and event.get("id") else None

    def has_material(self) -> bool:
        """Is there anything real to talk about?

        This is the guard that was missing. With no tasks and no session, an
        "idle" nudge has no legitimate content — and a prompt that orders the
        model to write a message anyway leaves it one way to comply: invent one.
        That is exactly what happened. Given a biography mentioning a UCLA lab
        and nothing else to work with, it produced "How's the UCLA lab work
        going?" and a "project due next week" that never existed, five times in
        one day, on a day Niranjan had said was a rest day.

        No material, no wake-up. Silence is the correct output, and it costs
        nothing to be sure of it before spending a model call.
        """
        if self._state.get_work_session_duration_minutes():
            return True
        return self.pending_task_count() > 0

    def pick_occasion(self) -> Occasion | None:
        """The reason to reach out right now, or None. No model call involved."""
        now = self._now()
        data = self._state.get()
        mode = data.get("mode", "idle")

        if self._in_quiet_hours(now) or mode == "napping":
            return None
        if (until := snooze_until(self.workspace)) is not None:
            logger.debug("Check-ins snoozed until {}", until)
            return None
        if self.ledger.spoken_count() >= self.max_per_day:
            return None
        # A floor between messages, whatever the reason. Without it two
        # occasions coming due together read as a double-text.
        if self.ledger.minutes_since_said(now) < self.min_gap_minutes:
            return None

        # An event about to start outranks everything, the mid-flow guard
        # included: being deep in a task is exactly when you miss the thing you
        # have to leave for. Announced once per event, never re-announced.
        if (event := self._pending_event()) is not None:
            self._pending = event
            return OCCASIONS["upcoming"]

        if mode in ("working", "lock_in"):
            # Mid-flow, only the session occasion earns an interruption.
            minutes = self._state.get_work_session_duration_minutes() or 0
            if minutes >= SESSION_FLOOR_MINUTES and self._ready("session", now):
                return OCCASIONS["session"]
            return None

        if mode == "done":
            return None

        hour = now.hour + now.minute / 60
        if hour < 10 and self._ready("morning", now):
            return OCCASIONS["morning"]
        if 15 <= hour < 17.5 and self._is_school_day() and self._ready("after_school", now):
            return OCCASIONS["after_school"]
        if 20 <= hour < 22.5 and self._ready("evening", now):
            return OCCASIONS["evening"]

        # Everything below is a nudge about work, so it needs work to exist.
        if not self.has_material():
            return None
        if self._ready("idle", now):
            return OCCASIONS["idle"]
        if (
            self._ready("ambient", now)
            and self.ledger.minutes_since_said(now) >= AMBIENT_QUIET_MINUTES
        ):
            return OCCASIONS["ambient"]
        return None

    def build_prompt(self, occasion: Occasion) -> str:
        """What the model is woken with.

        Phrasing this as "decide whether to text him" gets the decision back as
        the answer — gpt-oss-20b replied a bare "No.", which would then have been
        delivered to Niranjan as the check-in. So the instruction is imperative,
        and refusal has one exact spelling the caller can filter on.
        """
        already = self.ledger.said_today()
        history = (
            "\n".join(f"- {text}" for text in already)
            if already
            else "- nothing yet today"
        )
        # What he actually said today, so a nudge can reference the real thing
        # instead of reaching into biography for something plausible.
        try:
            from argon.core.journal import Journal

            today_notes = Journal(self.workspace).read_day() or "(nothing recorded)"
        except Exception:  # noqa: BLE001 — never let memory break the check-in
            today_notes = "(nothing recorded)"
        # The calendar is stated outright rather than left to a tool call. It is
        # the one thing the model reliably failed to look up, and "you have X in
        # fifteen minutes" is the most useful thing Argon can say.
        agenda_lines = self._agenda_lines()
        headline = ""
        if occasion.kind == "upcoming" and self._pending:
            from argon.services import agenda as _agenda

            headline = (
                "STARTING SOON: {}\nThis is why you woke up — say this, briefly.\n\n"
                .format(_agenda.describe(self._pending))
            )

        return (
            f"It's {self._now():%-I:%M %p} and {occasion.blurb}.\n\n"
            f"{headline}"
            f"Still on his calendar today:\n{agenda_lines}\n\n"
            f"What Niranjan said or did today:\n{today_notes}\n\n"
            "First call get_status, and list_tasks if it would tell you anything.\n\n"
            f"Already sent today:\n{history}\n\n"
            "Now WRITE THE TEXT MESSAGE you would send Niranjan — one or two "
            "sentences, unprompted, in your own voice, the way a friend texts.\n\n"
            "Reply with the message itself and nothing else — no preamble, no "
            "explanation, no quotes around it.\n\n"
            "HARD RULE: only mention a task, deadline, project or piece of work "
            "that appeared in the tool output you just read, or in the calendar "
            "and journal blocks above — those are real, verified, and you should "
            "use them. Your background "
            "notes describe who Niranjan is, not what he owes — 'research at a "
            "UCLA lab' is a fact about his life, never an assignment. Do not "
            "invent work, and do not ask how something is going unless a tool "
            "just told you it exists. Inventing a deadline is much worse than "
            "saying nothing: he cannot tell the difference from a real one, and "
            "it costs him his trust in everything else you say.\n\n"
            f"Don't repeat anything listed above, even reworded. If the tools "
            f"showed no tasks and nothing is going on, reply with exactly "
            f"{SKIP_TOKEN} and nothing else — that is the right answer far more "
            'often than filler like "just checking in!".'
        )

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Check-ins disabled")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(
            "Check-ins started (evaluating every {}m, max {}/day)",
            TICK_MINUTES, self.max_per_day,
        )

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(TICK_MINUTES * 60)
                if not self._running:
                    break
                # Yesterday's journal gets folded into long-term memory on the
                # first tick of a new day; it rides this loop rather than
                # spawning a second one for a once-daily job.
                if self.on_day_rollover is not None:
                    try:
                        await self.on_day_rollover()
                    except Exception:
                        logger.exception("Day rollover failed")
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Check-in failed")

    async def tick(self) -> str:
        """Evaluate once; returns whatever was said (empty string if nothing)."""
        occasion = self.pick_occasion()
        if occasion is None:
            logger.debug("Check-in: no occasion")
            return ""

        now = self._now()
        # Record the attempt before running: a silent turn should still start
        # this occasion's cooldown, or it retries every tick and burns calls.
        self.ledger.record_fired(occasion.kind, now)
        # Same reasoning per event: if the model declines to mention the 7 PM
        # meeting, it must not be re-offered every ten minutes until 7.
        if occasion.kind == "upcoming" and self._pending:
            self.ledger.record_announced(self._pending["id"])
        logger.info("Check-in: {}", occasion.kind)

        said = await self.on_check_in(self.build_prompt(occasion))
        text = (said or "").strip() if isinstance(said, str) else ""
        if is_silence(text):
            logger.debug("Check-in ({}): nothing to say", occasion.kind)
            return ""
        if is_near_duplicate(text, self.ledger.said_today()):
            logger.info("Check-in ({}) suppressed as a reword: {}", occasion.kind, text[:60])
            return ""
        if text:
            self.ledger.record_said(occasion.kind, text, now)
            logger.info("Check-in spoke ({}): {}", occasion.kind, text[:80])
        return text
