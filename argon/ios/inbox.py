"""What Argon said unprompted, and what Niranjan can tap back.

Discord got buttons; the phone got a text field. So every proactive message —
the afternoon brief, the follow-up, the fifteen-minute meeting warning — was
answerable in one place and only readable in the other.

This records what went out alongside the actions that were offered with it, so
the app can render the same message with the same buttons. Acting on one is not
handled here: ``PATCH /v1/tasks/<id>`` already starts and completes tasks, and a
second implementation of "start a task" is exactly how two surfaces drift into
disagreeing about what is running.

Answers are recorded, though. An item he has already dealt with must stop
looking like an open question, on every surface at once.
"""

from __future__ import annotations

import uuid
from typing import Any

from argon import clock
from argon.core import store

#: Key for the whole inbox document.
_DOC = "ios_inbox"

#: Kept short on purpose. This is an inbox, not a transcript — the message log
#: is the check-in ledger's job, and an unbounded list here would be reread and
#: rewritten in full on every delivery.
MAX_ITEMS = 40


def _stamp() -> str:
    """Whole-second ISO 8601, which is all Swift's parser reliably accepts."""
    return clock.now().replace(microsecond=0).isoformat()


def record(
    text: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    """Note that Argon said something, with the buttons it offered.

    ``key`` is the delivery idempotency key. Recording under it means a retry of
    the same check-in updates one entry instead of stacking duplicates in the
    app — the same guarantee the outbox already gives Discord.
    """
    item = {
        "id": key or uuid.uuid4().hex,
        "text": text,
        "sent_at": _stamp(),
        "actions": list(actions or []),
        "answered": None,
    }
    with store.edit_doc(_DOC, {"items": []}) as doc:
        items = [i for i in doc.get("items", []) if i.get("id") != item["id"]]
        items.append(item)
        doc["items"] = items[-MAX_ITEMS:]
    return item


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """Newest first, so the app does not have to know the storage order."""
    items = store.get_doc(_DOC, {"items": []}).get("items", [])
    return list(reversed(items))[: max(1, limit)]


def mark_answered(item_id: str, verb: str, result: str = "") -> dict[str, Any] | None:
    """Record that he answered, so the item stops reading as an open question.

    Returns None for an unknown id rather than inventing an entry — a tap on a
    message that has already aged out of the inbox is not worth resurrecting.
    """
    found = None
    with store.edit_doc(_DOC, {"items": []}) as doc:
        for item in doc.get("items", []):
            if item.get("id") == item_id:
                item["answered"] = {"verb": verb, "at": _stamp(), "result": result}
                found = dict(item)
                break
    return found


def unanswered() -> list[dict[str, Any]]:
    """Items still waiting on him — what a badge count would show."""
    return [i for i in recent(MAX_ITEMS) if not i.get("answered") and i.get("actions")]
