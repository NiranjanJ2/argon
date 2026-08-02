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


# ---------------------------------------------------------------------------
# convergence — catching a lock that never landed
# ---------------------------------------------------------------------------


def test_matching_versions_are_converged():
    ios_mode.set_mode("off", reason="idle")
    ios_mode.record_actual({"mode": "off", "version": ios_mode.get_mode()["version"]})
    assert ios_mode.convergence()[0] == "converged"


def test_a_phone_that_never_checked_in():
    ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    assert ios_mode.convergence()[0] == "never_seen"


def test_a_phone_that_has_not_answered_yet_is_pending():
    """Reported before the mode was published — it simply has not seen it."""
    ios_mode.record_actual({"mode": "off", "version": 0})
    ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    assert ios_mode.convergence()[0] == "pending"


def test_a_phone_that_answered_but_did_not_apply_has_diverged(monkeypatch):
    """The real bug: the app's reconciler fails silently on a bad profile.

    It reports *after* the request, still on the old version. Without this the
    server cannot tell "tried and failed" from "phone is switched off".
    """
    ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    later = datetime.now(LA) + timedelta(seconds=30)
    monkeypatch.setattr("argon.ios.mode.clock.now", lambda: later)
    ios_mode.record_actual({"mode": "off", "version": 0, "shielded": False})

    state, detail = ios_mode.convergence()
    assert state == "diverged"
    assert "could not apply" in detail


def test_a_silent_phone_goes_stale(monkeypatch):
    ios_mode.record_actual({"mode": "off", "version": 0})
    ios_mode.set_mode("lock_in", duration_min=60, reason="pset")

    later = datetime.now(LA) + timedelta(minutes=ios_mode.STALE_AFTER_MINUTES + 1)
    monkeypatch.setattr("argon.ios.mode.clock.now", lambda: later)
    assert ios_mode.convergence()[0] == "stale"


def test_snapshot_carries_convergence_without_breaking_the_app_contract():
    snap = ios_mode.snapshot()
    assert snap["convergence"]["state"]
    # The Swift structs still find everything they decode; extra keys are ignored.
    assert REQUIRED_DESIRED <= snap["desired"].keys()
    assert REQUIRED_ACTUAL <= snap["actual"].keys()


def test_a_reported_error_is_a_failure_not_a_gap():
    """The app now confesses; the server must not read that as silence."""
    ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    ios_mode.record_actual(
        {"mode": "off", "version": 0, "shielded": False,
         "error": "No profile named 'Argon Lockdown'"}
    )
    state, detail = ios_mode.convergence()
    assert state == "failed"
    assert "Argon Lockdown" in detail


def test_an_acknowledged_lock_without_a_shield_is_a_failure():
    """The app refuses unsafe focus states and still reports the version.

    Comparing versions alone would call that converged while the phone sits
    wide open — the exact lie this whole mechanism exists to prevent.
    """
    desired = ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    ios_mode.record_actual(
        {"mode": "off", "version": desired["version"], "shielded": False}
    )
    assert ios_mode.convergence()[0] == "failed"


def test_an_acknowledged_lock_with_a_shield_is_converged():
    desired = ios_mode.set_mode("lock_in", duration_min=60, reason="pset")
    ios_mode.record_actual(
        {"mode": "lock_in", "version": desired["version"], "shielded": True}
    )
    assert ios_mode.convergence()[0] == "converged"


def test_an_acknowledged_unlock_needs_no_shield():
    desired = ios_mode.set_mode("off", reason="done")
    ios_mode.record_actual(
        {"mode": "off", "version": desired["version"], "shielded": False}
    )
    assert ios_mode.convergence()[0] == "converged"


# ---------------------------------------------------------------------------
# Emergency override — "don't get stuck"
# ---------------------------------------------------------------------------


def test_an_override_releases_whatever_is_active():
    ios_mode.set_mode("lock_in", duration_min=600, allow_early_end=False, reason="pset")
    ios_mode.engage_override(120, source="cli")
    assert ios_mode.get_mode()["mode"] == "off"


def test_argon_cannot_impose_a_block_during_an_override():
    """The whole point: releasing is useless if it re-locks a minute later."""
    ios_mode.engage_override(120, source="phone")
    for mode in ("lock_in", "school", "homework", "sleep"):
        with pytest.raises(ios_mode.OverrideActive):
            ios_mode.set_mode(mode, duration_min=30, reason="nope")
    assert ios_mode.get_mode()["mode"] == "off"


def test_off_is_always_allowed_during_an_override():
    """An escape hatch must never be able to jam shut."""
    ios_mode.engage_override(120)
    assert ios_mode.set_mode("off", reason="fine")["mode"] == "off"


