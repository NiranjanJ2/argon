"""Turning off check-ins used to turn off remembering.

End-of-day consolidation was handed to ReminderService as an `on_day_rollover`
callback and ran inside its tick loop — but `ReminderService.start()` returns
early when check-ins are disabled. So the one switch a person reaches for when
Argon is talking too much also stopped yesterday's journal from ever reaching
MEMORY.md and stopped old day pages from being swept. Nothing said so: the cost
showed up weeks later as an assistant that remembered nothing he had told it.

Maintenance is not part of the unsolicited-message policy, so it does not live
behind its switch. These drive the tick directly; nothing here sleeps.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from argon.core import journal as journal_mod
from argon.core.journal import Journal
from argon.services.maintenance import MaintenanceService
from argon.services.reminder import ReminderService


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))


class _Call:
    def __init__(self, arguments):
        self.name = "carry_forward"
        self.arguments = arguments


class _Response:
    def __init__(self, arguments):
        self.has_tool_calls = True
        self.tool_calls = [_Call(arguments)]


class _Provider:
    """The nightly model, stubbed. Counts calls so a repeat tick is visible."""

    def __init__(self, arguments):
        self._arguments = arguments
        self.calls = 0

    async def chat_with_retry(self, **kwargs):
        self.calls += 1
        return _Response(self._arguments)


def _service(workspace, provider) -> MaintenanceService:
    async def consolidate(journal, day):
        await journal_mod.consolidate_day(journal, provider, "nightly-model", day)

    return MaintenanceService(workspace=workspace, on_consolidate=consolidate)


async def _never_called(_prompt: str) -> str:  # pragma: no cover - guards the test
    raise AssertionError("check-ins are disabled; nothing should ask for a message")


async def test_yesterday_is_consolidated_even_with_check_ins_switched_off(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(journal_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    checkins = ReminderService(
        workspace=tmp_path,
        timezone="America/Los_Angeles",
        on_check_in=_never_called,
        enabled=False,
    )
    await checkins.start()
    # The loop that used to carry consolidation is not running at all.
    assert checkins._task is None

    Journal(tmp_path).note("Chem test moved to 2026-08-19.", day="2026-08-11")
    provider = _Provider({"keep": [{"fact": "The chem test is on 2026-08-19."}]})

    assert await _service(tmp_path, provider).tick() == "2026-08-11"

    assert [f.text for f in Journal(tmp_path).facts()] == ["The chem test is on 2026-08-19."]


async def test_ticking_twice_does_not_fold_the_same_day_in_again(tmp_path, monkeypatch):
    """A second pass over a day already folded in would put the same fact in
    MEMORY.md twice, which is how it filled with near-duplicates before."""
    monkeypatch.setattr(journal_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    Journal(tmp_path).note("Practice moved to 2026-08-18.", day="2026-08-11")
    provider = _Provider({"keep": [{"fact": "Water polo practice is on 2026-08-18."}]})
    service = _service(tmp_path, provider)

    assert await service.tick() == "2026-08-11"
    assert await service.tick() is None

    assert provider.calls == 1
    assert [f.text for f in Journal(tmp_path).facts()] == ["Water polo practice is on 2026-08-18."]


async def test_day_pages_are_still_swept_when_check_ins_never_run(tmp_path, monkeypatch):
    monkeypatch.setattr(journal_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    monkeypatch.setattr(
        journal_mod.clock, "now",
        lambda: datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc),
    )
    journal = Journal(tmp_path)
    stale = journal.day_path("2026-01-02")
    stale.write_text("- 09:00 [note] months old\n", encoding="utf-8")
    journal.mark_consolidated("2026-01-02")

    assert await _service(tmp_path, _Provider({"keep": []})).tick() is None

    assert not stale.exists()


async def test_a_failed_fold_leaves_the_day_pending_instead_of_losing_it(
    tmp_path, monkeypatch
):
    """A provider outage at 4 AM must cost a retry, not a day of his life."""
    monkeypatch.setattr(journal_mod.clock, "today_key", lambda *a, **k: "2026-08-12")
    Journal(tmp_path).note("Something worth keeping.", day="2026-08-11")

    async def explode(_journal, _day):
        raise RuntimeError("provider is down")

    service = MaintenanceService(workspace=tmp_path, on_consolidate=explode)

    assert await service.tick() is None
    assert Journal(tmp_path).pending_day() == "2026-08-11"
