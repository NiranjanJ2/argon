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

from argon.core.journal import UNDATED, Fact, Journal, describe_tool, parse_facts


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
