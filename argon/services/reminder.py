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
from dataclasses import dataclass
from datetime import datetime
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
            return {"date": today, "fired": {}, "said": []}
        data.setdefault("fired", {})
        data.setdefault("said", [])
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

    def spoken_count(self) -> int:
        return len(self._load(clock.today_key())["said"])


class ReminderService:
    """Decides when Argon has a reason to start a conversation."""

    def __init__(
        self,
        workspace: Path,
        timezone: str,
        on_check_in: Callable[[str], Awaitable[Any]],
        *,
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
        self.enabled = enabled
        self.max_per_day = max_per_day
        self.min_gap_minutes = min_gap_minutes
        self.quiet_start_hour = quiet_start_hour
        self.quiet_end_hour = quiet_end_hour
        self._state = DailyState(workspace)
        self.ledger = CheckInLedger(workspace)
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

    def pick_occasion(self) -> Occasion | None:
        """The reason to reach out right now, or None. No model call involved."""
        now = self._now()
        data = self._state.get()
        mode = data.get("mode", "idle")

        if self._in_quiet_hours(now) or mode == "napping":
            return None
        if self.ledger.spoken_count() >= self.max_per_day:
            return None
        # A floor between messages, whatever the reason. Without it two
        # occasions coming due together read as a double-text.
        if self.ledger.minutes_since_said(now) < self.min_gap_minutes:
            return None

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
        return (
            f"It's {self._now():%-I:%M %p} and {occasion.blurb}.\n\n"
            "First call get_status, and list_tasks if it would tell you anything.\n\n"
            f"Already sent today:\n{history}\n\n"
            "Now WRITE THE TEXT MESSAGE you would send Niranjan — one or two "
            "sentences, unprompted, in your own voice, the way a friend texts. "
            "Something real: a deadline he hasn't started, a session worth "
            "acknowledging, something he mentioned earlier, or just asking how a "
            "thing went.\n\n"
            "Reply with the message itself and nothing else — no preamble, no "
            "explanation, no quotes around it.\n\n"
            f"Don't repeat anything listed above, even reworded. If there is "
            f"genuinely nothing worth saying, reply with exactly {SKIP_TOKEN} "
            "and nothing else — that is better than filler like "
            '"just checking in!".'
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
        # Record the attempt before running: a silent turn should still start
        # this occasion's cooldown, or it retries every tick and burns calls.
        self.ledger.record_fired(occasion.kind, now)
        logger.info("Check-in: {}", occasion.kind)

        said = await self.on_check_in(self.build_prompt(occasion))
        text = (said or "").strip() if isinstance(said, str) else ""
        if is_silence(text):
            logger.debug("Check-in ({}): nothing to say", occasion.kind)
            return ""
        if text:
            self.ledger.record_said(occasion.kind, text, now)
            logger.info("Check-in spoke ({}): {}", occasion.kind, text[:80])
        return text