def test_an_override_expires(monkeypatch):
    ios_mode.engage_override(30)
    later = datetime.now(LA) + timedelta(minutes=31)
    monkeypatch.setattr("argon.ios.mode.clock.now", lambda: later)

    assert ios_mode.override_status()[0] is False
    assert ios_mode.set_mode("lock_in", duration_min=30, reason="ok")["mode"] == "lock_in"


def test_an_override_can_be_ended_early():
    ios_mode.engage_override(120)
    ios_mode.clear_override()
    assert ios_mode.override_status()[0] is False
    assert ios_mode.set_mode("lock_in", duration_min=30, reason="ok")["mode"] == "lock_in"


def test_a_corrupt_override_file_does_not_trap_anyone():
    """Fail open: an unreadable override must not become a permanent lock."""
    ios_mode._file("override.json").write_text("{ not json")
    assert ios_mode.override_status()[0] is False


def test_an_unparseable_until_does_not_trap_anyone():
    ios_mode._file("override.json").write_text('{"until": "whenever"}')
    assert ios_mode.override_status()[0] is False


def test_the_override_shows_up_in_the_status_snapshot():
    ios_mode.engage_override(120)
    override = ios_mode.snapshot()["override"]
    assert override["active"] is True
    assert override["until"]


async def test_the_focus_tool_refuses_during_an_override():
    from argon.tools.focus import SetFocusModeTool

    ios_mode.engage_override(120)
    result = await SetFocusModeTool(60).execute(mode="lock_in", reason="try it")
    assert "Not applied" in result
    assert ios_mode.get_mode()["mode"] == "off"


# ---------------------------------------------------------------------------
# Night guard
#
# Live, 01:37: "today i want to really lock in for SAT prep" -> Argon locked
# the phone immediately. A plan is not a command, and 1:37 AM is not now.
# ---------------------------------------------------------------------------


async def test_a_block_is_refused_late_at_night(monkeypatch):
    from argon.tools.focus import SetFocusModeTool

    night = datetime.now(LA).replace(hour=1, minute=37)
    monkeypatch.setattr("argon.tools.focus.clock.now", lambda: night)

    result = await SetFocusModeTool(60).execute(mode="lock_in", reason="SAT prep")

    assert "Not applied" in result
    assert ios_mode.get_mode()["mode"] == "off"


async def test_the_model_cannot_confirm_on_his_behalf(monkeypatch):
    """It set confirmed=true itself and locked the phone at 1:47 AM."""
    from argon.tools.focus import SetFocusModeTool

    night = datetime.now(LA).replace(hour=1, minute=37)
    monkeypatch.setattr("argon.tools.focus.clock.now", lambda: night)
    tool = SetFocusModeTool(60)

    first = await tool.execute(mode="lock_in", reason="SAT prep", confirmed=True)
    retry = await tool.execute(mode="lock_in", reason="SAT prep", confirmed=True)

    assert "Not applied" in first
    assert "Not applied" in retry, "a same-turn retry must not slip through"
    assert ios_mode.get_mode()["mode"] == "off"


async def test_a_night_block_goes_through_once_he_has_replied(monkeypatch):
    """New turn after the refusal = he was actually asked."""
    from argon.tools.focus import SetFocusModeTool

    night = datetime.now(LA).replace(hour=1, minute=37)
    monkeypatch.setattr("argon.tools.focus.clock.now", lambda: night)
    tool = SetFocusModeTool(60)

    assert "Not applied" in await tool.execute(mode="lock_in", reason="SAT prep")
    tool.start_turn()  # Niranjan replied; the loop starts a fresh turn
    await tool.execute(mode="lock_in", reason="he said yes")

    assert ios_mode.get_mode()["mode"] == "lock_in"


async def test_daytime_blocks_need_no_confirmation(monkeypatch):
    from argon.tools.focus import SetFocusModeTool

    noon = datetime.now(LA).replace(hour=12, minute=0)
    monkeypatch.setattr("argon.tools.focus.clock.now", lambda: noon)

    await SetFocusModeTool(60).execute(mode="lock_in", reason="pset")

    assert ios_mode.get_mode()["mode"] == "lock_in"


async def test_unblocking_is_never_refused_by_the_night_guard(monkeypatch):
    """Getting out must work at any hour."""
    from argon.tools.focus import SetFocusModeTool

    night = datetime.now(LA).replace(hour=3, minute=0)
    monkeypatch.setattr("argon.tools.focus.clock.now", lambda: night)

    assert "released" in await SetFocusModeTool(60).execute(mode="off")
