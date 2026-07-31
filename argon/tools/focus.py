"""Screen Time control — publishes the focus mode the iPhone reconciles toward."""

from __future__ import annotations

from typing import Any

from argon.ios import mode as ios_mode
from argon.tools.base import Tool


class SetFocusModeTool(Tool):
    """Ask the phone to block (or unblock) apps."""

    def __init__(self, default_lock_minutes: int = 60) -> None:
        self._default_minutes = default_lock_minutes

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
            "for that in advance. If an emergency override is active this refuses "
            "outright — that is deliberate; do not try to work around it."
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
