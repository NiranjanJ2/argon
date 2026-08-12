"""Memory has to survive being written to every night.

By 2026-08-11, MEMORY.md read:

    2026-08-01 · Math Analysis summer assignments are due 2026-08-16 20:00 PT.
    2026-08-06 · Math Analysis summer assignments are due 2026-08-16 20:00 PT
    2026-08-01 · School resumes 2026-08-12; before then there is no school.
    2026-08-06 · School resumes 2026-08-12
    2026-08-05 · User scheduled to start math homework at 3 PM on 2026-08-05.
    2026-08-06 · User scheduled to start math homework at 3 PM on 2026-08-05

Three facts, each stored twice, differing by a trailing full stop — the exact
dedupe key never matched. Nothing at all from 2026-08-07 onward survived, and
a one-off from the 5th was still being carried with five days left to run.
"""

from __future__ import annotations

from argon.core.journal import (
    Fact,
    _same_fact,
    parse_facts,
    prune,
    render_facts,
)

TODAY = "2026-08-11"


class TestTheDuplicatesItAccumulated:
    def test_a_trailing_full_stop_is_the_same_fact(self):
        assert _same_fact(
            "Math Analysis summer assignments are due 2026-08-16 20:00 PT.",
            "Math Analysis summer assignments are due 2026-08-16 20:00 PT",
        )

    def test_a_restatement_in_fewer_words_is_the_same_fact(self):
        assert _same_fact(
            "School resumes 2026-08-12; before then there is no school.",
            "School resumes 2026-08-12, and before then there is no school",
        )

    def test_the_real_file_collapses_to_five(self):
        facts = parse_facts("""
- 2026-08-01 · Math Analysis summer assignments are due 2026-08-16 20:00 PT. (until 2026-08-17)
- 2026-08-01 · School resumes 2026-08-12; before then there is no school. (until 2026-08-13)
- 2026-08-01 · Dislikes being asked about work that was never assigned - never invent tasks.
- 2026-08-05 · User scheduled to start math homework at 3 PM on 2026-08-05. (until 2026-08-16)
- 2026-08-06 · Math Analysis summer assignments are due 2026-08-16 20:00 PT (until 2026-08-17)
- 2026-08-06 · School resumes 2026-08-12 (until 2026-08-13)
- 2026-08-06 · User scheduled to start math homework at 3 PM on 2026-08-05 (until 2026-08-16)
""")
        kept = prune(facts, TODAY)
        assert len(kept) == 4

    def test_the_later_wording_wins(self):
        """It carries the most recently known expiry."""
        kept = prune([
            Fact("2026-08-01", "School resumes 2026-08-12.", until="2026-08-13"),
            Fact("2026-08-06", "School resumes 2026-08-12", until="2026-08-20"),
        ], TODAY)
        assert len(kept) == 1 and kept[0].until == "2026-08-20"


class TestShortFactsAreNotCollapsed:
    def test_two_numbered_notes_stay_distinct(self):
        """Word overlap on a four-word sentence is meaningless: "fact 1" and
        "fact 28" share every word long enough to count."""
        assert not _same_fact("fact 1", "fact 28")
        assert len(prune([Fact("2026-01-01", "fact {}".format(i)) for i in range(6)], TODAY)) == 6

    def test_a_short_fact_is_not_swallowed_by_a_long_one_containing_it(self):
        """Containment alone would lose it: every word of "SAT prep" appears in
        the longer sentence, so the short standing fact would vanish into a
        one-off note that merely mentions it."""
        assert not _same_fact(
            "SAT prep",
            "He finished SAT prep and started the Chem lab at 5 with Dave.",
        )

    def test_but_identical_short_facts_still_collapse(self):
        assert _same_fact("Gym at 5", "gym at 5.")


class TestStandingFacts:
    def test_a_standing_fact_never_expires(self):
        fact = Fact("2026-01-01", "School days end at 3:40 PM.", until="2026-01-02",
                    standing=True)
        assert fact.expired(TODAY) is False

    def test_it_survives_the_cap_that_drops_the_oldest(self):
        """A one-off deadline must never push out the shape of his week."""
        facts = [Fact("2026-01-01", "School days end at 3:40 PM.", standing=True)]
        facts += [Fact("2026-06-{:02d}".format(d), "one-off {}".format(d)) for d in range(1, 20)]

        kept = prune(facts, TODAY, limit=5)

        assert any(f.standing for f in kept)
        assert len(kept) == 5

    def test_it_round_trips_through_the_file(self):
        original = [Fact("2026-08-11", "He is free after 4 PM on school days.",
                         standing=True)]
        reparsed = parse_facts(render_facts(original))

        assert reparsed[0].standing is True
        assert reparsed[0].text == "He is free after 4 PM on school days."

    def test_a_normal_fact_still_round_trips_with_its_expiry(self):
        original = [Fact("2026-08-11", "Chem test on Friday.", until="2026-08-15")]
        reparsed = parse_facts(render_facts(original))

        assert reparsed[0].standing is False
        assert reparsed[0].until == "2026-08-15"
