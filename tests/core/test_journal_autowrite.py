"""The day page must fill itself.

Argon sent three near-identical "SAT prep is due" nudges in one morning because
the journal had a single writer — the model choosing to call ``remember`` — and
it almost never did. Every check-in therefore opened with "What Niranjan said or
did today: (nothing recorded)" and re-derived the day from the task list alone.

These tests pin the recording down: state-changing tools produce a line,
read-only ones do not, and Argon's own check-in prompts never land in the page
as though Niranjan had said them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from argon.core.hook import AgentHookContext
from argon.core.journal import UNDATED, Fact, Journal, describe_tool, parse_facts
from argon.core.loop import _LoopHook
from argon.providers.base import ToolCallRequest
from argon.tools.base import Tool, ToolResult
from argon.tools.registry import ToolRegistry


class TestDescribeTool:
    def test_state_changing_tools_produce_a_line(self):
        assert describe_tool("set_mode", {"mode": "lock_in"}) == "mode -> lock_in"
        assert describe_tool("add_task", {"title": "Chem pset"}) == "added task: Chem pset"
        assert describe_tool("complete_task", {"task_id": "SAT prep"}) == "completed: SAT prep"
        assert describe_tool("set_focus_mode", {"mode": "lock_in", "duration_min": 45}) == (
            "phone -> lock_in for 45m"
        )

    def test_read_only_tools_are_ignored(self):
        """A day page full of "checked the status" is noise in the check-in prompt."""
        for name in ("get_status", "list_tasks", "recall", "web_search", "read_file"):
            assert describe_tool(name, {}) is None

    def test_a_tool_with_unexpected_arguments_never_raises(self):
        """Journalling must not be able to break the tool call it is describing."""
        assert describe_tool("set_mode", {}) == "mode -> ?"
        assert describe_tool("add_task", None) == "added task: ?"
        assert describe_tool("log_note", {}) is None


class TestDescribersMatchRealSchemas:
    """Describers read tool arguments by name, so they rot silently.

    ``snooze_check_ins`` shipped reading ``minutes`` when the tool takes
    ``hours``, so a 24-hour rest day was journalled as a bare "asked for quiet"
    with the duration dropped — and nothing failed.
    """

    @staticmethod
    def _real_tools() -> dict[str, set[str]]:
        import importlib
        import inspect

        from argon.tools.base import Tool

        found: dict[str, set[str]] = {}
        for mod in ("tasks", "status", "focus", "quiet", "memory", "bell", "cron"):
            try:
                module = importlib.import_module(f"argon.tools.{mod}")
            except ImportError:
                continue
            for obj in vars(module).values():
                if not (inspect.isclass(obj) and issubclass(obj, Tool) and obj is not Tool):
                    continue
                try:
                    inst = obj.__new__(obj)
                    found[inst.name] = set((inst.parameters.get("properties") or {}).keys())
                except Exception:
                    continue
        assert found, "could not introspect any tools"
        return found

    def test_every_described_tool_actually_exists(self):
        from argon.core.journal import _TOOL_NOTES

        real = self._real_tools()
        # focus/bell tools can fail to introspect without instance state; only
        # assert on names we did manage to resolve plus the ones we know of.
        unknown = {n for n in _TOOL_NOTES if n not in real and n != "set_focus_mode"}
        assert not unknown, f"describers for tools that do not exist: {unknown}"

    def test_describers_only_read_parameters_the_tool_declares(self):
        """A describer reading a key the schema never sends silently loses detail."""
        from argon.core.journal import _TOOL_NOTES

        real = self._real_tools()
        for name, make in _TOOL_NOTES.items():
            if name not in real:
                continue
            # Feed every declared parameter a marker; anything the describer
            # reports must have come from a real one.
            args = {key: f"<{key}>" for key in real[name]}
            line = make(args)
            assert line, f"{name} produced no line from its own schema"
            assert "?" not in line, (
                f"{name} fell back to '?' given every declared parameter "
                f"{sorted(real[name])} — it is reading a key the tool never sends"
            )


class TestJournalWriting:
    def test_notes_land_on_todays_page(self, tmp_path):
        journal = Journal(tmp_path)
        journal.note("mode -> lock_in", kind="did")
        journal.note("rest day today", kind="said")

        page = journal.read_day()

        assert "[did] mode -> lock_in" in page
        assert "[said] rest day today" in page

    def test_the_page_is_append_only(self, tmp_path):
        """The old design rewrote the file and lost everything each time."""
        journal = Journal(tmp_path)
        for i in range(5):
            journal.note("entry {}".format(i))
        assert len(journal.read_day().splitlines()) == 5

    def test_empty_notes_are_dropped(self, tmp_path):
        journal = Journal(tmp_path)
        journal.note("   ")
        assert journal.read_day() == ""


@pytest.mark.asyncio
async def test_a_failed_mutation_never_enters_the_journal_as_done():
    wrote = []
    loop = SimpleNamespace(_journal_tool=lambda name, args: wrote.append((name, args)))
    hook = _LoopHook(loop)
    context = AgentHookContext(
        iteration=0,
        messages=[],
        tool_calls=[
            ToolCallRequest(id="start", name="start_task", arguments={"task_id": "a"}),
            ToolCallRequest(id="finish", name="complete_task", arguments={"task_id": "a"}),
        ],
        tool_events=[
            {"name": "start_task", "status": "ok", "detail": "Started"},
            {"name": "complete_task", "status": "error", "detail": "network down"},
        ],
    )

    await hook.after_iteration(context)

    assert wrote == [("start_task", {"task_id": "a"})]


@pytest.mark.asyncio
async def test_a_semantic_mutation_refusal_is_classified_as_an_error_before_journalling():
    """A refusal changes no state even when its human text is not an exception."""
    from argon.core.runner import AgentRunner, AgentRunSpec

    class RefuseFocus(Tool):
        @property
        def name(self):
            return "set_focus_mode"

        @property
        def description(self):
            return "test only"

        @property
        def parameters(self):
            return {"type": "object", "properties": {}, "required": []}

        async def execute(self, **_kwargs):
            return ToolResult("Not applied: confirmation required.", success=False)

    registry = ToolRegistry()
    registry.register(RefuseFocus())
    spec = AgentRunSpec(
        initial_messages=[], tools=registry, model="test", max_iterations=1,
        max_tool_result_chars=1000,
    )
    _, event, error = await AgentRunner(object())._run_tool(
        spec, ToolCallRequest(id="focus", name="set_focus_mode", arguments={}), {}
    )

    assert error is None
    assert event["status"] == "error"


def test_automation_speech_is_not_attributed_to_niranjan():
    wrote = []
    loop = object.__new__(__import__("argon.core.loop", fromlist=["AgentLoop"]).AgentLoop)
    loop._journal_note = lambda text, *, kind: wrote.append((kind, text))

    loop._journal_said("[Scheduled Task] Timer finished.", "cron:abc", origin="cron")

    assert wrote == []


def test_automation_does_not_refresh_the_unsolicited_delivery_target(monkeypatch, tmp_path):
    from argon.config import Config
    from argon.core import target
    from argon.core.bus import MessageBus
    from argon.core.loop import AgentLoop
    from argon.providers.base import GenerationSettings, LLMResponse

    class Provider:
        generation = GenerationSettings()

        async def chat_with_retry(self, **_kwargs):
            return LLMResponse(content="done")

    remembered = []
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    monkeypatch.setattr(target, "remember", lambda *args: remembered.append(args))
    loop = AgentLoop(Config(google={"enabled": False}), MessageBus(), Provider(), register_tools=False)

    import asyncio
    asyncio.run(loop.process_direct("timer fired", origin="cron", background=True))

    assert remembered == []


class TestUndatedFacts:
    def test_a_legacy_fact_round_trips_without_showing_the_sentinel(self):
        """0000-00-00 in every system prompt reads as corruption, model included."""
        facts = parse_facts("- User prefers one-time cron jobs.")

        assert facts[0].learned == UNDATED
        assert facts[0].line() == "- User prefers one-time cron jobs."
        assert parse_facts(facts[0].line())[0].text == "User prefers one-time cron jobs."

    def test_a_dated_fact_keeps_its_date(self):
        fact = Fact(learned="2026-08-01", text="Internship finished.")
        assert fact.line() == "- 2026-08-01 · Internship finished."
        assert parse_facts(fact.line())[0].learned == "2026-08-01"
