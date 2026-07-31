"""Normalising whatever the model calls the lockdown state.

The tool schema deliberately has no enum, because models kept failing the
constrained call. Instead every string is coerced: anything mentioning "unlock"
releases the phone, everything else locks it. Locking down on an ambiguous word
is the safe direction — it is recoverable from the phone itself.
"""

from __future__ import annotations

import pytest

from argon.config import LockdownConfig
from argon.tools.lockdown import SendPhoneNotificationTool, normalize_state


@pytest.mark.parametrize(
    "raw",
    ["unlock", "Unlock", "UNLOCK", "  unlock  ", "unlock the phone", "please unlock", "unlocked"],
)
def test_anything_mentioning_unlock_unlocks(raw):
    assert normalize_state(raw) == "unlock"


@pytest.mark.parametrize(
    "raw",
    ["lockdown", "LOCKDOWN", "Lockdown", "lock", "lock it", "  lock it down ", "on", "restrict"],
)
def test_everything_else_locks_down(raw):
    assert normalize_state(raw) == "lockdown"


def test_empty_and_garbage_default_to_lockdown():
    assert normalize_state("") == "lockdown"
    assert normalize_state("   ") == "lockdown"
    assert normalize_state("¯\\_(ツ)_/¯") == "lockdown"


async def test_tool_reports_missing_configuration_instead_of_sending():
    tool = SendPhoneNotificationTool(LockdownConfig())
    result = await tool.execute(notification="lockdown")
    assert "not configured" in result


async def test_tool_normalises_before_sending(monkeypatch):
    sent: list[str] = []

    def fake_send(config, state):
        sent.append(state)
        return f"Trigger '{state.upper()}' sent to phone."

    monkeypatch.setattr("argon.tools.lockdown.send_trigger", fake_send)
    tool = SendPhoneNotificationTool(LockdownConfig(email="a@b.c", password="p", phone="5551234"))

    assert "UNLOCK" in await tool.execute(notification="Unlock it please")
    assert "LOCKDOWN" in await tool.execute(notification="LOCK IT DOWN")
    assert sent == ["unlock", "lockdown"]


async def test_tool_defaults_to_lockdown_when_the_model_omits_the_argument(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        "argon.tools.lockdown.send_trigger",
        lambda config, state: sent.append(state) or "ok",
    )
    tool = SendPhoneNotificationTool(LockdownConfig(email="a@b.c", password="p", phone="1"))

    await tool.execute()

    assert sent == ["lockdown"]


def test_configured_requires_all_three_credentials():
    assert LockdownConfig(email="a@b.c", password="p", phone="1").configured is True
    assert LockdownConfig(email="a@b.c", password="p").configured is False
    assert LockdownConfig().configured is False
