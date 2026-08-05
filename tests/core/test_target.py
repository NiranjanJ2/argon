"""The delivery address must outlive the session cache.

Archiving one stale session file on 2026-08-03 deleted the only record of the
Discord DM. ``pick_target()`` fell back to ``cli``, ``notify()`` returned early,
and every check-in for two days was generated, logged as "Check-in spoke" and
recorded in the ledger without ever being sent.
"""

from __future__ import annotations

from argon.core import target


def test_a_real_channel_is_remembered(tmp_path):
    target.remember(tmp_path, "discord", "1477811435487891629")
    assert target.recall(tmp_path, {"discord"}) == ("discord", "1477811435487891629")


def test_unreachable_channels_are_never_recorded(tmp_path):
    """Recording "cli" would overwrite the only address Argon can deliver to."""
    target.remember(tmp_path, "discord", "123")
    for channel in target.UNREACHABLE:
        target.remember(tmp_path, channel, "direct")
    assert target.recall(tmp_path, {"discord"}) == ("discord", "123")


def test_a_blank_chat_id_is_ignored(tmp_path):
    target.remember(tmp_path, "discord", "")
    assert target.recall(tmp_path, {"discord"}) is None


def test_a_disabled_channel_is_not_offered(tmp_path):
    """Turning Discord off must not keep routing messages at it."""
    target.remember(tmp_path, "discord", "123")
    assert target.recall(tmp_path, {"whatsapp"}) is None


def test_nothing_recorded_yet(tmp_path):
    assert target.recall(tmp_path, {"discord"}) is None


def test_a_corrupt_file_reads_as_no_target(tmp_path):
    (tmp_path / "last_target.json").write_text("{not json", encoding="utf-8")
    assert target.recall(tmp_path, {"discord"}) is None


def test_the_newest_address_wins(tmp_path):
    target.remember(tmp_path, "discord", "old")
    target.remember(tmp_path, "discord", "new")
    assert target.recall(tmp_path, {"discord"}) == ("discord", "new")


def test_an_unwritable_workspace_does_not_break_the_turn(tmp_path):
    """Journalling the address must never take down the message it describes."""
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    target.remember(blocked / "ws", "discord", "123")  # must not raise
