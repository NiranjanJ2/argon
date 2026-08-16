"""The phone's self-reports, kept so a Screen Time failure can be read later."""

from argon.ios import diagnostics


class TestRecording:
    def test_a_report_is_stamped_and_readable(self):
        diagnostics.record({"kind": "reconcile", "metered_monitoring": True})

        [entry] = diagnostics.recent()
        assert entry["kind"] == "reconcile"
        assert entry["metered_monitoring"] is True
        assert entry["at"], "every entry needs a time or the log cannot be read"

    def test_the_shape_is_not_enforced(self):
        # Deliberately schema-free: the useful field is always the one nobody
        # thought to add, and rejecting it means the next failure is a mystery.
        diagnostics.record({"something_new": {"nested": [1, 2, 3]}})

        assert diagnostics.recent()[0]["something_new"] == {"nested": [1, 2, 3]}

    def test_newest_first(self):
        diagnostics.record({"kind": "a"})
        diagnostics.record({"kind": "b"})

        assert [e["kind"] for e in diagnostics.recent()] == ["b", "a"]

    def test_filtering_by_kind(self):
        diagnostics.record({"kind": "reconcile"})
        diagnostics.record({"kind": "push"})
        diagnostics.record({"kind": "reconcile"})

        assert len(diagnostics.recent(kind="reconcile")) == 2

    def test_the_log_stays_bounded(self):
        for n in range(diagnostics.MAX_ENTRIES + 30):
            diagnostics.record({"kind": "n", "i": n})

        entries = diagnostics.recent(limit=diagnostics.MAX_ENTRIES)
        assert len(entries) == diagnostics.MAX_ENTRIES
        # The tail is what matters — the newest reports survive, not the oldest.
        assert entries[0]["i"] == diagnostics.MAX_ENTRIES + 29

    def test_a_phone_supplied_timestamp_cannot_overwrite_the_server_one(self):
        diagnostics.record({"kind": "x", "at": "not-a-time"})

        assert diagnostics.recent()[0]["at"] != "not-a-time"

    def test_clearing_starts_a_clean_run(self):
        diagnostics.record({"kind": "a"})
        assert diagnostics.clear() == 1
        assert diagnostics.recent() == []
