"""Screen Time control — publishes the focus mode the iPhone reconciles toward."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from argon import clock
from argon.core import store, turn
from argon.ios import mode as ios_mode
from argon.productivity.state import DailyState
from argon.tools.base import Tool, ToolResult

#: Outside these hours a block needs explicit confirmation.
NIGHT_START_HOUR = 23
NIGHT_END_HOUR = 7

#: How long a request for consent stays answerable. Long enough to reply to,
#: short enough that "yes" an hour later is not read as consent to something he
#: has forgotten he was asked.
CONSENT_TTL_MINUTES = 10

#: Where the outstanding request lives. See argon/core/store.py.
CONSENT_DOC = "pending_confirmation"

#: What counts as "yes" — the whole message, not a substring. "no, don't",
#: "why would you do that" and "ok but not now" must all fail this.
_AFFIRMATIVE = frozenset({
    "y", "yes", "yeah", "yep", "yup", "yes please", "please do", "do it",
    "go ahead", "goahead", "confirm", "confirmed", "affirmative", "sure",
    "ok", "okay", "k", "lock it", "block it", "lock my phone", "yes do it",
})


def is_affirmative(text: str) -> bool:
    """Is this message, in its entirety, an unambiguous yes?

    Deliberately strict and deliberately not a model call. Anything that is not
    plainly a yes is not consent to blocking his phone at 2 AM.
    """
    normalized = " ".join(re.findall(r"[a-z']+", (text or "").lower()))
    return normalized in _AFFIRMATIVE


class SetFocusModeTool(Tool):
    """Ask the phone to block (or unblock) apps."""

    def __init__(
        self, default_lock_minutes: int = 60, state: DailyState | None = None
    ) -> None:
        self._default_minutes = default_lock_minutes
        self._state = state

    @staticmethod
    def _fingerprint(mode: str, duration: Any, allow_early_end: bool) -> str:
        """The exact action being consented to, not merely "a block"."""
        return f"set_focus_mode|{mode}|{duration}|{bool(allow_early_end)}"

    def _night_block_refused(
        self, mode: str, duration: Any, allow_early_end: bool
    ) -> str | None:
        """Refuse a night-time block until Niranjan has actually said yes to *this*.

        A ``confirmed`` parameter was worthless: faced with the first version of
        this guard the model set ``confirmed: true`` itself and locked the phone
        at 1:47 AM. The second version inferred consent from the shape of the
        conversation — a recorded refusal plus *any* later message inside thirty
        minutes. That is not consent either. It meant "actually never mind",
        "what time is it?", or a message about something else entirely all
        unlocked the block, and with a tool instance shared across sessions the
        later message did not even have to come from the same conversation.

        Consent is now bound to four things at once: the exact action and its
        parameters, the session it was asked in, an explicit yes in the message
        now being processed, and an expiry. Change the duration and it must be
        asked again, because that is a different thing to agree to.
        """
        hour = clock.now().hour
        if not (hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR):
            return None

        ctx = turn.current()
        fingerprint = self._fingerprint(mode, duration, allow_early_end)
        if ctx is None or ctx.automated:
            # Nothing Argon started can consent on his behalf.
            return (
                f"Not applied: it is {clock.now():%-I:%M %p} and this turn was not "
                "started by Niranjan. Only he can authorise a night-time block."
            )

        if self._consumed(ctx, fingerprint):
            return None

        self._request(ctx, fingerprint)
        return (
            f"Not applied: it is {clock.now():%-I:%M %p}. Ask Niranjan whether he "
            "really wants his phone blocked right now, and say what for and for "
            "how long. Only apply it if his next message is an explicit yes to "
            "exactly this."
        )

    def _request(self, ctx: turn.TurnContext, fingerprint: str) -> None:
        now = clock.now()
        store.put_doc(CONSENT_DOC, {
            "session_key": ctx.session_key,
            "action": fingerprint,
            # The turn that asked. Consent must arrive in a *later* one.
            "asked_in_turn": ctx.turn_id,
            "asked_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=CONSENT_TTL_MINUTES)).isoformat(),
        })

    def _consumed(self, ctx: turn.TurnContext, fingerprint: str) -> bool:
        """Is the message being processed right now a yes to exactly this?

        The turn check is what stops the model consenting on his behalf. A turn
        gets many tool calls and ``user_text`` is the same for all of them, so
        without it the model could call this twice in one turn — refused, then
        allowed — with his only message being an "ok" about something else.
        That is the 1:47 AM phone lock, reachable again by a different route.
        """
        record = store.get_doc(CONSENT_DOC)
        if not isinstance(record, dict):
            return False
        if record.get("action") != fingerprint:
            return False
        if record.get("session_key") != ctx.session_key:
            return False
        if record.get("asked_in_turn") == ctx.turn_id:
            return False
        try:
            expires = datetime.fromisoformat(str(record.get("expires_at")))
        except (TypeError, ValueError):
            return False
        if clock.now() >= expires:
            return False
        if not is_affirmative(ctx.user_text):
            return False
        # One yes authorises one block.
        store.put_doc(CONSENT_DOC, {})
        return True

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
            return ToolResult(f"Error: mode must be one of {list(ios_mode.MODES)}.", success=False)

        duration = kwargs.get("duration_min")
        if mode != "off" and not duration:
            duration = self._default_minutes

        # "Today I want to lock in for SAT prep" is a plan, not an instruction
        # to lock the phone now — and it was said at 1:37 AM, which is when
        # Argon locked it.
        if mode != "off" and (refusal := self._night_block_refused(
            mode, duration, bool(kwargs.get("allow_early_end", True))
        )):
            return ToolResult(refusal, success=False)

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
            return ToolResult(f"Not applied: {exc}. Leave it alone until then.", success=False)

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
            return ToolResult("Screen Time block released.")

        # The phone applies this when it next reconciles, so promise intent, not
        # completion — saying "locked" when the phone is in a drawer is a lie.
        window = f" until {desired['expires_at'][11:16]}" if desired["expires_at"] else ""
        requested = f"Focus mode '{mode}' requested{window}."

        state, detail = ios_mode.convergence()
        if state == "never_seen":
            return ToolResult(f"{requested} The phone has never checked in — it may not be paired yet.")
        if state in ("stale", "diverged", "failed"):
            # Worth saying plainly: the previous request never landed either.
            return ToolResult(f"{requested} But {detail}. Do not assume it is locked.")
        return ToolResult(f"{requested} Waiting for the phone to pick it up.")
