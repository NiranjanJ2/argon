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
