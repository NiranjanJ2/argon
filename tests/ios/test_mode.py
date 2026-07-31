"""The desired-mode contract the iPhone decodes.

The app's Swift structs are non-optional for everything but ``since`` and
``expires_at``, so a partial object fails the whole ``/v1/status`` decode and
the app shows "Offline". These tests pin the shape, not just the behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from argon.ios import mode as ios_mode

LA = ZoneInfo("America/Los_Angeles")
REQUIRED_DESIRED = {"mode", "version", "since", "expires_at", "allow_early_end", "reason"}
REQUIRED_ACTUAL = {"mode", "version", "shielded", "last_seen"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))


def test_a_fresh_install_still_returns_a_complete_object():
    desired = ios_mode.get_mode()
    assert REQUIRED_DESIRED <= desired.keys()
    assert desired["mode"] == "off"
    assert desired["version"] == 0
    # Non-optional on the Swift side — null here breaks the whole decode.
    assert desired["reason"] == ""
    assert desired["allow_early_end"] is True


def test_snapshot_always_has_both_halves():
    snap = ios_mode.snapshot()
    assert REQUIRED_DESIRED <= snap["desired"].keys()
    assert REQUIRED_ACTUAL <= snap["actual"].keys()
    assert snap["actual"]["shielded"] is False


def test_every_change_bumps_the_version():
    first = ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    second = ios_mode.set_mode("off", reason="done")
    assert second["version"] == first["version"] + 1


def test_timestamps_carry_no_fractional_seconds():
    """Swift parses 0 or 3 fractional digits; Python emits 6.

    A 6-digit expiry silently decodes to nil, the app treats a timed lock as
    open-ended, and the shield never releases itself. Second precision dodges it.
    """
    desired = ios_mode.set_mode("lock_in", duration_min=90, reason="pset")
    assert "." not in desired["expires_at"]
    assert "." not in desired["since"]
    # And it is still a real, parseable instant.
    assert datetime.fromisoformat(desired["expires_at"]) > datetime.fromisoformat(
        desired["since"]
    )


def test_a_duration_becomes_an_expiry():
    desired = ios_mode.set_mode("homework", duration_min=45, reason="hw")
    delta = datetime.fromisoformat(desired["expires_at"]) - datetime.fromisoformat(
        desired["since"]
    )
    assert delta == timedelta(minutes=45)


def test_off_never_carries_an_expiry():
    assert ios_mode.set_mode("off", duration_min=45)["expires_at"] is None


def test_an_elapsed_window_collapses_to_off(monkeypatch):
    """Otherwise the server keeps advertising a lock the phone already released."""
    ios_mode.set_mode("lock_in", duration_min=30, reason="pset")

    later = datetime.now(LA) + timedelta(minutes=31)
    monkeypatch.setattr("argon.ios.mode.clock.now", lambda: later)

    collapsed = ios_mode.get_mode()
    assert collapsed["mode"] == "off"
    assert collapsed["reason"] == "focus window ended"


def test_an_unexpired_window_survives(monkeypatch):
    ios_mode.set_mode("lock_in", duration_min=120, reason="pset")
    assert ios_mode.get_mode()["mode"] == "lock_in"


def test_an_unparseable_expiry_does_not_lose_the_mode(monkeypatch):
    ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    path = ios_mode._file("desired_mode.json")
    path.write_text(path.read_text().replace(
        ios_mode.get_mode()["expires_at"], "not-a-date"))
    assert ios_mode.get_mode()["mode"] == "lock_in"


def test_an_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        ios_mode.set_mode("banana")


def test_the_phone_report_round_trips():
    ios_mode.record_actual(
        {"mode": "lock_in", "version": 7, "shielded": True,
         "applied_at": "2026-07-31T10:00:00-07:00", "battery": 0.62}
    )
    actual = ios_mode.get_actual()
    assert actual["mode"] == "lock_in"
    assert actual["version"] == 7
    assert actual["shielded"] is True
    assert actual["last_seen"] is not None


def test_a_junk_report_still_yields_a_decodable_object():
    ios_mode.record_actual({})
    actual = ios_mode.get_actual()
    assert REQUIRED_ACTUAL <= actual.keys()
    assert actual["shielded"] is False
    assert actual["version"] == 0


def test_a_corrupt_state_file_falls_back_to_defaults():
    ios_mode._file("desired_mode.json").write_text("{ this is not json")
    assert ios_mode.get_mode()["mode"] == "off"


def test_a_partial_state_file_is_completed():
    """A file written by an older build must not break the app's decode."""
    ios_mode._file("desired_mode.json").write_text('{"mode": "sleep", "version": 3}')
    desired = ios_mode.get_mode()
    assert REQUIRED_DESIRED <= desired.keys()
    assert desired["mode"] == "sleep"
    assert desired["reason"] == ""


def test_the_device_token_is_stored():
    ios_mode.record_device({"device_token": "abc123", "environment": "sandbox",
                            "app_version": "1.0"})
    stored = ios_mode._read("device.json", {})
    assert stored["device_token"] == "abc123"
    assert stored["environment"] == "sandbox"
