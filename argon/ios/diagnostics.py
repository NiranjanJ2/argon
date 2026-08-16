"""What the phone says about itself, kept so a failure can be read afterwards.

Screen Time failures are close to invisible from the server. ``/v1/ios/state``
carries a mode, a version and one error string, which answers "did it work" and
nothing at all about *why* — whether Family Controls is authorised, whether the
profile was found, whether a shield is actually up, what the allowance thinks it
is. Debugging weekend mode meant guessing, because every question worth asking
was about state that only existed on the device.

This is an append-only ring of whatever the app chooses to report. Deliberately
schema-free: the useful field is always the one nobody thought to add, and a
strict shape here means the next unexplained failure is unexplained again.
"""

from __future__ import annotations

from typing import Any

from argon import clock
from argon.core import store

_DOC = "ios_diagnostics"

#: Enough to cover a session of poking at the phone, not so many that the
#: document becomes expensive to rewrite on every report.
MAX_ENTRIES = 200


def record(payload: dict[str, Any]) -> dict[str, Any]:
    """Append one report from the phone."""
    entry = {
        "at": clock.now().replace(microsecond=0).isoformat(),
        **{k: v for k, v in payload.items() if k != "at"},
    }
    with store.edit_doc(_DOC, {"entries": []}) as doc:
        entries = doc.get("entries", [])
        entries.append(entry)
        doc["entries"] = entries[-MAX_ENTRIES:]
    return entry


def recent(limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
    """Newest first, optionally filtered to one ``kind`` of report."""
    entries = store.get_doc(_DOC, {"entries": []}).get("entries", [])
    if kind:
        entries = [e for e in entries if e.get("kind") == kind]
    return list(reversed(entries))[: max(1, limit)]


def clear() -> int:
    """Drop everything. Returns how many entries went."""
    with store.edit_doc(_DOC, {"entries": []}) as doc:
        count = len(doc.get("entries", []))
        doc["entries"] = []
    return count
