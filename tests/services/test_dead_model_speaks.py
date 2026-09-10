"""The model can die; the board still has to go out.

gpt-oss-120b was retired on 09/03. Chat used a different model and kept working,
so nothing looked broken, while every check-in for a week returned a 410 that was
logged as a WARNING and dropped. An assignment went past. These pin the three
things that had to line up for that to happen.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from argon.providers.openai_compat import OpenAICompatProvider
from argon.services import reminder as reminder_mod
from argon.services.reminder import ReminderService

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 10, 16, 30, tzinfo=LA)

GONE = (
    '{"type":"about:blank","title":"Gone","status":410,"detail":"The model '
    "'openai/gpt-oss-120b' has reached its end of life on 2026-09-03T08:00:00Z "
    'and is no longer available."}'
)


class _ProviderError(Exception):
    def __init__(self, msg: str, status: int | None = None) -> None:
        super().__init__(msg)
        self.status_code = status


class TestRetiredModelIsRecognised:
    def test_a_410_counts_as_unavailable(self):
        assert OpenAICompatProvider._is_model_unavailable(_ProviderError(GONE, 410))

    def test_the_wording_alone_is_enough_without_a_status(self):
        # "no longer available" does not contain "not available" — the old
        # substring list missed it even after the status check missed too.
        assert OpenAICompatProvider._is_model_unavailable(_ProviderError(GONE))

    def test_an_ordinary_outage_is_not_a_dead_model(self):
        # A 504 must keep retrying the primary rather than burn the fallback.
        assert not OpenAICompatProvider._is_model_unavailable(_ProviderError("gateway timeout", 504))


class TestTheBoardStillGoesOut:
    @pytest.fixture
    def service(self, tmp_path, monkeypatch):
        sent: list[str] = []

        async def deliver(text, key=None, actions=None):
            sent.append(text)
            return True

        svc = ReminderService(
            tmp_path,
            "America/Los_Angeles",
            on_check_in=lambda _p: _fail(),
            on_deliver=deliver,
        )
        monkeypatch.setattr(
            reminder_mod, "plain_board", lambda *a, **k: "Argon's model is unreachable:\n- APUSH - Key terms"
        )
        return svc, sent

    async def test_one_failure_stays_quiet(self, service):
        svc, sent = service
        await svc._unworded_fallback(NOW)
        assert sent == []

    async def test_a_second_failure_sends_the_board(self, service):
        svc, sent = service
        await svc._unworded_fallback(NOW)
        await svc._unworded_fallback(NOW)
        assert len(sent) == 1 and "Key terms" in sent[0]

    async def test_it_does_not_repeat_all_day(self, service):
        svc, sent = service
        for _ in range(8):
            await svc._unworded_fallback(NOW)
        assert len(sent) == 1

    async def test_a_new_day_speaks_again(self, service):
        svc, sent = service
        for _ in range(3):
            await svc._unworded_fallback(NOW)
        await svc._unworded_fallback(NOW.replace(day=11))
        assert len(sent) == 2

    async def test_an_unreadable_board_still_raises_the_alarm(self, tmp_path, monkeypatch):
        sent: list[str] = []

        async def deliver(text, key=None, actions=None):
            sent.append(text)
            return True

        svc = ReminderService(
            tmp_path, "America/Los_Angeles", on_check_in=lambda _p: _fail(), on_deliver=deliver
        )
        monkeypatch.setattr(reminder_mod, "plain_board", _boom)

        await svc._unworded_fallback(NOW)
        await svc._unworded_fallback(NOW)

        # Silence is the failure mode being fixed; a broken board must not
        # reintroduce it.
        assert len(sent) == 1 and "unreachable" in sent[0]


def _boom(*_a, **_k):
    raise RuntimeError("board is broken")


async def _fail():
    return "Error: " + GONE
