"""Things with a history, so a project mentioned weeks later means something.

Memory was a flat list of sentences with expiry dates. That answers "what is
true about him" and cannot answer "what is the Petoi robot" — a project is a
name, a status and a story that accrued. Tell Argon about one on the 3rd,
mention it on the 24th, and there was nothing to look it up in.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from argon.core import threads as threads_mod
from argon.core.threads import DORMANT_DAYS, Threads, ago, slugify

LA = ZoneInfo("America/Los_Angeles")
TODAY = datetime(2026, 8, 11, 17, 0, tzinfo=LA)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(threads_mod.clock, "now", lambda: TODAY)
    monkeypatch.setattr(threads_mod.clock, "today_key", lambda *a, **k: "2026-08-11")
    return Threads(tmp_path)


def _on(store, monkeypatch, day: str):
    """Write as if it were another day."""
    monkeypatch.setattr(threads_mod.clock, "today_key", lambda *a, **k: day)


class TestElapsedTime:
    def test_it_reads_the_way_people_say_it(self):
        assert ago((TODAY - timedelta(days=0)).isoformat(), now=TODAY) == "today"
        assert ago((TODAY - timedelta(days=1)).isoformat(), now=TODAY) == "yesterday"
        assert ago((TODAY - timedelta(days=5)).isoformat(), now=TODAY) == "5 days ago"
        assert ago((TODAY - timedelta(days=21)).isoformat(), now=TODAY) == "3 weeks ago"
        assert ago((TODAY - timedelta(days=90)).isoformat(), now=TODAY) == "3 months ago"

    def test_junk_does_not_raise(self):
        assert ago(None) == "never"
        assert ago("not a date") == "unknown"


class TestRememberingAThing:
    def test_a_project_accrues_a_history(self, store, monkeypatch):
        _on(store, monkeypatch, "2026-07-20")
        store.note("Petoi robot", "Ordered the kit", summary="Quadruped robot.")
        _on(store, monkeypatch, "2026-08-11")
        store.note("Petoi robot", "Got the servos calibrated")

        thread = store.get("Petoi robot")
        assert len(thread.log) == 2
        assert thread.first_seen == "2026-07-20"
        assert thread.last_touched == "2026-08-11"

    def test_it_survives_a_round_trip_to_disk(self, store):
        store.note("UCLA lab", "Joined the sync", summary="Research.",
                   aliases=["the lab"])
        again = Threads(store.dir.parent.parent).get("UCLA lab")

        assert again.summary == "Research."
        assert again.aliases == ["the lab"]
        assert again.log == ["- 2026-08-11 — Joined the sync"]

    def test_the_same_entry_twice_is_recorded_once(self, store):
        store.note("Petoi robot", "Ordered the kit")
        store.note("Petoi robot", "Ordered the kit")
        assert len(store.get("Petoi robot").log) == 1

    def test_a_backdated_entry_does_not_regress_newer_thread_state(self, store):
        store.note(
            "Petoi robot", "Finished calibration", day="2026-08-11",
            summary="Calibrated robot.", status="done",
        )

        store.note(
            "Petoi robot", "Ordered the kit", day="2026-08-01",
            summary="Early project description.", status="active",
        )

        thread = store.get("Petoi robot")
        assert thread.first_seen == "2026-08-01"
        assert thread.last_touched == "2026-08-11"
        assert thread.summary == "Calibrated robot."
        assert thread.status == "done"
        assert thread.log == [
            "- 2026-08-01 — Ordered the kit",
            "- 2026-08-11 — Finished calibration",
        ]

    def test_replaying_a_backdated_entry_is_idempotent(self, store):
        store.note("Petoi robot", "Ordered the kit", day="2026-08-01")
        store.note("Petoi robot", "Finished calibration", day="2026-08-11")

        store.note("Petoi robot", "Ordered the kit", day="2026-08-01")

        assert store.get("Petoi robot").log == [
            "- 2026-08-01 — Ordered the kit",
            "- 2026-08-11 — Finished calibration",
        ]


class TestBringingItUpLater:
    def test_an_alias_finds_it(self, store):
        """He will not say "Petoi robot" three weeks later; he will say "petoi"."""
        store.note("Petoi robot", "Ordered the kit", aliases=["petoi", "the robot"])

        assert store.mentioned("hows the petoi going")
        assert store.mentioned("did i finish the robot")

    def test_a_word_that_merely_contains_the_name_does_not(self, store):
        """Substring matching would fire "SAT prep" on "saturday", and wrong
        retrieved context is worse than none."""
        store.note("SAT prep", "Started English")

        assert store.mentioned("what am i doing saturday") == []

    def test_an_unrelated_message_pulls_nothing(self, store):
        store.note("Petoi robot", "Ordered the kit")
        assert store.recall("what's for dinner") == ""

    def test_recall_gives_the_whole_story(self, store, monkeypatch):
        _on(store, monkeypatch, "2026-07-20")
        store.note("Petoi robot", "Ordered the kit", summary="Quadruped robot.")
        _on(store, monkeypatch, "2026-08-11")
        store.note("Petoi robot", "Servos calibrated")

        text = store.recall("back to the petoi", now=TODAY)
        assert "Quadruped robot." in text
        assert "Ordered the kit" in text and "Servos calibrated" in text
        assert "3 weeks ago" in text  # started; the elapsed time is the point


class TestTheAlwaysOnIndex:
    def test_live_threads_are_listed_with_their_age(self, store):
        store.note("Petoi robot", "x", summary="Quadruped robot.")
        line = store.index(now=TODAY)

        assert "Petoi robot" in line and "last touched today" in line

    def test_a_finished_thread_drops_out(self, store):
        store.note("Petoi robot", "x")
        store.note("Petoi robot", status="done")
        assert store.index(now=TODAY) == ""

    def test_a_dormant_thread_drops_out_but_is_still_findable(self, store, monkeypatch):
        """It stops taking up room in every prompt; it does not stop existing."""
        old = (TODAY - timedelta(days=DORMANT_DAYS + 10)).strftime("%Y-%m-%d")
        _on(store, monkeypatch, old)
        store.note("Old science fair", "Built the poster", aliases=["science fair"])

        assert store.index(now=TODAY) == ""
        assert store.get("Old science fair") is not None
        assert store.mentioned("remember the science fair")

    def test_nothing_tracked_is_an_empty_string_not_a_heading(self, store):
        assert store.index(now=TODAY) == ""


class TestSlugs:
    def test_names_become_filenames_safely(self):
        assert slugify("Petoi robot") == "petoi-robot"
        assert slugify("AP Chem / Period 3") == "ap-chem-period-3"
        assert slugify("  spaces  ") == "spaces"


class TestDistinctiveWords:
    def test_one_distinctive_word_is_enough(self, store):
        """He says "the petoi", not "Petoi robot", and that has to work without
        anyone having thought to add an alias."""
        store.note("Petoi robot", "Ordered the kit")
        assert store.mentioned("back to the petoi this week")

    def test_a_generic_word_alone_is_not(self, store):
        """"robot" and "prep" are in half his projects and identify none."""
        store.note("Petoi robot", "x")
        store.note("SAT prep", "y")

        assert store.mentioned("i need to do some prep") == []
        assert store.mentioned("robot stuff tonight") == []

    def test_the_full_name_still_matches_a_generic_one(self, store):
        store.note("Math homework", "x")
        assert store.mentioned("did my math homework")

    def test_short_words_need_the_full_name(self, store):
        """Three-letter fragments would match half of English."""
        store.note("AP Chem", "x")
        assert store.mentioned("the ap") == []
        assert store.mentioned("ap chem tonight")
