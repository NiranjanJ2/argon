"""The phone's copy of what Argon said, and whether it was answered."""

from argon.ios import inbox


def _actions(task_id: str = "t1") -> list[dict[str, str]]:
    return [
        {"label": "Starting now", "action": "start", "task_id": task_id, "title": "APUSH"},
        {"label": "Done", "action": "complete", "task_id": task_id, "title": "APUSH"},
    ]


class TestRecording:
    def test_a_message_is_readable_with_the_buttons_it_offered(self):
        inbox.record("Have you started APUSH?", actions=_actions())

        [item] = inbox.recent()
        assert item["text"] == "Have you started APUSH?"
        assert [a["action"] for a in item["actions"]] == ["start", "complete"]
        assert item["answered"] is None

    def test_a_redelivery_updates_one_entry_rather_than_stacking_copies(self):
        # The outbox retries under the same key. Two entries would show him the
        # same question twice and make the badge count lie.
        inbox.record("Have you started APUSH?", actions=_actions(), key="checkin:2026-08-15:nudge")
        inbox.record("Have you started APUSH?", actions=_actions(), key="checkin:2026-08-15:nudge")

        assert len(inbox.recent()) == 1

    def test_newest_first(self):
        inbox.record("first")
        inbox.record("second")

        assert [i["text"] for i in inbox.recent()] == ["second", "first"]

    def test_the_inbox_stays_bounded(self):
        for n in range(inbox.MAX_ITEMS + 15):
            inbox.record(f"message {n}")

        stored = inbox.recent(limit=inbox.MAX_ITEMS)
        assert len(stored) == inbox.MAX_ITEMS
        assert stored[0]["text"] == f"message {inbox.MAX_ITEMS + 14}"


class TestAnswering:
    def test_answering_closes_the_question(self):
        item = inbox.record("Have you started APUSH?", actions=_actions())

        answered = inbox.mark_answered(item["id"], "start", "Started APUSH.")

        assert answered["answered"]["verb"] == "start"
        assert inbox.recent()[0]["answered"]["result"] == "Started APUSH."

    def test_an_answered_item_stops_counting_as_waiting_on_him(self):
        item = inbox.record("Have you started APUSH?", actions=_actions())
        assert len(inbox.unanswered()) == 1

        inbox.mark_answered(item["id"], "defer")

        assert inbox.unanswered() == []

    def test_a_message_with_no_buttons_is_never_waiting_on_him(self):
        inbox.record("Your meeting starts in 15 minutes.")

        assert inbox.unanswered() == []

    def test_answering_something_that_aged_out_is_not_resurrected(self):
        assert inbox.mark_answered("gone", "start") is None
        assert inbox.recent() == []


class TestBackfill:
    """Messages Argon really sent, adopted from the ledger."""

    def _ledger(self, entries):
        from argon.core import store
        from argon.services.reminder import LEDGER_DOC

        store.put_doc(LEDGER_DOC, {"date": "2026-08-15", "said": entries})

    def test_a_check_in_sent_before_the_inbox_existed_is_adopted(self):
        self._ledger([
            {"at": "2026-08-15T16:00:01-07:00", "occasion": "daily_brief",
             "text": "Two Math summer assignments are due Sunday."}
        ])

        assert inbox.backfill_from_ledger() == 1
        assert inbox.recent()[0]["text"].startswith("Two Math")

    def test_backfilling_twice_does_not_duplicate(self):
        self._ledger([{"at": "2026-08-15T16:00:01-07:00", "occasion": "daily_brief",
                       "text": "Brief"}])

        inbox.backfill_from_ledger()
        assert inbox.backfill_from_ledger() == 0
        assert len(inbox.recent()) == 1

    def test_a_live_recording_wins_over_the_backfilled_copy(self):
        # Same delivery key, so the version carrying buttons replaces the
        # text-only one rather than sitting beside it.
        self._ledger([{"at": "2026-08-15T16:00:01-07:00", "occasion": "daily_brief",
                       "text": "Brief"}])
        inbox.backfill_from_ledger()

        inbox.record("Brief", actions=_actions(), key="checkin:2026-08-15:daily_brief")

        [item] = inbox.recent()
        assert item["actions"], "the live copy should carry its buttons"

    def test_an_empty_ledger_adopts_nothing(self):
        assert inbox.backfill_from_ledger() == 0
        assert inbox.recent() == []
