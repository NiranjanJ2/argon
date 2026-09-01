"""The evening watch: is he working, and if not, does something need to happen.

This ran 1,531 times in August and spent zero model calls, because Phase 1 asked
the model to review a HEARTBEAT.md that had never been written in. The service
was alive and the agent inside it had never once woken up — which is why nothing
ever reminded him about pending work or turned Screen Time on by itself.

Two things changed. The window is now his evening rather than all day, and the
decision to wake the agent is read from **state** instead of asked of a model
looking at static markdown. `_situation` is ordinary Python over DailyState, the
board and the phone: it is free, it cannot hallucinate, and it says no far more
often than yes.

The self-limiting property is the point. The watch fires only when he is *not*
working; if it engages a focus session, the next tick sees `working` and stands
down. Acting once and going quiet is the behaviour a nagging loop never had.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

from argon.services.reminder import extract_message, is_silence

if TYPE_CHECKING:
    from argon.providers.base import LLMProvider

_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of active tasks (required for run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


class HeartbeatService:
    """
    Periodic heartbeat service that wakes the agent to check for tasks.

    Phase 1 (decision): reads HEARTBEAT.md and asks the LLM — via a virtual
    tool call — whether there are active tasks.  This avoids free-text parsing
    and the unreliable HEARTBEAT_OK token.

    Phase 2 (execution): only triggered when Phase 1 returns ``run``.  The
    ``on_execute`` callback runs the task through the full agent loop and
    returns the result to deliver.
    """

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        on_execute: Callable[[str], Coroutine[Any, Any, str]] | None = None,
        on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        interval_s: int = 30 * 60,
        enabled: bool = True,
        timezone: str | None = None,
        active_from_hour: int = 16,
        active_until_hour: int = 0,
    ):
        self.workspace = workspace
        self.provider = provider
        self.model = model
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self.timezone = timezone
        #: The evening only. Before he is home there is nothing to watch, and
        #: after midnight the answer to "should you be working" is no.
        self.active_from_hour = active_from_hour
        self.active_until_hour = active_until_hour
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def heartbeat_file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        """Return the file, or None when it holds no actual tasks.

        A template HEARTBEAT.md is all headings and HTML comments. Returning it
        anyway made every tick spend an LLM call to conclude "nothing to do" —
        48 pointless calls a day. Strip the scaffolding and check what is left.
        """
        if not self.heartbeat_file.exists():
            return None
        try:
            content = self.heartbeat_file.read_text(encoding="utf-8")
        except Exception:
            return None
        # Drop comments, including an unterminated trailing one — a half-written
        # `<!--` would otherwise read as a live task forever.
        body = re.sub(r"<!--.*?-->", "", content, flags=re.S)
        body = re.sub(r"<!--.*$", "", body, flags=re.S)
        body = "\n".join(
            line for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
        return content if body.strip() else None

    def _in_window(self) -> bool:
        """His evening. Wraps past midnight if the hours are set that way."""
        from argon import clock

        hour = clock.now().hour
        a, b = self.active_from_hour, self.active_until_hour
        if a == b:
            return True
        return a <= hour < b if a < b else (hour >= a or hour < b)

    def _situation(self) -> dict[str, Any]:
        """What is true right now. No model, no network beyond cached reads.

        Everything here is a fact the rest of Argon already maintains. Asking a
        model to infer it from prose was the old Phase 1, and it is how a watch
        that should have said "he is working, leave him alone" instead said
        nothing at all for a month.
        """
        from argon.productivity.state import DailyState

        out: dict[str, Any] = {
            "mode": "idle", "current_task": None, "started_today": False,
            "due_now": [], "shielded": False, "phone": "unknown", "override": False,
            "before_start": False,
        }
        try:
            data = DailyState(self.workspace).get()
            out["mode"] = data.get("mode", "idle")
            out["current_task"] = data.get("current_task")
        except Exception:  # noqa: BLE001 - a fault must not wake the agent
            return out

        try:
            from argon.productivity.log import DailyLog

            page = DailyLog(self.workspace).get_path()
            out["started_today"] = "Started:" in (page.read_text() if page.exists() else "")
        except Exception:  # noqa: BLE001
            pass

        try:
            from argon import clock
            from argon.commitments import load_board

            board = load_board(self.workspace)
            if board.source("tasks").ok:
                today = clock.today_key()
                out["due_now"] = [
                    r for r in board.as_dicts()
                    if (r.get("due") or "")[:10] and (r.get("due") or "")[:10] <= today
                ]
        except Exception:  # noqa: BLE001 - never invent work from a failure
            pass

        try:
            from argon import clock, planner

            hhmm = planner.start_time() or planner.DEFAULT_START_HHMM
            hour, minute = (int(x) for x in hhmm.split(":"))
            now = clock.now()
            out["before_start"] = now < now.replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
        except Exception:  # noqa: BLE001 - a planner fault must not license a nudge
            out["before_start"] = True

        try:
            from argon.ios import mode as ios_mode

            out["shielded"] = bool(ios_mode.get_actual().get("shielded"))
            out["phone"] = ios_mode.convergence()[0]
            out["override"] = ios_mode.override_status()[0]
        except Exception:  # noqa: BLE001
            pass
        return out

    def _decide(self, situation: dict[str, Any]) -> str | None:
        """Why the agent should wake, or None to stay quiet.

        Deliberately narrow. Being mid-work is not an occasion — that is the one
        lesson every removed nudge in this codebase taught — so the only thing
        this watch acts on is an evening with work due and nothing running.
        """
        if situation["mode"] in ("working", "lock_in"):
            return None  # he is at it; the watch exists to leave him alone
        if situation["mode"] in ("napping", "done"):
            return None
        if situation["before_start"]:
            # He gets home at four and naps; the evening he chose starts later.
            # Not working before his own start time is the plan, not a lapse —
            # and `napping` cannot cover this because it has to be set by hand
            # and has never once been set in a month of running.
            return None
        if situation["override"]:
            # He pulled the emergency release. Without this the watch simply
            # locks him again on the next tick, which turns "let me out" into a
            # thing he has to keep saying — the nagging failure, with a shield.
            return None
        if not situation["due_now"]:
            return None
        return "nothing is running and work is due"

    async def start(self) -> None:
        """Start the heartbeat service."""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """Stop the heartbeat service."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)

    async def _tick(self) -> None:
        """One look at the evening."""
        from argon import clock

        if not self._in_window():
            return

        situation = self._situation()
        reason = self._decide(situation)
        if reason is None:
            logger.debug(
                "Heartbeat: standing down (mode={}, due={})",
                situation["mode"], len(situation["due_now"]),
            )
            return

        logger.info("Heartbeat: {}", reason)
        if not self.on_execute:
            return

        try:
            response = await self.on_execute(self._prompt(situation))
        except Exception:
            logger.exception("Heartbeat execution failed")
            return
        if not response:
            return

        text = extract_message(response)
        if is_silence(text):
            logger.info("Heartbeat: acted, nothing worth saying")
            return
        if self.on_notify is None:
            return

        # At most one heartbeat message an hour. The outbox dedupes on this key,
        # so a watch that fires every 30 minutes cannot become the twelve-message
        # evening the check-in nudges already were.
        now = clock.now()
        key = f"heartbeat:{clock.today_key()}:{now.hour}"
        try:
            await self.on_notify(text, key=key)
        except TypeError:
            await self.on_notify(text)
        logger.info("Heartbeat spoke: {}", text[:80])

    def _prompt(self, situation: dict[str, Any]) -> str:
        """What the agent is woken with.

        It is handed the state rather than told to go and look, for the same
        reason the brief inlines the board: the model reliably skips an optional
        tool call, and a watch that skipped `get_status` would be guessing about
        the one thing it exists to know.
        """
        from argon.utils.helpers import current_time_str

        due = situation["due_now"]
        lines = "\n".join(
            "- {}{}".format(r.get("title"), f" (due {r['due_when']})" if r.get("due_when") else "")
            for r in due[:6]
        ) or "- nothing"
        phone = (
            "shielded" if situation["shielded"]
            else f"not shielded ({situation['phone']})"
        )
        return (
            f"It is {current_time_str(self.timezone)} and this is the evening watch.\n\n"
            f"He is not working on anything right now (mode: {situation['mode']}). "
            f"He has {'started something' if situation['started_today'] else 'started nothing'} "
            f"today. His phone is {phone}.\n\n"
            f"Due today or overdue:\n{lines}\n\n"
            "Your job is to get him working, not to ask him whether he will. "
            "If a focus session would help, start one with set_focus_mode and say "
            "one short line about what you did and why. If he is plainly not "
            "available — out, eating, mid-conversation — do nothing.\n\n"
            "Answer in exactly this form:\n"
            "THINKING: <your reasoning — he never sees this>\n"
            "MESSAGE: <one or two lines, or the single word SKIP>\n\n"
            "Every task you name must appear above. Do not restate the whole "
            "list, do not repeat what you said earlier today, and do not ask him "
            "when he plans to start — that question has been asked thirteen times "
            "and answered none."
        )

    async def trigger_now(self) -> str | None:
        """Manually trigger a heartbeat."""
        content = self._read_heartbeat_file()
        if not content:
            return None
        action, tasks = await self._decide(content)
        if action != "run" or not self.on_execute:
            return None
        return await self.on_execute(tasks)
