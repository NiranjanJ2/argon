"""A mirror needs both halves.

`cron add` wrote a calendar event and `cron remove` did not delete one. So
"lock in for school day" was cancelled — the job removed, Argon replying
"Lock-in canceled" — and the event stayed on his calendar, where it turned up
in his week summary as a commitment he had explicitly called off.
"""

from __future__ import annotations

from argon.services import agenda


class _FakeEvents:
    def __init__(self, items):
        self.items = items
        self.deleted = []

    def list(self, **_kw):
        return _Exec({"items": self.items})

    def delete(self, *, eventId, **_kw):
        self.deleted.append(eventId)
        return _Exec({})


class _Exec:
    def __init__(self, value): self.value = value
    def execute(self): return self.value


def _svc(items):
    class _S:
        def __init__(self): self.ev = _FakeEvents(items)
        def events(self): return self.ev
    return _S()


def _patch(monkeypatch, service):
    monkeypatch.setattr("argon.google.service.build_google_service",
                        lambda *a, **k: service)


AT_MS = 1786000000000


def test_a_mirrored_event_is_removed(monkeypatch):
    service = _svc([{"id": "e1", "summary": "Lock in for school day",
                     "description": agenda.MIRROR_TAG}])
    _patch(monkeypatch, service)

    assert agenda.remove_from_calendar("Lock in for school day", AT_MS) is True
    assert service.ev.deleted == ["e1"]


def test_his_own_event_at_the_same_minute_is_untouched(monkeypatch):
    """Matched on the tag Argon writes, never on the summary alone — deleting
    something he created himself would be unrecoverable."""
    service = _svc([{"id": "his", "summary": "Lock in for school day",
                     "description": "I made this myself"}])
    _patch(monkeypatch, service)

    assert agenda.remove_from_calendar("Lock in for school day", AT_MS) is False
    assert service.ev.deleted == []


def test_a_different_reminder_at_the_same_minute_is_untouched(monkeypatch):
    service = _svc([{"id": "other", "summary": "Something else",
                     "description": agenda.MIRROR_TAG}])
    _patch(monkeypatch, service)

    assert agenda.remove_from_calendar("Lock in for school day", AT_MS) is False
    assert service.ev.deleted == []


def test_nothing_there_is_not_an_error(monkeypatch):
    service = _svc([])
    _patch(monkeypatch, service)

    assert agenda.remove_from_calendar("Lock in for school day", AT_MS) is False
