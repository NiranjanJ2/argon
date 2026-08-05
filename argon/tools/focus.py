"""Screen Time control — publishes the focus mode the iPhone reconciles toward."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from argon import clock
from argon.ios import mode as ios_mode
from argon.paths import get_runtime_subdir
from argon.productivity.state import DailyState
from argon.tools.base import Tool


#: Outside these hours a block needs explicit confirmation.
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7


class SetFocusModeTool(Tool):
    """Ask the phone to block (or unblock) apps."""

    def __init__(
        self, default_lock_minutes: int = 60, state: DailyState | None = None
    ) -> None:
        self._default_minutes = default_lock_minutes
        self._state = state
        self._refused_this_turn = False

    def start_turn(self) -> None:
        """Called by the loop before each turn. See ``_night_block_refused``."""
        self._refused_this_turn = False

    def _night_block_refused(self) -> str | None:
        """Refuse a night-time block until Niranjan has actually been asked.

        A ``confirmed`` parameter is worthless here: faced with the first
        version of this guard the model simply set ``confirmed: true`` itself
        and locked the phone at 1:47 AM anyway. A flag the model controls is
        not a guard, so consent is inferred from the shape of the conversation
        instead. The first attempt is always refused and the refusal is
        recorded; retrying inside the same turn is refused again, which leaves
        the model no option but to end its turn and ask. Only once Niranjan has
        replied — a new turn — does the recorded refusal let it through.
        """
        hour = clock.now().hour
        if not (hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR):
            return None

        if not self._refused_this_turn and self._asked_recently():
            return None  # refused in an earlier turn, and he has since replied

        self._refused_this_turn = True
        self._record_refusal()
        return (
            f"Not applied: it is {clock.now():%-I:%M %p}. Ask Niranjan whether he "
            "really wants his phone blocked right now, and only do it if he says "
            "yes in his next message."
        )

    def _refusal_file(self) -> Path:
        return get_runtime_subdir("ios") / "night_prompt.json"

    def _record_refusal(self) -> None:
        self._refusal_file().write_text(
            json.dumps({"at": clock.now().isoformat()}), encoding="utf-8"
        )

    def _asked_recently(self, minutes: int = 30) -> bool:
        try:
            stamp = json.loads(self._refusal_file().read_text(encoding="utf-8"))["at"]
            asked = datetime.fromisoformat(stamp)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return False
        return (clock.now() - asked).total_seconds() < minutes * 60

    @property
    def name(self) -> str:
        return "set_focus_mode"

    @property
    def description(self) -> str:
        return (
            "Block or unblock apps on Niranjan's iPhone via Screen Time. "
            "This is a real interruption — use it when there is a concrete reason "
            "(a deadline he has not started, a work session he asked you to protect), "
            "not as a general nudge. Always give a reason; he sees it in the app. "
            "Use 'off' to release. Set allow_early_end to false only when he asked "
            "for that in advance. Only call this when he is asking to be blocked "
            "*now* — 'today I want to lock in' is a plan, not a request to lock "
            "his phone this second. If an emergency override is active this "
            "refuses outright — that is deliberate; do not work around it."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": list(ios_mode.MODES),
                    "description": "Focus mode to apply. 'off' clears any block.",
                },
                "duration_min": {
                    "type": "integer",
                    "description": (
                        f"Minutes before the block releases itself (default "
                        f"{self._default_minutes}). Ignored for 'off'."
                    ),
                },
                "allow_early_end": {
                    "type": "boolean",
                    "description": "May he end it early? Default true.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why — shown on the phone. Be specific.",
                },
            },
            "required": ["mode"],
        }

    async def execute(self, **kwargs: Any) -> str:
        mode = kwargs.get("mode")
        if mode not in ios_mode.MODES:
            return f"Error: mode must be one of {list(ios_mode.MODES)}."

        duration = kwargs.get("duration_min")
        if mode != "off" and not duration:
            duration = self._default_minutes

        # "Today I want to lock in for SAT prep" is a plan, not an instruction
        # to lock the phone now — and it was said at 1:37 AM, which is when
        # Argon locked it.
        if mode != "off" and (refusal := self._night_block_refused()):
            return refusal

        try:
            desired = ios_mode.set_mode(
                mode,
                duration_min=duration,
                allow_early_end=bool(kwargs.get("allow_early_end", True)),
                reason=str(kwargs.get("reason") or ""),
            )
        except ios_mode.OverrideActive as exc:
            # Niranjan pulled the emergency release. Do not argue with it, and
            # do not pretend the block was applied.
            return f"Not applied: {exc}. Leave it alone until then."

        # Blocking his phone and tracking what he is doing are the same event
        # seen from two sides. Left independent they contradicted each other:
        # a lock_in block with the day still recorded as idle, so the check-in
        # gate treated a locked phone as free time and texted him anyway.
        if self._state is not None:
            if mode == "off":
                if self._state.get_mode() == "lock_in":
                    self._state.set_mode("idle")
            elif mode == "lock_in":
                self._state.set_mode("lock_in")

        if mode == "off":
            return "Screen Time block released."

        # The phone applies this when it next reconciles, so promise intent, not
        # completion — saying "locked" when the phone is in a drawer is a lie.
        window = f" until {desired['expires_at'][11:16]}" if desired["expires_at"] else ""
        requested = f"Focus mode '{mode}' requested{window}."

        state, detail = ios_mode.convergence()
        if state == "never_seen":
            return f"{requested} The phone has never checked in — it may not be paired yet."
        if state in ("stale", "diverged", "failed"):
            # Worth saying plainly: the previous request never landed either.
            return f"{requested} But {detail}. Do not assume it is locked."
        return f"{requested} Waiting for the phone to pick it up."
