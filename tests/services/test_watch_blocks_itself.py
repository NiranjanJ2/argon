"""The watch imposes the block itself instead of asking a model to.

`_decide` was already ordinary Python — it ruled out working, napping,
before-start and the emergency override without a network call. But the only
consequence of a decision was an LLM turn, so when the background model was
retired on 09/03 the watch fired twenty times an evening and blocked nothing.
"""

from __future__ import annotations

import pytest

from argon.services import heartbeat as hb_mod
from argon.services.heartbeat import WATCH_SHIELD_SOURCE, HeartbeatService


class _Phone:
    """Stands in for argon.ios.mode's on-disk desired state."""

    def __init__(self, mode="off", source=""):
        self.state = {"mode": mode, "source": source}
        self.calls: list[tuple] = []

    def get_mode(self):
        return dict(self.state)

    def set_mode(self, mode, *, duration_min=None, reason="", source="", **kw):
        self.calls.append(("set", mode, source))
        self.state = {"mode": mode, "source": source}
        return self.state

    def renew(self, minutes, *, source):
        self.calls.append(("renew", minutes, source))


class _OverrideActiveError(ValueError):
    pass


@pytest.fixture
def watch(tmp_path, monkeypatch):
    phone = _Phone()
    phone.OverrideActive = _OverrideActiveError
    # `from argon.ios import mode` takes the package attribute once anything
    # has imported it, so patching sys.modules alone works in isolation and
    # silently does nothing in a full run.
    import argon.ios

    monkeypatch.setattr(argon.ios, "mode", phone)
    monkeypatch.setitem(__import__("sys").modules, "argon.ios.mode", phone)
    service = HeartbeatService(
        workspace=tmp_path, provider=None, model="unused",
        on_execute=None, on_notify=None, timezone="America/Los_Angeles",
    )
    return service, phone


def test_a_reason_shields_the_phone(watch):
    service, phone = watch

    service._engage_shield("nothing is running and work is due")

    assert phone.calls == [("set", "lock_in", WATCH_SHIELD_SOURCE)]


def test_a_standing_shield_is_renewed_not_reapplied(watch):
    # Re-applying bumps the version and makes the phone redo the whole block.
    service, phone = watch
    phone.state = {"mode": "lock_in", "source": WATCH_SHIELD_SOURCE}

    service._engage_shield("still due")

    assert phone.calls == [("renew", hb_mod.WATCH_SHIELD_MIN, WATCH_SHIELD_SOURCE)]


def test_a_task_focus_is_never_overwritten(watch):
    # A started task blocks harder than the watch does; stomping it would swap a
    # lock-until-done for a 60 minute timer.
    service, phone = watch
    phone.state = {"mode": "lock_in", "source": "task"}

    service._engage_shield("work is due")

    assert phone.calls == []


def test_the_emergency_release_wins(watch):
    service, phone = watch

    def refuse(*a, **k):
        raise _OverrideActiveError("override")

    phone.set_mode = refuse
    service._engage_shield("work is due")  # must not raise

    assert phone.state["mode"] == "off"


def test_it_lifts_only_its_own_block(watch):
    service, phone = watch
    phone.state = {"mode": "lock_in", "source": "task"}

    service._release_shield()

    assert phone.calls == []


def test_it_lifts_its_own_block_when_nothing_is_due(watch):
    service, phone = watch
    phone.state = {"mode": "lock_in", "source": WATCH_SHIELD_SOURCE}

    service._release_shield()

    assert phone.calls == [("set", "off", WATCH_SHIELD_SOURCE)]


def test_a_broken_phone_never_takes_the_watch_down(watch):
    service, phone = watch

    def boom(*a, **k):
        raise RuntimeError("phone is unreachable")

    phone.get_mode = boom
    service._engage_shield("work is due")
    service._release_shield()
