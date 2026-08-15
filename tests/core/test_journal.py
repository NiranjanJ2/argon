"""Day-scoped memory and end-of-day pruning.

The system this replaces asked the model to rewrite MEMORY.md in full whenever
context got tight. A small model handed "return the full updated file" returns
a short one, so every rewrite quietly dropped what came before: after months of
use MEMORY.md was two lines and HISTORY.md was 8MB of repeated API errors.
Nothing here ever asks a model to rewrite a file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argon.core import journal as journal_mod
from argon.core.journal import Fact, Journal, parse_facts, prune, render_facts
from argon.core.threads import Threads


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))


def _journal(tmp_path) -> Journal:
    return Journal(tmp_path)


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------


def test_a_fact_round_trips():
    facts = parse_facts(render_facts([Fact("2026-08-02", "Internship ended.")]))
    assert facts[0].text == "Internship ended."
    assert facts[0].learned == "2026-08-02"


def test_an_expiry_round_trips():
    original = Fact("2026-08-02", "Rest day.", until="2026-08-03")
    assert parse_facts(render_facts([original]))[0].until == "2026-08-03"


def test_an_unrecognised_line_is_kept_not_lost():
    """Losing a hand-written note would repeat the original failure."""
    facts = parse_facts("# Memory\n\n- some older freeform note\n")
    assert len(facts) == 1
    assert "older freeform note" in facts[0].text


def test_expired_facts_are_dropped():
    facts = [
        Fact("2026-07-01", "Internship runs through July.", until="2026-07-31"),
        Fact("2026-07-01", "Lives in Cerritos."),
    ]
    kept = prune(facts, "2026-08-02")
    assert [f.text for f in kept] == ["Lives in Cerritos."]


def test_an_unexpired_fact_survives():
    facts = [Fact("2026-08-01", "Rest day.", until="2026-08-31")]
    assert len(prune(facts, "2026-08-02")) == 1


def test_duplicates_collapse():
    facts = [Fact("2026-08-01", "Prefers Discord."), Fact("2026-08-02", "prefers discord.")]
    assert len(prune(facts, "2026-08-02")) == 1


def test_memory_is_capped_keeping_the_newest():
    facts = [Fact(f"2026-01-{i:02d}", f"fact {i}") for i in range(1, 29)]
    kept = prune(facts, "2026-08-02", limit=5)
    assert len(kept) == 5
    assert kept[-1].text == "fact 28"


# ---------------------------------------------------------------------------
# Day pages
# ---------------------------------------------------------------------------


def test_notes_append_rather_than_replace(tmp_path):
    j = _journal(tmp_path)
    j.note("Last day of the internship.")
    j.note("Tomorrow is a rest day.")
    day = j.read_day()
    assert "Last day of the internship." in day
    assert "Tomorrow is a rest day." in day


def test_the_journal_reaches_the_prompt_context(tmp_path):
    j = _journal(tmp_path)
    j.add_fact("Lives in Cerritos.")
    j.note("Tomorrow is a rest day.")
    context = j.context()
    assert "Lives in Cerritos." in context
    assert "Tomorrow is a rest day." in context


def test_a_lasting_fact_is_stored_immediately(tmp_path):
    j = _journal(tmp_path)
    j.add_fact("Prefers Discord.")
    assert any(f.text == "Prefers Discord." for f in Journal(tmp_path).facts())


def test_an_empty_note_is_ignored(tmp_path):
    j = _journal(tmp_path)
    j.note("   ")
    assert j.read_day() == ""


def test_old_day_pages_are_swept(tmp_path):
    j = _journal(tmp_path)
    (j.days / "2020-01-01.md").write_text("- 09:00 [note] ancient\n")
    j.note("today")
    assert j.sweep_old_days(keep=30) == 1
    assert j.read_day() != ""


def test_pending_day_ignores_today(tmp_path, monkeypatch):
    j = _journal(tmp_path)
    j.note("today")
    assert j.pending_day() is None


def test_pending_day_finds_an_unconsolidated_past_day(tmp_path):
    j = _journal(tmp_path)
    (j.days / "2026-01-01.md").write_text("- 09:00 [note] something\n")
    assert j.pending_day() == "2026-01-01"
    j.mark_consolidated("2026-01-01")
    assert j.pending_day() is None


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------


class _Call:
    def __init__(self, arguments):
        self.name = "carry_forward"
        self.arguments = arguments


class _Response:
    def __init__(self, arguments):
        self.has_tool_calls = True
        self.tool_calls = [_Call(arguments)]


class _Provider:
    def __init__(self, arguments):
        self._arguments = arguments
        self.calls = 0

    async def chat_with_retry(self, **kwargs):
        self.calls += 1
        return _Response(self._arguments)


class _Silent:
    async def chat_with_retry(self, **kwargs):
        response = _Response({})
        response.has_tool_calls = False
        return response


async def test_consolidation_carries_facts_forward(tmp_path):
    j = _journal(tmp_path)
    (j.days / "2026-08-01.md").write_text("- 14:00 [said] Last day of the internship.\n")
    provider = _Provider({"keep": [{"fact": "Internship ended on 2026-07-31."}]})

    added, dropped = await journal_mod.consolidate_day(j, provider, "m", "2026-08-01")

    assert added == 1
    assert any("Internship ended" in f.text for f in Journal(tmp_path).facts())
    assert j.pending_day() is None


async def test_consolidation_drops_what_is_finished(tmp_path):
    j = _journal(tmp_path)
    j.add_fact("Internship runs through July.")
    (j.days / "2026-08-01.md").write_text("- 14:00 [said] Internship is over.\n")
    provider = _Provider({"keep": [], "drop": ["Internship runs through July."]})

    _, dropped = await journal_mod.consolidate_day(j, provider, "m", "2026-08-01")

    assert dropped == 1
    assert not Journal(tmp_path).facts()


async def test_a_silent_model_never_erases_memory(tmp_path):
    """The old design lost everything exactly here."""
    j = _journal(tmp_path)
    j.add_fact("Lives in Cerritos.")
    (j.days / "2026-08-01.md").write_text("- 14:00 [said] whatever\n")

    await journal_mod.consolidate_day(j, _Silent(), "m", "2026-08-01")

    assert [f.text for f in Journal(tmp_path).facts()] == ["Lives in Cerritos."]


async def test_an_empty_day_costs_no_model_call(tmp_path):
    j = _journal(tmp_path)
    (j.days / "2026-08-01.md").write_text("")
    provider = _Provider({"keep": []})

    await journal_mod.consolidate_day(j, provider, "m", "2026-08-01")

    assert provider.calls == 0
    assert j.pending_day() is None


async def test_a_bogus_expiry_is_ignored_not_fatal(tmp_path):
    j = _journal(tmp_path)
    (j.days / "2026-08-01.md").write_text("- 14:00 [said] something\n")
    provider = _Provider({"keep": [{"fact": "A thing.", "until": "next tuesday"}]})

    await journal_mod.consolidate_day(j, provider, "m", "2026-08-01")

    assert Journal(tmp_path).facts()[0].until is None


async def test_consolidation_rejects_relative_day_facts(tmp_path):
    j = _journal(tmp_path)
    (j.days / "2026-08-01.md").write_text("- 14:00 [said] rest day\n")
    provider = _Provider({"keep": [{"fact": "Tomorrow is a rest day."}]})

    added, _ = await journal_mod.consolidate_day(j, provider, "m", "2026-08-01")

    assert added == 0
    assert Journal(tmp_path).facts() == []


async def test_nightly_thread_entries_keep_the_day_being_consolidated(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    j = _journal(tmp_path)
    (j.days / "2026-08-11.md").write_text("- 14:00 [said] Petoi update\n")
    provider = _Provider({"keep": [], "threads": [{
        "name": "Petoi robot", "entry": "Ordered the kit", "aliases": ["petoi"],
    }]})

    await journal_mod.consolidate_day(j, provider, "m", "2026-08-11")

    thread = Threads(tmp_path).get("Petoi robot")
    assert thread.first_seen == "2026-08-11"
    assert thread.last_touched == "2026-08-11"
    assert thread.log == ["- 2026-08-11 — Ordered the kit"]


async def test_nightly_thread_reuses_an_existing_alias(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    j = _journal(tmp_path)
    Threads(tmp_path).note("Chemistry", "Read chapter one", aliases=["chem"])
    (j.days / "2026-08-11.md").write_text("- 14:00 [said] Chem update\n")
    provider = _Provider({"keep": [], "threads": [{
        "name": "AP Chemistry", "entry": "Started the lab", "aliases": ["chem"],
    }]})

    await journal_mod.consolidate_day(j, provider, "m", "2026-08-11")

    threads = Threads(tmp_path).all()
    assert len(threads) == 1
    assert threads[0].name == "Chemistry"
    assert "AP Chemistry" in threads[0].aliases


def test_the_legacy_history_dump_is_archived_not_deleted(tmp_path):
    root = tmp_path / "memory"
    root.mkdir(parents=True)
    (root / "HISTORY.md").write_text("8MB of API errors, notionally")
    (root / "MEMORY.md").write_text("# MEMORY\n\n- User prefers one-time cron jobs.\n")

    target = journal_mod.migrate_legacy(tmp_path)

    assert target is not None
    assert Path(target).exists()
    assert not (root / "HISTORY.md").exists()
    # The one surviving fact is preserved through the migration.
    assert any("cron" in f.text for f in Journal(tmp_path).facts())


def test_days_are_consolidated_oldest_first(tmp_path, monkeypatch):
    """It took the newest pending day and marked it done, so an older one was
    stepped over and could never be reached again."""
    from argon.core import journal as journal_mod
    from argon.core.journal import Journal

    monkeypatch.setattr(journal_mod.clock, "today_key", lambda *a, **k: "2026-08-11")
    j = Journal(tmp_path)
    for day in ("2026-08-08", "2026-08-09", "2026-08-10"):
        j.note("something", day=day)

    assert j.pending_day() == "2026-08-08"
    j.mark_consolidated("2026-08-08")
    assert j.pending_day() == "2026-08-09"
    j.mark_consolidated("2026-08-09")
    assert j.pending_day() == "2026-08-10"
    j.mark_consolidated("2026-08-10")
    assert j.pending_day() is None
