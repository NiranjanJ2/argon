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
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from loguru import logger

from argon import clock
from argon.core import store
from argon.productivity.plan import DayPlan
from argon.productivity.state import DailyState

# How often the gate is evaluated. Cheap — it is local state only, no LLM.
TICK_MINUTES = 10

#: After this hour, a same-day secretary brief is stale rather than useful.
DAILY_BRIEF_UNTIL_HOUR = 20

#: A concrete event or a block Niranjan placed on his plan is deduped by id,
#: not wording.  The daily cap and sixty-minute gap still bind it.
HIS_OWN_SCHEDULE = frozenset({"block_start", "upcoming"})

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
        Occasion("daily_brief", "the after-school secretary brief is due", 0),
        Occasion("block_start", "a block of his plan starts about now", 0),
    )
}


#: How long a failed attempt holds an occasion back before it may be retried.
#: Short on purpose: a provider hiccup should cost minutes, not the whole day.
ATTEMPT_BACKOFF_MINUTES = 15

#: The document holding today's check-in ledger. See argon/core/store.py.
LEDGER_DOC = "checkin_ledger"


class CheckInLedger:
    """What was attempted, what was delivered, and what was actually said today.

    Cooldowns lived in memory before, so every gateway restart reset them and a
    crash loop could produce a burst of messages. They then lived in a JSON file
    rewritten in place, which a concurrent turn could lose and a torn write could
    empty. They now live in one transactional document.

    The important distinction here is *attempted* versus *fired*. The old code
    recorded a once-per-day occasion as fired before generating anything, so a
    provider outage at 4 PM permanently consumed that day's brief — the failure
    looked exactly like a day with nothing to say. An attempt now only holds the
    occasion back for :data:`ATTEMPT_BACKOFF_MINUTES`; only an acknowledged
    delivery consumes it.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @staticmethod
    def _blank(today: str) -> dict[str, Any]:
        return {"date": today, "fired": {}, "said": [], "announced": [], "attempts": {}}

    def _load(self, today: str) -> dict[str, Any]:
        data = store.get_doc(LEDGER_DOC)
        if not isinstance(data, dict) or data.get("date") != today:
            return self._blank(today)
        for key, empty in (("fired", {}), ("said", []), ("announced", []), ("attempts", {})):
            data.setdefault(key, empty)
        return data

    @contextmanager
    def _edit(self) -> Any:
        """Read-modify-write today's ledger as one transaction."""
        today = clock.today_key()
        with store.edit_doc(LEDGER_DOC, self._blank(today)) as data:
            if data.get("date") != today:
                data.clear()
                data.update(self._blank(today))
            for key, empty in (("fired", {}), ("said", []), ("announced", []), ("attempts", {})):
                data.setdefault(key, empty)
            yield data

    def record_attempt(self, kind: str, now: datetime) -> None:
        """We are about to try. Holds the occasion back briefly if this fails."""
        with self._edit() as data:
            data["attempts"][kind] = now.isoformat()

    def record_fired(self, kind: str, now: datetime) -> None:
        """This occasion has been delivered. Starts its real cooldown."""
        with self._edit() as data:
            data["fired"][kind] = now.isoformat()

    def record_said(self, kind: str, text: str, now: datetime) -> None:
        with self._edit() as data:
            data["said"].append({"at": now.isoformat(), "occasion": kind, "text": text[:400]})

    def minutes_since_fired(self, kind: str, now: datetime) -> float | None:
        stamp = self._load(clock.today_key())["fired"].get(kind)
        if not stamp:
            return None
        return (now - datetime.fromisoformat(stamp)).total_seconds() / 60

    def minutes_since_attempt(self, kind: str, now: datetime) -> float | None:
        stamp = self._load(clock.today_key())["attempts"].get(kind)
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
        with self._edit() as data:
            if event_id not in data["announced"]:
                data["announced"].append(event_id)

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
        on_deliver: Callable[..., Awaitable[Any]] | None = None,
        enabled: bool = True,
        max_per_day: int = 3,
        min_gap_minutes: int = 60,
        quiet_start_hour: int = 23,
        quiet_end_hour: int = 7,
        unprompted_from_hour: int = 16,
    ) -> None:
        self.workspace = workspace
        # Explicit tz wins (tests); otherwise the process-wide clock.
        self.tz = ZoneInfo(timezone) if timezone else clock.tz()
        self.on_check_in = on_check_in
        self.on_deliver = on_deliver
        self.enabled = enabled
        self.max_per_day = max_per_day
        self.min_gap_minutes = min_gap_minutes
        self.quiet_start_hour = quiet_start_hour
        self.quiet_end_hour = quiet_end_hour
        self.unprompted_from_hour = unprompted_from_hour
        self._state = DailyState(workspace)
        self._plan = DayPlan(workspace)
        self.ledger = CheckInLedger(workspace)
        #: The event that caused an `upcoming` occasion, handed to build_prompt.
        self._pending: dict[str, Any] | None = None
        #: Same, for the plan-driven occasions.
        self._pending_block: Any = None
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
        """Has this occasion's own cooldown elapsed?

        A delivered occasion is bound by its real cooldown. An *attempt* that
        did not reach him only holds it back briefly: a provider outage at 4 PM
        used to consume the day's brief outright, and a day Argon failed to
        speak was indistinguishable from a day it had nothing to say.
        """
        elapsed = self.ledger.minutes_since_fired(kind, now)
        if elapsed is not None:
            cooldown = OCCASIONS[kind].cooldown_min
            return False if cooldown == 0 else elapsed >= cooldown
        attempted = self.ledger.minutes_since_attempt(kind, now)
        if attempted is not None and attempted < ATTEMPT_BACKOFF_MINUTES:
            return False
        return True

    def pending_task_count(self) -> int:
        """Open tasks with no time set aside yet. -1 when it cannot be determined.

        A task he has already scheduled is not outstanding — deciding when to do
        something is doing something about it. Counting the 3 PM math homework
        as "free time, with things outstanding" is what had Argon telling him at
        noon to start work it had itself put in his calendar for three hours
        later. Excluding them here means no occasion and no model call at all,
        rather than a prompt asking the model not to say the obvious thing.
        """
        try:
            from argon.commitments import load_board
            from argon.services import agenda
            from argon.tools.tasks import mark_scheduled, unscheduled

            board = load_board(self.workspace)
            if not board.source("tasks").ok:
                # Absence of evidence. A task list that did not answer is not a
                # task list with nothing in it, and guessing zero here is how a
                # brief reports a clear evening on a night with work due.
                return -1
            # A Classroom outage does not mute the brief — what Tasks returned
            # is still real material, and `_overdue_lines` states the outage.
            tasks = board.as_dicts()
        except Exception:  # noqa: BLE001 — offline must not become a guess
            return -1
        try:
            # A block of the plan is a commitment just like a calendar entry.
            entries = agenda.upcoming(self.workspace) + self._plan.as_entries()
            tasks = mark_scheduled(tasks, entries)
        except Exception:  # noqa: BLE001 — a calendar outage must not invent work
            pass
        return len(unscheduled(tasks))

    def _headline(self, occasion: Occasion) -> str:
        """The one line saying why this moment, not some other moment.

        Every occasion here is a time *he* chose, so the message can name it.
        That is the whole difference from the old timer: "you said SAT prep at
        2" lands where "you have tasks outstanding" reads as nagging.
        """
        if occasion.kind == "upcoming" and self._pending:
            from argon.services import agenda as _agenda

            return (
                "STARTING SOON: {}\nThis is why you woke up — say this, briefly.\n\n"
                .format(_agenda.describe(self._pending))
            )

        if occasion.kind == "daily_brief":
            # This is the brief: he is home from school and the evening is the
            # part of the day the whole tool exists for. Classroom and the
            # overdue list are fetched and stated here rather than left to a
            # tool call the model kept skipping.
            overdue = self._overdue_lines()
            return (
                "THIS IS THE AFTER-SCHOOL BRIEF — he is home and the evening is "
                "his. This is a one-way secretary brief, not coaching and not a "
                "planning conversation. No reply is needed.\n\n"
                "Due from Google Classroom:\n{}\n\n{}"
                "Report only verified exceptions and commitments he already has. "
                "Put overdue items or real conflicts first; otherwise use time or "
                "deadline order. Keep it to two or three short items. Do not rank "
                "work by difficulty, propose priorities, invent a plan, ask a "
                "question, call a mutation tool, or imply that he should respond.\n\n"
            ).format(
                self._schoolwork_lines(),
                "Past due and still open:\n{}\n\n".format(overdue) if overdue else "",
            )

        if occasion.kind == "block_start" and self._pending_block:
            return (
                "HIS PLAN SAYS: {} starts about now ({}). Say one line marking "
                "it — he chose this time, so you are reminding him of his own "
                "decision, not proposing one. If he confirms, start_task if it "
                "matches a task.\n\n"
                .format(self._pending_block.what, self._pending_block.start)
            )

        return ""

    def _schoolwork_lines(self) -> str:
        """Classroom assignments due soon, as prompt lines. Never raises.

        Reads the canonical board. This used to call ``agenda.schoolwork``,
        which swallows its own failure and returns an empty list — so the one
        branch here that said "Classroom unavailable" was unreachable, and a
        stale-token outage printed "nothing due from Classroom" into the 4 PM
        brief. That is an assertion Argon had no basis for, in the message it
        exists to send. It also means the brief no longer crawls Classroom
        twice through two caches with two different windows.
        """
        try:
            from argon.commitments import load_board

            board = load_board(self.workspace)
        except Exception:  # noqa: BLE001 — never let this break the brief
            return "- (Google Classroom unavailable)"

        health = board.source("classroom")
        if not health.ok:
            return f"- (Google Classroom unavailable: {health.error})"

        work = [c for c in board.commitments if c.origin == "classroom"]
        lines = [
            "- {}{} — due {}{}".format(
                c.title,
                f" ({c.subject})" if c.subject else "",
                c.official_due_when or c.official_due or "no date",
                f", he plans to do it {c.work_by_when}" if c.work_by_when else "",
            )
            for c in work[:6]
        ]
        if not lines:
            lines = ["- nothing due from Classroom"]
        # A partial read is not a clear plate; say which courses went unread.
        lines.extend(f"- ({warning})" for warning in health.warnings[:3])
        return "\n".join(lines)

    def _overdue_lines(self) -> str:
        """Commitments past due, with how far past. Never raises.

        Reads the reconciled board, not raw Google Tasks. Reading the raw list
        here is how an assignment he had already turned in — correctly hidden
        from the board — was still announced as overdue in the 4 PM brief.
        """
        try:
            from argon.commitments import load_board

            board = load_board(self.workspace)
        except Exception:  # noqa: BLE001 — offline must not blank the brief
            return ""
        late = [c for c in board.as_dicts() if (c.get("days_overdue") or 0) > 0]
        late.sort(key=lambda c: -(c.get("days_overdue") or 0))
        lines = [
            "- {} — {} days past due, still open".format(c["title"], c["days_overdue"])
            for c in late[:4]
        ]
        # An incomplete board must say so here too, or the brief silently
        # under-reports and reads as though everything is accounted for.
        lines.extend(board.health_lines())
        return "\n".join(lines)

    def _agenda_lines(self) -> str:
        """Today's remaining events and reminders as prompt lines. Never raises."""
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
        """Is there verified work or a commitment for the daily brief?

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
        if self._plan.exists() or self.pending_task_count() > 0:
            return True
        from argon.services import agenda

        try:
            if agenda.upcoming(self.workspace):
                return True
        except Exception:  # noqa: BLE001 — an outage is absence of evidence
            pass
        try:
            return bool(agenda.schoolwork(self.workspace))
        except Exception:  # noqa: BLE001 — never invent material on failure
            return False

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
        # All automatic messages share one budget. A planned block or imminent
        # event is useful, but it is still an unsolicited interruption.
        if self.ledger.spoken_count() >= self.max_per_day:
            return None
        quiet_for = self.ledger.minutes_since_said(now)
        if quiet_for < self.min_gap_minutes:
            return None

        # An event about to start outranks everything, the mid-flow guard
        # included: being deep in a task is exactly when you miss the thing you
        # have to leave for. Announced once per event, never re-announced.
        if (event := self._pending_event()) is not None:
            self._pending = event
            return OCCASIONS["upcoming"]

        if mode in ("working", "lock_in"):
            # Mid-flow, nothing else earns an interruption. There used to be a
            # `session` occasion here that fired every 45 minutes while he
            # worked; on the first day of school it went off six times and four
            # were suppressed as rewords of each other, because "you're on
            # APUSH, want to switch to Math?" is all there is to say and he did
            # not ask. Every moment worth interrupting for is already covered:
            # a block starting or an event about to begin. Being
            # mid-work is not an occasion.
            return None

        if mode == "done":
            return None

        if (block := self._plan.starting_now(now)) is not None and not self.ledger.announced(
            self._block_key(block)
        ):
            self._pending_block = block
            return OCCASIONS["block_start"]

        # The after-school brief is the only ambient check-in. A block he
        # planned can still start before four; this invitation cannot.
        if now.hour < self.unprompted_from_hour:
            return None

        if (
            now.hour < DAILY_BRIEF_UNTIL_HOUR
            and self._ready("daily_brief", now)
            and self.has_material()
        ):
            return OCCASIONS["daily_brief"]

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
        headline = self._headline(occasion)

        return (
            f"It's {self._now():%-I:%M %p} and {occasion.blurb}.\n\n"
            f"{headline}"
            f"His plan for today:\n{self._plan.summary(self._now())}\n\n"
            f"Still on his calendar today:\n{agenda_lines}\n\n"
            f"What Niranjan said or did today:\n{today_notes}\n\n"
            "First call get_status, and list_tasks if it would tell you anything.\n\n"
            f"Already sent today:\n{history}\n\n"
            "Now WRITE THE TEXT MESSAGE you would send Niranjan — one or two "
            "sentences, unprompted, in your own voice, the way a friend texts.\n\n"
            "Reply with the message itself and nothing else — no preamble, no "
            "explanation, no quotes around it.\n\n"
            "If a task is days past due and still open, flag once that the "
            "record may be stale. Do not turn an unsolicited brief into a "
            "request for him to reconcile your records.\n\n"
            "If a task shows scheduled_for, he has already decided when to do "
            "it. Mentioning it is fine — telling him to start it now is not. "
            "Never argue with a plan he has already made.\n\n"
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
        # An attempt, not a consumption. A silent turn still holds this occasion
        # back — otherwise it retries every tick and burns model calls — but a
        # failure costs ATTEMPT_BACKOFF_MINUTES rather than the whole day.
        self.ledger.record_attempt(occasion.kind, now)
        logger.info("Check-in: {}", occasion.kind)

        said = await self.on_check_in(self.build_prompt(occasion))
        text = (said or "").strip() if isinstance(said, str) else ""
        if is_silence(text):
            # The model genuinely had nothing to say. That is a real outcome for
            # a discretionary check-in, so it consumes the occasion.
            self.ledger.record_fired(occasion.kind, now)
            self._consume_pending(occasion)
            logger.debug("Check-in ({}): nothing to say", occasion.kind)
            return ""
        # The reword filter is for the after-school brief. Planned starts and
        # imminent events are already deduped by their concrete ids.
        if occasion.kind not in HIS_OWN_SCHEDULE and is_near_duplicate(
            text, self.ledger.said_today()
        ):
            self.ledger.record_fired(occasion.kind, now)
            self._consume_pending(occasion)
            logger.info("Check-in ({}) suppressed as a reword: {}", occasion.kind, text[:60])
            return ""

        # Only an acknowledged delivery counts. `notify` returns False when
        # there is no reachable channel or the send failed outright; recording
        # "said" regardless is how two days of check-ins were logged as spoken
        # while nothing left the machine.
        delivered, arrived = True, text
        if self.on_deliver is not None:
            key = self._delivery_key(occasion, now)
            result = await self.on_deliver(text, key=key) if key else await self.on_deliver(text)
            delivered = result is not False and getattr(result, "ok", True)
            # What he actually received. When this key was already delivered —
            # an earlier attempt whose acknowledgement arrived late — the text
            # generated just now never went out, and recording it would show
            # the model a message it never sent as its own history.
            arrived = getattr(result, "content", "") or text
        if not delivered:
            logger.warning(
                "Check-in ({}) was not delivered; leaving it available to retry", occasion.kind
            )
            return ""

        self.ledger.record_fired(occasion.kind, now)
        self._consume_pending(occasion)
        self.ledger.record_said(occasion.kind, arrived, now)
        logger.info("Check-in spoke ({}): {}", occasion.kind, arrived[:80])
        return arrived

    def _consume_pending(self, occasion: Occasion) -> None:
        """Mark the concrete thing this occasion was about as dealt with.

        Deferred until the outcome is known: announcing a 7 PM meeting before
        generating the message meant a provider error permanently swallowed it,
        and it was never mentioned at all.
        """
        if occasion.kind == "upcoming" and self._pending:
            self.ledger.record_announced(self._pending["id"])
        if occasion.kind == "block_start" and self._pending_block:
            self.ledger.record_announced(self._block_key(self._pending_block))

    @staticmethod
    def _block_key(block: Any) -> str:
        """Dedupe key for a planned block: stable identity plus its start time.

        Positional ids ("b0", "b1") were used here, so inserting an earlier
        block shifted every id: an already-announced key then suppressed a
        different block, and the moved block was announced a second time.
        """
        reminder_key = getattr(block, "reminder_key", None)
        if callable(reminder_key):
            return reminder_key()
        return "start:{}:{}".format(getattr(block, "id", ""), getattr(block, "start", ""))

    def _delivery_key(self, occasion: Occasion, now: datetime) -> str | None:
        """Idempotency key so a retry cannot deliver the same brief twice."""
        day = clock.today_key()
        if occasion.kind == "upcoming" and self._pending:
            return f"checkin:{day}:upcoming:{self._pending['id']}"
        if occasion.kind == "block_start" and self._pending_block:
            return f"checkin:{day}:{self._block_key(self._pending_block)}"
        return f"checkin:{day}:{occasion.kind}"
