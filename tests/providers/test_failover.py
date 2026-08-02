"""Provider failover.

Groq blocked the org on a spend alert in May 2026 and Argon stopped answering
for months — the only trace was `spend_limit_reached` repeated in a log nobody
read. Degrading to a slower provider beats going silent.
"""

from __future__ import annotations

import pytest

from argon.config import AgentDefaults, AgentsConfig, Config, ProviderConfig
from argon.providers.openai_compat import OpenAICompatProvider
from argon.runtime import build_provider


class _Boom(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status


@pytest.mark.parametrize("error", [
    _Boom("Organization has blocked API access because a spend alert threshold was met"),
    _Boom("rate limit exceeded"),
    _Boom("insufficient quota"),
    _Boom("Service temporarily overloaded"),
    _Boom("nope", status=429),
    _Boom("nope", status=402),
])
def test_a_provider_refusal_is_recognised(error):
    assert OpenAICompatProvider._is_provider_refusal(error) is True


@pytest.mark.parametrize("error", [
    _Boom("model not found", status=404),
    _Boom("invalid tool schema", status=400),
])
def test_an_ordinary_error_is_not_a_refusal(error):
    """Failing over on a malformed request would just break twice."""
    assert OpenAICompatProvider._is_provider_refusal(error) is False


def _config(fallback: str | None = "nim") -> Config:
    return Config(
        agents=AgentsConfig(defaults=AgentDefaults(
            provider="groq", fallback_provider=fallback, model="openai/gpt-oss-120b",
        )),
        providers={
            "groq": ProviderConfig(api_key="g-key"),
            "nim": ProviderConfig(api_key="n-key"),
        },
    )


def test_the_standby_is_attached_and_points_elsewhere():
    provider = build_provider(_config())
    assert provider.standby is not None
    assert provider.api_base != provider.standby.api_base


def test_the_standby_has_no_standby_of_its_own():
    """Guards against building providers forever."""
    assert build_provider(_config()).standby.standby is None


def test_no_standby_when_none_is_configured():
    assert build_provider(_config(fallback=None)).standby is None


def test_no_standby_when_it_lacks_credentials():
    config = _config()
    config.providers["nim"] = ProviderConfig()
    assert build_provider(config).standby is None


def test_a_provider_is_never_its_own_standby():
    config = _config(fallback="groq")
    assert build_provider(config).standby is None


async def test_chat_fails_over_to_the_standby(monkeypatch):
    provider = build_provider(_config())
    calls: list[str] = []

    async def refuse(**kwargs):
        calls.append("primary")
        raise _Boom("spend limit reached")

    async def answer(*args, **kwargs):
        calls.append("standby")
        return "recovered"

    monkeypatch.setattr(provider._client.chat.completions, "create", refuse)
    monkeypatch.setattr(provider.standby, "chat", answer)

    assert await provider.chat([{"role": "user", "content": "hi"}]) == "recovered"
    assert calls == ["primary", "standby"]


async def test_an_ordinary_error_does_not_reach_the_standby(monkeypatch):
    provider = build_provider(_config())
    reached: list[bool] = []

    async def bad_request(**kwargs):
        raise _Boom("invalid tool schema", status=400)

    async def standby_chat(*args, **kwargs):
        reached.append(True)
        return "should not happen"

    monkeypatch.setattr(provider._client.chat.completions, "create", bad_request)
    monkeypatch.setattr(provider.standby, "chat", standby_chat)

    try:
        await provider.chat([{"role": "user", "content": "hi"}])
    except Exception:
        pass
    assert reached == []
