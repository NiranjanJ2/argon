"""Tools for the day's plan — the thing check-ins are scheduled from."""

from __future__ import annotations

import json
from typing import Any

from argon.productivity.plan import DayPlan, normalize_time
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
            "Record a plan only when Niranjan explicitly gives one — 'SAT prep "
            "at 2, gym around 5' is a plan. Never derive blocks from tasks, "
            "calendars, habits, or a question he asked. This REPLACES the whole "
            "plan and every block loses its status, so use it only when he lays "
            "out the whole day or clears it (empty blocks list). To change, add, "
            "move or drop ONE block, use update_plan_block instead — do not "
            "resend the day. If he says today is the same as a previous day, "
            "pass same_as."
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
        if same_as := str(kwargs.get("same_as") or "").strip():
            day = "" if same_as.lower() in ("yesterday", "last", "") else same_as
            copied = self._plan.copy_from(day)
            if not copied:
                return (
                    "Nothing recorded for that day, so nothing changed."
                )
            lines = "; ".join(
                "{}{} {}".format(b.start, "-" + b.end if b.end else "", b.what)
                for b in copied
            )
            return f"Plan set from {same_as}: {lines}"

        if "blocks" not in kwargs:
            return "No explicit plan supplied; nothing changed."
        blocks = kwargs.get("blocks") or []

        # What it is replacing, so a rewrite cannot be silent. Asked "what's the
        # board looking like" it rewrote his whole day and answered "Plan set",
        # and he had to work out for himself what had happened to it.
        before = {b.what for b in self._plan.blocks()}
        stored = self._plan.set_blocks(blocks)
        if not blocks:
            return "Plan cleared."
        after = {b.what for b in stored}
        dropped = sorted(before - after)
        if not stored:
            return (
                "Error: none of those blocks had a usable start time. "
                "Use '14:00' or '2pm'."
            )
        unusable = len(blocks) - len(stored)
        lines = "; ".join(
            "{}{} {}".format(b.start, "-" + b.end if b.end else "", b.what)
            for b in stored
        )
        # Say what was dropped. A block silently missing from the plan is worse
        # than none at all — he would believe Argon had it.
        note = " ({} block(s) had no usable time and were skipped)".format(unusable) if unusable else ""
        lost = "  REMOVED from his plan: {}".format(", ".join(dropped)) if dropped else ""
        return "Plan set: {}{}{}".format(lines, note, lost)


class UpdatePlanBlockTool(Tool):
    """Change one block of the plan without touching the others.

    Every edit used to go through ``set_day_plan``, which replaces the day. So a
    single change meant restating every block still standing, and a block the
    model failed to repeat vanished from his plan with nothing saying so. These
    are deltas against stable block ids: the untouched blocks keep their id,
    their timing and their status.
    """

    def __init__(self, plan: DayPlan) -> None:
        self._plan = plan

    @property
    def name(self) -> str:
        return "update_plan_block"

    @property
    def description(self) -> str:
        return (
            "Change ONE block of today's plan, leaving the rest exactly as it "
            "is. Never resend the whole day to make one change. action: 'mark' "
            "(status done/skipped/pending, when he reports how it went), 'add' "
            "(start, what, optional end), 'move' or 'update' (block_id plus a "
            "new start/end/what), 'remove' (block_id). Block ids come from "
            "get_day_plan and do not change when a block is moved or reworded. "
            "Only when he explicitly asks for the change."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["mark", "add", "update", "move", "remove"],
                    "description": "Defaults to 'mark' when a status is given.",
                },
                "block_id": {
                    "type": "string",
                    "description": "Id from get_day_plan. Required for everything but 'add'.",
                },
                "status": {"type": "string", "enum": ["done", "skipped", "pending"]},
                "start": {"type": "string", "description": "New start, e.g. '14:00' or '2pm'."},
                "end": {"type": "string", "description": "New end. Empty string clears it."},
                "what": {"type": "string", "description": "What he is doing, in his words."},
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action") or "").strip().lower()
        block_id = str(kwargs.get("block_id") or "").strip()
        status = str(kwargs.get("status") or "").strip().lower()
        start = kwargs.get("start")
        what = kwargs.get("what")
        if not action:
            action = "mark" if status else ("add" if start else "update")

        if action == "add":
            block = self._plan.add_block(start or "", what or "", kwargs.get("end"))
            if block is None:
                return (
                    "Error: an added block needs what he is doing and a usable "
                    "start time. Use '14:00' or '2pm'."
                )
            return "Added {} to the plan ({}). {}".format(
                _describe(block), block.id, self._rest()
            )

        if not block_id:
            return "Error: block_id is required. Call get_day_plan for the ids."

        if action == "remove":
            block = self._plan.remove_block(block_id)
            if block is None:
                return _missing(block_id)
            return "Removed {} from the plan. {}".format(_describe(block), self._rest())

        if action == "mark":
            if status not in ("done", "skipped", "pending"):
                return "Error: status must be done, skipped or pending."
            if not self._plan.mark(block_id, status):
                return _missing(block_id)
            return "Block {} marked {}.".format(block_id, status)

        # update / move — the same operation; a move is a retime.
        if start is not None and normalize_time(start) is None:
            return "Error: '{}' is not a usable time. Use '14:00' or '2pm'.".format(start)
        changes: dict[str, Any] = {"start": start, "what": what}
        if "end" in kwargs:
            end = kwargs["end"]
            if end and normalize_time(end) is None:
                return "Error: '{}' is not a usable time. Use '16:00' or '4pm'.".format(end)
            # An explicit empty end means "open-ended from here on".
            changes["end"] = end or None
        block = self._plan.update_block(block_id, **changes)
        if block is None:
            return _missing(block_id)
        return "Block {} is now {}. {}".format(block.id, _describe(block), self._rest())

    def _rest(self) -> str:
        """The rest of the day, unchanged — so the edit is visibly a delta."""
        blocks = self._plan.blocks()
        if not blocks:
            return "The plan is now empty."
        return "Plan: {}".format("; ".join(_describe(b) for b in blocks))


def _describe(block: Any) -> str:
    return "{}{} {}".format(block.start, "-" + block.end if block.end else "", block.what)


def _missing(block_id: str) -> str:
    return "No block '{}' in today's plan. Call get_day_plan for the ids.".format(block_id)


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
            },
            indent=2,
        )
