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
from argon.productivity.plan import DayPlan
from argon.productivity.state import DailyState

# How often the gate is evaluated. Cheap — it is local state only, no LLM.
TICK_MINUTES = 10

# Minimum minutes an active work session must run before it is worth remarking on.
SESSION_FLOOR_MINUTES = 25

#: How long between asking for a plan and asking again. He asked to be pestered
#: until he names something; this is what "pestered" is allowed to mean.
PLAN_ASK_COOLDOWN_MIN = 100

#: Don't ask for a plan before this hour. Waking someone to ask about their day
#: is not structure, it is an alarm clock.
PLAN_ASK_FROM_HOUR = 8

#: How many times in one day Argon may ask what the plan is before letting it
#: go. He did say to keep asking until he names something — but nine identical
#: questions between 8 AM and 9:30 PM, three days running, all unanswered, is
#: not persistence. If he has ignored it this many times, he has answered.
MAX_PLAN_ASKS_PER_DAY = 3

#: Moments he chose himself, each tied to one block or event. Two rules follow.
#:
#: The daily cap does not apply: eight discretionary "want to use this hour?"
#: offers used to exhaust the budget by five and silently swallow the 7 PM block
#: he had actually asked to be reminded about — the cap suppressing exactly the
#: messages it exists to make room for. He can still bound these: plan less.
#:
#: Nor does the reword filter, because the ledger already dedupes them by id.
#: Running both silenced every single block_end: "How did the All Project Sync
#: go?" necessarily shares its subject with "All Project Sync starts now", and
#: the block's name is the whole point of the message.
HIS_OWN_SCHEDULE = frozenset({"block_start", "block_end", "upcoming"})

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
        # The plan drives the day. `plan_request` runs until there is one;
        # after that the blocks he named are the schedule.
        Occasion("plan_request", "the day has no shape yet", PLAN_ASK_COOLDOWN_MIN),
        Occasion("block_start", "a block of his plan starts about now", 0),
        Occasion("block_end", "a block of his plan just finished", 0),
        Occasion("open_stretch", "he left this stretch of the day unclaimed", 0),
        Occasion("session", "a work session has been running a while", 45),
        Occasion("evening", "the day is winding down", 0),
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


