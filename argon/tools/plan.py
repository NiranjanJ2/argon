"""Tools for the day's plan — the thing check-ins are scheduled from."""

from __future__ import annotations

import json
from typing import Any

from argon.productivity.plan import DayPlan
from argon.tools.base import Tool


class SetDayPlanTool(Tool):
    """Record how Niranjan says his day is laid out."""

    def __init__(self, plan: DayPlan) -> None:
        self._plan = plan

    @property
    def name(self) -> str:
        return "set_day_plan"

    @property
    def description(self) -> str:
        return (
            "Record how Niranjan's day is laid out, in his own words. Call this "
            "whenever he describes his day or changes it — 'SAT prep at 2, gym "
            "around 5' is a plan. This replaces the whole plan, so include every "
            "block still standing, not just the new one. The blocks become the "
            "schedule for when you check in with him, so getting them right is "
            "how you stop bothering him at the wrong times. If he says he does "
            "not want to plan today, call set_day_plan with planning: false "
            "instead and stop asking. If he says today is the same as "
            "yesterday, pass same_as instead of retyping the blocks."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "blocks": {
                    "type": "array",
                    "description": "Every block of the day, in any order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "start": {
                                "type": "string",
                                "description": "Start time, e.g. '14:00' or '2pm'.",
                            },
                            "end": {
                                "type": "string",
                                "description": "End time. Omit if open-ended.",
                            },
                            "what": {
                                "type": "string",
                                "description": "What he is doing, in his words.",
                            },
                        },
                        "required": ["start", "what"],
                    },
                },
                "planning": {
                    "type": "boolean",
                    "description": "false if he does not want to plan today.",
                },
                "same_as": {
                    "type": "string",
                    "description": (
                        "Copy a previous day's plan instead of listing blocks. "
                        "'yesterday' for the last day he planned, or YYYY-MM-DD. "
                        "Use this when he says today is the same as yesterday."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        if kwargs.get("planning") is False:
            self._plan.decline()
            return "No plan today — I won't ask again."

        if same_as := str(kwargs.get("same_as") or "").strip():
            day = "" if same_as.lower() in ("yesterday", "last", "") else same_as
            copied = self._plan.copy_from(day)
            if not copied:
                return (
                    "Nothing recorded for that day, so there is no plan to copy. "
                    "Ask him what today looks like."
                )
            lines = "; ".join(
                "{}{} {}".format(b.start, "-" + b.end if b.end else "", b.what)
                for b in copied
            )
            return f"Plan set from {same_as}: {lines}"

        blocks = kwargs.get("blocks")
        if not blocks:
            return "Error: give at least one block, or planning: false."

        stored = self._plan.set_blocks(blocks)
        if not stored:
            return (
                "Error: none of those blocks had a usable start time. "
                "Use '14:00' or '2pm'."
            )
        dropped = len(blocks) - len(stored)
        lines = "; ".join(
            "{}{} {}".format(b.start, "-" + b.end if b.end else "", b.what)
            for b in stored
        )
        # Say what was dropped. A block silently missing from the plan is worse
        # than none at all — he would believe Argon had it.
        note = " ({} block(s) had no usable time and were dropped)".format(dropped) if dropped else ""
        return "Plan set: {}{}".format(lines, note)


class UpdatePlanBlockTool(Tool):
    """Mark a block of the plan done or skipped."""

    def __init__(self, plan: DayPlan) -> None:
        self._plan = plan

    @property
    def name(self) -> str:
        return "update_plan_block"

    @property
    def description(self) -> str:
        return (
            "Mark a block of today's plan as done or skipped. Use this when he "
            "tells you how a block went, so the plan reflects the day and you "
            "stop asking about it."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "block_id": {"type": "string", "description": "Block id, e.g. 'b1'."},
                "status": {"type": "string", "enum": ["done", "skipped", "pending"]},
            },
            "required": ["block_id", "status"],
        }

    async def execute(self, **kwargs: Any) -> str:
        block_id, status = kwargs["block_id"], kwargs["status"]
        if not self._plan.mark(block_id, status):
            return "No block '{}' in today's plan.".format(block_id)
        return "Block {} marked {}.".format(block_id, status)


class GetDayPlanTool(Tool):
    """Read today's plan."""

    def __init__(self, plan: DayPlan) -> None:
        self._plan = plan

    @property
    def name(self) -> str:
        return "get_day_plan"

    @property
    def description(self) -> str:
        return "Read today's plan: what he said he'd do and when, and how far along it is."

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> str:
        return json.dumps(
            {
                "blocks": [b.as_dict() for b in self._plan.blocks()],
                "declined_to_plan": self._plan.declined(),
            },
            indent=2,
        )
