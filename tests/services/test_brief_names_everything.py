"""The brief names every item, because on 09/09 it named none of them.

Five things were due. The prompt handed the model the whole board and then
asked for "two or three items". Three APUSH assignments went out as the word
"APUSH", Chapter 5 Key Terms was never named, and it was never turned in.

Choosing what to mention is not a judgement worth delegating.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from argon.services import reminder as reminder_mod
from argon.services.reminder import OCCASIONS, ReminderService, board_digest

LA = ZoneInfo("America/Los_Angeles")


def _row(title, due, subject=None, days_overdue=None):
    """A real Commitment, so this fake cannot drift from the shape the code reads."""
    from argon.commitments import Commitment

    return Commitment(
        key=title, title=title, origin="classroom", subject=subject,
        official_due=due, official_due_when=due[:10], days_overdue=days_overdue,
    )


class _Health:
    ok, error, warnings = True, None, ()

    def __init__(self, name="classroom"):
        self.name, self.label, self.complete = name, name, True


class _Board:
    def __init__(self, rows):
        self.commitments = rows

    # `build_prompt` reads the board through these on its way to the model.
    def as_dicts(self):
        return [
            {"title": c.title, "days_overdue": c.days_overdue, "source": "classroom"}
            for c in self.commitments
        ]

    def source(self, name):
        return _Health(name)

    def health_lines(self):
        return []


@pytest.fixture
def board(monkeypatch):
    today = date.today()
    rows = [
        _row("Chapter 5 Key Terms ", today.isoformat() + "T23:59:00-07:00", "APUSH PM"),
        _row("Reminer:  Progress checks due tonight", today.isoformat(), "APUSH PM"),
        _row("Reminder:  Study for Midterm ", today.isoformat(), "APUSH PM"),
        _row("Max Height", today.isoformat(), "Physics"),
        _row("Codecademy 4: Functions", (today + timedelta(days=1)).isoformat(), "AI"),
        _row("Study for Ch 3 Quiz", (today - timedelta(days=14)).isoformat(), "APUSH PM", 14),
        _row("Next week's essay", (today + timedelta(days=5)).isoformat(), "Lang"),
    ]
    monkeypatch.setattr("argon.commitments.load_board", lambda *a, **k: _Board(rows))
    return rows


def test_every_due_item_is_named(board, tmp_path):
    digest = board_digest(tmp_path, LA)

    # The one that was missed, by name - not folded into its subject.
    assert "Chapter 5 Key Terms" in digest
    for title in ("Progress checks", "Study for Midterm", "Max Height", "Codecademy"):
        assert title in digest, title


def test_overdue_says_how_late(board, tmp_path):
    assert "14d late" in board_digest(tmp_path, LA)


def test_tonight_and_tomorrow_are_distinguished(board, tmp_path):
    digest = board_digest(tmp_path, LA)

    assert "tonight - Physics: Max Height" in digest
    # Due 00:01 tomorrow is tonight's work; it must not be silently dropped.
    assert "tomorrow - AI: Codecademy 4: Functions" in digest


def test_next_week_is_not_tonights_problem(board, tmp_path):
    assert "Next week's essay" not in board_digest(tmp_path, LA)


def test_an_empty_board_produces_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("argon.commitments.load_board", lambda *a, **k: _Board([]))

    assert board_digest(tmp_path, LA) == ""


class TestItSurvivesTheModel:
    @pytest.fixture
    def service(self, tmp_path, monkeypatch):
        sent: list[str] = []

        async def deliver(text, key=None, actions=None):
            sent.append(text)
            return True

        async def speak(_prompt: str) -> str:
            return "THINKING: ok\nMESSAGE: Tonight is mostly APUSH."

        svc = ReminderService(
            tmp_path, "America/Los_Angeles", on_check_in=speak, on_deliver=deliver
        )
        # The brief is the occasion under test; which occasion fires is
        # `pick_occasion`'s own concern and covered elsewhere.
        monkeypatch.setattr(svc, "pick_occasion", lambda: OCCASIONS["daily_brief"])
        return svc, sent

    async def test_the_list_is_appended_to_whatever_the_model_wrote(
        self, service, board, monkeypatch
    ):
        svc, sent = service
        monkeypatch.setattr(reminder_mod, "board_digest", lambda *a, **k: "- tonight - APUSH PM: Chapter 5 Key Terms")

        await svc.tick()

        assert len(sent) == 1
        # The model's framing survives...
        assert "Tonight is mostly APUSH." in sent[0]
        # ...and so does the item it did not name.
        assert "Chapter 5 Key Terms" in sent[0]

    async def test_a_broken_board_still_sends_the_framing(
        self, service, monkeypatch
    ):
        svc, sent = service

        def boom(*a, **k):
            raise RuntimeError("board is down")

        monkeypatch.setattr(reminder_mod, "board_digest", boom)

        await svc.tick()

        assert len(sent) == 1 and "Tonight is mostly APUSH." in sent[0]
