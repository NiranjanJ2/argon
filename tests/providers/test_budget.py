"""The five-dollar ceiling.

Hardcoded on purpose: a cap that lives in config is one bad migration away from
being no cap at all.
"""

from __future__ import annotations

import pytest

from argon.providers import budget


@pytest.fixture(autouse=True)
def _store(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    from argon.core import store

    store.reset_for_tests()
    yield
    store.reset_for_tests()


def test_unmetered_models_are_free_and_never_blocked():
    """NIM and Groq are prepaid. The cap exists to stop an OpenAI bill running
    away, not to ration endpoints already paid for."""
    assert budget.cost_of("openai/gpt-oss-120b", 1_000_000, 100_000) == 0.0
    assert budget.record("nvidia/nemotron-3-super-120b-a12b", 5_000_000, 500_000) == 0.0

    budget.check("openai/gpt-oss-120b")   # must not raise
    assert budget.spent() == 0.0


def test_luna_is_priced_from_the_published_rates():
    # 1M in, 1M out, no cache = 0.20 + 1.20
    assert budget.cost_of("gpt-5.6-luna", 1_000_000, 1_000_000) == pytest.approx(1.40)
    # cached input is a tenth of fresh input
    assert budget.cost_of("gpt-5.6-luna", 1_000_000, 0, 1_000_000) == pytest.approx(0.02)


def test_spend_accumulates_and_then_the_cap_bites():
    for _ in range(3):
        budget.record("gpt-5.6-luna", 1_000_000, 1_000_000)   # $1.40 each
    assert budget.spent() == pytest.approx(4.20)
    budget.check("gpt-5.6-luna")   # still under; must not raise

    budget.record("gpt-5.6-luna", 1_000_000, 0)               # +$0.20 -> 4.40
    budget.record("gpt-5.6-luna", 1_000_000, 1_000_000)       # +$1.40 -> 5.80
    assert budget.spent() > budget.MONTHLY_CAP_USD

    with pytest.raises(budget.SpendCapError):
        budget.check("gpt-5.6-luna")


def test_the_refusal_routes_through_the_standby_path():
    """`_is_provider_refusal` matches on "spend". Going over must degrade to the
    fallback provider, not take Argon off the air."""
    from argon.providers.openai_compat import OpenAICompatProvider

    budget.record("gpt-5.6-luna", 5_000_000, 5_000_000)
    try:
        budget.check("gpt-5.6-luna")
        raise AssertionError("expected the cap to bite")
    except budget.SpendCapError as exc:
        assert OpenAICompatProvider._is_provider_refusal(exc)


def test_the_cap_is_per_calendar_month(monkeypatch):
    budget.record("gpt-5.6-luna", 5_000_000, 5_000_000)
    assert budget.spent() > budget.MONTHLY_CAP_USD

    monkeypatch.setattr(budget, "_month", lambda: "2099-01")
    assert budget.spent() == 0.0
    budget.check("gpt-5.6-luna")   # a new month starts clean


class TestReasoningEffortIsPerEndpoint:
    """One global default, two providers that disagree about the vocabulary.

    gpt-5.6-luna *requires* reasoning_effort="none" to use function tools; NIM
    rejects "none" with a 400 whose text reached Niranjan as the message. An
    unsupported value has to be dropped, not sent.
    """

    def _kwargs(self, provider_name, effort):
        from argon.providers.openai_compat import OpenAICompatProvider
        from argon.providers.registry import find_by_name

        p = OpenAICompatProvider.__new__(OpenAICompatProvider)
        p._spec = find_by_name(provider_name)
        p.default_model = "m"
        p.fallback_model = None
        p.extra_headers = None
        return OpenAICompatProvider._build_kwargs(
            p, [{"role": "user", "content": "hi"}], None, None, 100, 0.1, effort, None
        )

    def test_nim_never_receives_none(self):
        assert "reasoning_effort" not in self._kwargs("nim", "none")

    def test_nim_still_receives_what_it_supports(self):
        assert self._kwargs("nim", "medium")["reasoning_effort"] == "medium"

    def test_openai_receives_none_because_luna_requires_it(self):
        assert self._kwargs("openai", "none")["reasoning_effort"] == "none"
