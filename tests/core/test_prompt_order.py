"""Prompt order is a cost and latency decision.

Providers cache on an exact prefix, so the first byte that differs from last
turn throws away everything after it. Memory sat in the middle of the prompt
and the thread recall inside it keys off whatever he just said — so every
message invalidated the skills below it. Measured hit rate was 24.9% across
173 real calls, on a budget of five dollars a month where the whole lever is
prompt tokens.
"""

from __future__ import annotations

import pytest

from argon.core.context import ContextBuilder


@pytest.fixture
def builder(tmp_path):
    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    (tmp_path / "SOUL.md").write_text("# Argon\n\nYou are Argon." + "\nfiller." * 200)
    (tmp_path / "AGENTS.md").write_text("# Rules\n\nUse tools quietly." + "\nfiller." * 200)
    return ContextBuilder(tmp_path, "America/Los_Angeles")


def _shared_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class TestTheStablePartComesFirst:
    def test_two_different_messages_share_almost_all_of_the_prompt(self, builder):
        from argon.core.threads import Threads

        Threads(builder.workspace).note("Petoi robot", "Ordered the kit",
                                        summary="Quadruped robot.")

        a = builder.build_system_prompt(recent="what's due today")
        b = builder.build_system_prompt(recent="how's the petoi going")

        assert _shared_prefix(a, b) / len(a) > 0.75

    def test_the_persona_is_never_after_something_volatile(self, builder):
        """SOUL.md and the skills are the bulk and never change; anything that
        moves must come after them or their cache entry is thrown away."""
        from argon.core.threads import Threads

        Threads(builder.workspace).note("Petoi robot", "Ordered the kit")
        prompt = builder.build_system_prompt(recent="how's the petoi going")

        assert prompt.index("You are Argon.") < prompt.index("# Memory")

    def test_recall_is_the_last_thing_in_the_prompt(self, builder):
        from argon.core.threads import Threads

        Threads(builder.workspace).note("Petoi robot", "Ordered the kit")
        prompt = builder.build_system_prompt(recent="the petoi")

        assert "Brought up just now" in prompt
        # Nothing stable may follow the per-message block.
        assert "# Skills" not in prompt.split("Brought up just now")[1]