def _gap_key(gap: Any) -> str:
    """Identify a free stretch by where it ends — the next thing he committed to.

    Keying on when it was noticed made one stretch of free time produce a
    message at 12:30 and another at 13:00.
    """
    return "gap:{}".format("{:%H:%M}".format(gap.end) if gap.end else "open")


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
        unprompted_from_hour: int = 16,
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
        self.unprompted_from_hour = unprompted_from_hour
        self._state = DailyState(workspace)
        self._plan = DayPlan(workspace)
        self.ledger = CheckInLedger(workspace)
        #: The event that caused an `upcoming` occasion, handed to build_prompt.
        self._pending: dict[str, Any] | None = None
        #: Same, for the plan-driven occasions.
        self._pending_block: Any = None
        self._pending_gap: Any = None
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
            from argon.google.tasks_store import GoogleTasksStore
            from argon.services import agenda
            from argon.tools.tasks import mark_scheduled, unscheduled

            tasks = GoogleTasksStore(self.workspace).get_all()
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

        if occasion.kind == "plan_request":
            asked = self._plan.times_asked()
            again = ""
            if asked:
                # It asked nine times in one day, each a reword of the last,
                # because it was shown "(sent)" as its own history and could
                # not see it had already asked. The history is real now; say
                # plainly that a second ask must not be the same question.
                again = (
                    "You have already asked {} time(s) today and he has not "
                    "answered. Do not ask the same question again — either put "
                    "it a different way with something concrete from his list, "
                    "or reply {} and leave it.\n"
                ).format(asked, SKIP_TOKEN)
            known = (
                "He already has these fixed today, so ask what goes around "
                "them rather than starting from a blank day.\n"
                if self._plan.exists() else ""
            )
            return (
                "ASK WHAT HIS DAY LOOKS LIKE. One question, plain — what is he "
                "doing today and roughly when. Whatever he says, record it with "
                "set_day_plan; those blocks become when you check in, so this "
                "is the message that makes the rest of the day work.\n"
                f"{known}{again}"
                "If he says he doesn't want to plan, call set_day_plan with "
                "planning: false and leave him alone about it.\n\n"
            )

        if occasion.kind == "block_start" and self._pending_block:
            return (
                "HIS PLAN SAYS: {} starts about now ({}). Say one line marking "
                "it — he chose this time, so you are reminding him of his own "
                "decision, not proposing one. If he confirms, start_task if it "
                "matches a task.\n\n"
                .format(self._pending_block.what, self._pending_block.start)
            )

        if occasion.kind == "block_end" and self._pending_block:
            return (
                "HIS PLAN SAYS: {} was meant to end about now ({}). Ask how it "
                "went in one line. When he answers, record it with "
                "update_plan_block so you stop asking.\n\n"
                .format(self._pending_block.what, self._pending_block.end)
            )

        if occasion.kind == "open_stretch" and self._pending_gap:
            until = (
                "until {:%-I:%M %p}".format(self._pending_gap.end)
                if self._pending_gap.end else "for a while"
            )
            return (
                "HE HAS NOTHING PLANNED {} ({} minutes). Offer it back to him: "
                "does he want to use it on something, or is it downtime? Both "
                "answers are fine and you must not push — the point is that he "
                "chooses, not that he works.\n\n"
                .format(until.upper(), self._pending_gap.minutes)
            )
        return ""

    def _seed_plan(self) -> None:
        """Adopt today's commitments as the plan, if he has not stated one."""
        if self._plan.exists() or self._plan.declined():
            return
        try:
            from argon.services import agenda

            self._plan.seed_from(agenda.upcoming(self.workspace))
        except Exception:  # noqa: BLE001 — a calendar outage must not mute the gate
            logger.warning("Could not seed the day plan from commitments")

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
        at_cap = self.ledger.spoken_count() >= self.max_per_day
        quiet_for = self.ledger.minutes_since_said(now)
        # A floor between messages, so two occasions coming due together do not
        # read as a double-text. His own scheduled moments get a much shorter
        # one: a 7 PM block he asked to be reminded about was being dropped
        # entirely because a discretionary offer had landed at 6:55, and the
        # 20-minute grace window closed before the floor lifted. Silence is the
        # wrong way to space out messages he specifically asked for.
        if quiet_for < TICK_MINUTES:
            return None

        # An event about to start outranks everything, the mid-flow guard
        # included: being deep in a task is exactly when you miss the thing you
        # have to leave for. Announced once per event, never re-announced.
        if (event := self._pending_event()) is not None:
            self._pending = event
            return OCCASIONS["upcoming"]

        # A block boundary is a moment he chose, so it outranks the mid-flow
        # guard: "that's your two hours" is the point of having named an end.
        if (block := self._plan.just_ended(now)) is not None and not self.ledger.announced(
            "end:" + block.id
        ):
            self._pending_block = block
            return OCCASIONS["block_end"]

        if mode in ("working", "lock_in"):
            # Otherwise mid-flow, only the session occasion earns an interruption.
            minutes = self._state.get_work_session_duration_minutes() or 0
            if minutes >= SESSION_FLOOR_MINUTES and self._ready("session", now):
                return OCCASIONS["session"]
            return None

        if mode == "done":
            return None

        if (block := self._plan.starting_now(now)) is not None and not self.ledger.announced(
            "start:" + block.id
        ):
            self._pending_block = block
            return OCCASIONS["block_start"]

        # Everything below is discretionary: the full floor, the cap, and the
        # hour before which Argon does not start conversations. A block he
        # scheduled for 10 AM still lands — a secretary would not chat before
        # four, but would certainly tell you about your ten o'clock.
        if at_cap or quiet_for < self.min_gap_minutes:
            return None
        if now.hour < self.unprompted_from_hour:
            return None

        # Before concluding the day has no shape, adopt what he has already
        # committed to. He had a 3 PM and a 7 PM reminder and had said so in
        # chat twice; asking him to describe that day would have been the exact
        # message this design exists to stop.
        self._seed_plan()

        # No plan means one job: get one. This is the only nag by design — he
        # asked to be pestered until he says what he wants out of the day.
        #
        # A seeded plan does not count as an answer. One recurring calendar
        # entry ("All Project Sync", 7-8pm) was enough to make the day look
        # planned, so Argon never asked what he was actually doing and said two
        # things all day, one of them about a meeting he had not mentioned. The
        # seed gives the day its known fixtures; it is not him telling you his
        # plan. The prompt shows the seeded blocks so the question can be
        # "you've got X at 7 — what else?" rather than starting from nothing.
        if not self._plan.answered() and not self._plan.declined():
            asked_enough = self._plan.times_asked() >= MAX_PLAN_ASKS_PER_DAY
            if (
                PLAN_ASK_FROM_HOUR <= now.hour
                and not asked_enough
                and self._ready("plan_request", now)
            ):
                return OCCASIONS["plan_request"]
            return None

        # He has a plan and is between blocks. Offer the free time back to him
        # rather than assuming it is work time; that assumption is what made
        # the old `idle` nudge feel like nagging.
        #
        # A gap only means something relative to a plan. With no blocks at all
        # the whole day reads as one long gap, which is the old ambient nudge
        # wearing a different hat — and it fired even after he had said he was
        # not planning today, which is the one thing that answer must prevent.
        if not self._plan.exists():
            return None
        gap = self._plan.open_stretch(now)
        if gap is not None and not self.ledger.announced(_gap_key(gap)):
            self._pending_gap = gap
            return OCCASIONS["open_stretch"]

        hour = now.hour + now.minute / 60
        if 20 <= hour < 22.5 and self._ready("evening", now):
            return OCCASIONS["evening"]
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
            "If a task is days past due and still open, it has usually stopped "
            "being real — finished and never ticked off, or quietly dropped. "
            "Asking which is more useful than repeating it back to him.\n\n"
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
        # And per block, per gap — each moment of the plan is worth one word,
        # not one every tick until the grace window closes.
        if occasion.kind == "block_start" and self._pending_block:
            self.ledger.record_announced("start:" + self._pending_block.id)
        if occasion.kind == "block_end" and self._pending_block:
            self.ledger.record_announced("end:" + self._pending_block.id)
        if occasion.kind == "open_stretch" and self._pending_gap:
            self.ledger.record_announced(_gap_key(self._pending_gap))
        if occasion.kind == "plan_request":
            self._plan.record_asked()
        logger.info("Check-in: {}", occasion.kind)

        said = await self.on_check_in(self.build_prompt(occasion))
        text = (said or "").strip() if isinstance(said, str) else ""
        if is_silence(text):
            logger.debug("Check-in ({}): nothing to say", occasion.kind)
            return ""
        # The reword filter is for occasions that could repeat themselves. The
        # ones keyed to a specific block or event cannot: each fires once, by
        # id. Running it on them silenced every single block_end, because
        # "How did the All Project Sync go?" necessarily shares its subject
        # with "All Project Sync starts now." — the block name is the point.
        if occasion.kind not in HIS_OWN_SCHEDULE and is_near_duplicate(
            text, self.ledger.said_today()
        ):
            logger.info("Check-in ({}) suppressed as a reword: {}", occasion.kind, text[:60])
            return ""
        if text:
            self.ledger.record_said(occasion.kind, text, now)
            logger.info("Check-in spoke ({}): {}", occasion.kind, text[:80])
        return text
