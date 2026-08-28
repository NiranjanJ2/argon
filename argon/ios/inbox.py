"""Unprompted messages waiting for the app to collect them.

The app was send-only: `/v1/chat` takes a message and returns one turn, so
anything Argon started — the brief, the follow-up, a reminder — could only ever
reach Discord. Making the app the primary surface needs somewhere for those to
sit until it next asks.

**A queue write is not a delivery.** That distinction is the whole point of
`argon.core.outbox`, and it holds here too, one step further out: `put` means
the message is durably waiting, `mark_fetched` means the phone has it. Nothing
in between counts, and `stale` exists so a message the phone never came for
falls back to a channel that can push.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from argon import clock
from argon.core import store

_DOC = "ios_inbox"

#: Messages kept after they are fetched, so the app can render recent history
#: without a second store. Older ones are dropped on the next write.
KEEP_RECENT = 50


def _blank() -> dict[str, Any]:
    return {"messages": []}


def put(text: str, *, key: str | None = None) -> dict[str, Any] | None:
    """Queue a message for the app. ``key`` makes a retry a no-op.

    Returns the row, or None when *key* was already queued — the same
    idempotency contract the outbox uses, for the same reason: a redelivery
    after a crash must not show him the brief twice.
    """
    text = (text or "").strip()
    if not text:
        return None
    with store.edit_doc(_DOC, _blank()) as doc:
        messages = doc.setdefault("messages", [])
        if key and any(m.get("key") == key for m in messages):
            return None
        row = {
            "id": uuid.uuid4().hex[:12],
            "key": key,
            "text": text,
            "created_at": clock.now().isoformat(),
            "fetched_at": None,
            "relayed_at": None,
        }
        messages.append(row)
        # Trim only what has been dealt with; an unfetched message is a promise.
        settled = [m for m in messages if m.get("fetched_at") or m.get("relayed_at")]
        if len(settled) > KEEP_RECENT:
            drop = {id(m) for m in settled[:-KEEP_RECENT]}
            doc["messages"] = [m for m in messages if id(m) not in drop]
        return dict(row)


def pending() -> list[dict[str, Any]]:
    """Everything the app has not collected yet, oldest first."""
    messages = store.get_doc(_DOC, _blank()).get("messages", [])
    return [m for m in messages if not m.get("fetched_at")]


def recent(limit: int = 20) -> list[dict[str, Any]]:
    """The tail of the mailbox, fetched or not, for rendering history."""
    messages = store.get_doc(_DOC, _blank()).get("messages", [])
    return messages[-limit:]


def mark_fetched(ids: list[str]) -> int:
    """The phone says it has these. This is the only thing that means delivered."""
    wanted = set(ids)
    if not wanted:
        return 0
    stamp = clock.now().isoformat()
    marked = 0
    with store.edit_doc(_DOC, _blank()) as doc:
        for row in doc.get("messages", []):
            if row.get("id") in wanted and not row.get("fetched_at"):
                row["fetched_at"] = stamp
                marked += 1
    return marked


def stale(minutes: int, now: datetime | None = None) -> list[dict[str, Any]]:
    """Queued this long ago, never collected, never relayed.

    The app is primary, not exclusive. If he has not opened it, a brief sitting
    in a mailbox he cannot see is the same as no brief.
    """
    now = now or clock.now()
    cutoff = now - timedelta(minutes=minutes)
    out = []
    for row in pending():
        if row.get("relayed_at"):
            continue
        try:
            created = datetime.fromisoformat(row["created_at"])
        except (KeyError, ValueError):
            continue  # unreadable stamp: leave it for the app rather than guess
        if created <= cutoff:
            out.append(row)
    return out


def mark_relayed(ids: list[str]) -> int:
    """Sent somewhere the phone is not. Relayed once, never twice."""
    wanted = set(ids)
    if not wanted:
        return 0
    stamp = clock.now().isoformat()
    marked = 0
    with store.edit_doc(_DOC, _blank()) as doc:
        for row in doc.get("messages", []):
            if row.get("id") in wanted and not row.get("relayed_at"):
                row["relayed_at"] = stamp
                marked += 1
    return marked
