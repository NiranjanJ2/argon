"""The operational store, and the two failures it exists to remove.

Argon's load-bearing state was a handful of JSON files rewritten in place. A
torn write left invalid JSON, and every reader in the codebase caught
`JSONDecodeError` and returned defaults — so a crash at the wrong moment did
not surface as a fault, it surfaced as an empty day. And the Flask API thread
and an asyncio turn writing the same file silently discarded one of the two.
"""

from __future__ import annotations

import threading

import pytest

from argon.core import store


def test_a_missing_document_is_not_an_error():
    assert store.get_doc("nothing_here") is None
    assert store.get_doc("nothing_here", {"a": 1}) == {"a": 1}


def test_writing_bumps_a_version_so_readers_can_tell_it_moved():
    assert store.put_doc("k", {"n": 1}) == 1
    assert store.put_doc("k", {"n": 2}) == 2
    assert store.doc_version("k") == 2
    assert store.get_doc("k") == {"n": 2}


def test_an_unreadable_document_raises_instead_of_reading_as_empty():
    """The whole point. "Nothing today" and "I cannot read it" are different."""
    with store.txn() as conn:
        conn.execute(
            "INSERT INTO docs (key, value, version, updated_at) VALUES ('k', '{oops', 1, 0)"
        )

    with pytest.raises(store.StoreCorrupt):
        store.get_doc("k")


def test_a_failed_edit_leaves_the_previous_document_intact():
    store.put_doc("k", {"blocks": ["keep me"]})

    with pytest.raises(RuntimeError):
        with store.edit_doc("k") as data:
            data["blocks"] = []
            raise RuntimeError("something went wrong mid-edit")

    assert store.get_doc("k") == {"blocks": ["keep me"]}, "the rollback must be real"


def test_concurrent_edits_from_two_threads_do_not_lose_one():
    """The lost update. Read-modify-write on a file dropped whichever landed first.

    The Flask API runs in a daemon thread beside the asyncio loop, so this is
    not hypothetical: a widget PATCH and a chat turn touch the same day.
    """
    store.put_doc("counter", {"n": 0, "who": []})
    errors: list[BaseException] = []

    def bump(name: str) -> None:
        try:
            for _ in range(25):
                with store.edit_doc("counter") as data:
                    data["n"] += 1
                    data["who"].append(name)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=bump, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    result = store.get_doc("counter")
    assert result["n"] == 100, "every increment survived"
    assert len(result["who"]) == 100


def test_health_reports_a_usable_database():
    store.put_doc("k", {"n": 1})
    health = store.health()
    assert health["ok"] is True
    assert health["integrity"] == "ok"
    assert health["docs"] >= 1


def test_health_reports_an_unusable_database_rather_than_raising(monkeypatch, tmp_path):
    """Corruption has to be reportable, or nothing can say "needs recovery"."""
    broken = tmp_path / "argon.db"
    broken.write_bytes(b"this is not a sqlite file, not even close" * 40)
    monkeypatch.setenv("ARGON_HOME", str(tmp_path))
    store.reset_for_tests()

    health = store.health()

    assert health["ok"] is False
    assert health["error"]


class TestMigrationFromTheOldFiles:
    def test_existing_json_state_is_adopted_and_kept_as_a_backup(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        store.reset_for_tests()
        daily = tmp_path / "daily"
        daily.mkdir(parents=True)
        (daily / "state.json").write_text(json.dumps({"date": "2026-08-13", "mode": "working"}))
        (daily / "plan.json").write_text(json.dumps({"date": "2026-08-13", "blocks": [1]}))

        store.connect()

        assert store.get_doc("daily_state") == {"date": "2026-08-13", "mode": "working"}
        assert store.get_doc("day_plan") == {"date": "2026-08-13", "blocks": [1]}
        assert (daily / "state.json.pre-sqlite").exists(), "the original is recoverable"
        assert not (daily / "state.json").exists()

    def test_an_already_migrated_document_is_not_overwritten(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        store.reset_for_tests()
        store.put_doc("daily_state", {"date": "2026-08-14", "mode": "lock_in"})

        daily = tmp_path / "daily"
        daily.mkdir(parents=True)
        (daily / "state.json").write_text(json.dumps({"date": "2026-08-13", "mode": "working"}))
        store.reset_for_tests()
        store.connect()

        assert store.get_doc("daily_state")["mode"] == "lock_in", "live state wins over the file"

    def test_an_unreadable_old_file_does_not_stop_startup(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARGON_HOME", str(tmp_path))
        store.reset_for_tests()
        daily = tmp_path / "daily"
        daily.mkdir(parents=True)
        (daily / "state.json").write_text("{ truncated")

        store.connect()  # must not raise

        assert store.get_doc("daily_state") is None
