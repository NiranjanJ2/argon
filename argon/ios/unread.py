"""How many messages he has not seen, for the badge on the app icon.

A badge is only useful if it can reach zero. Counting up from the device would
mean the number never matched reality — he reads a message on the Mac, or
answers it in Discord, and the phone goes on insisting there are four. So the
count is kept here, where every surface reports back to, and pushed as an
absolute value.
"""

from __future__ import annotations

from argon import clock
from argon.core import store

_DOC = "ios_unread"


def count() -> int:
    return int(store.get_doc(_DOC, {"count": 0}).get("count", 0) or 0)


def bump() -> int:
    """One more thing he has not read. Returns the new count."""
    with store.edit_doc(_DOC, {"count": 0}) as doc:
        doc["count"] = int(doc.get("count", 0) or 0) + 1
        return int(doc["count"])


def clear() -> int:
    """He opened the app. Returns how many were outstanding."""
    with store.edit_doc(_DOC, {"count": 0}) as doc:
        was = int(doc.get("count", 0) or 0)
        doc["count"] = 0
        doc["cleared_at"] = clock.now().replace(microsecond=0).isoformat()
        return was
