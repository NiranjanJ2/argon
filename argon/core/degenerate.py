"""Catch a reply that is not language before it reaches him.

A model can fall into a decoding loop and emit the same character until it hits
the token limit. Argon sent 4,557 characters to the phone that were 2,328
newlines, 1,909 full stops and fifteen letters, twice in one minute, and nothing
anywhere noticed — the turn "succeeded", the ledger recorded it as said, and the
session stored it as the assistant's own words, which then became context for
the next turn.

Low temperature makes this stickier rather than safer: once a loop starts, the
repeated token *is* the most likely next one, so a near-greedy decode has no way
out of it. That is a reason to check the output rather than to trust settings.

Deliberately a pure function over the finished string. Whatever produced it —
any provider, any model, a fallback nobody remembers configuring — the test is
the same: does this look like something a person wrote.
"""

from __future__ import annotations

import re

#: Below this, nonsense is cheap to ignore and real replies are often symbols
#: ("👍", "3pm?"). Loops are long by nature — they run until the token limit.
MIN_LENGTH = 200

#: Real prose is mostly letters and digits. The observed failure was 0.3%.
#: Prose sits well above 50% even when heavy with punctuation and markdown, so
#: this leaves a wide margin rather than sitting near the boundary.
MIN_ALNUM_RATIO = 0.25

#: One character repeated this many times running is not writing. Rules ("---")
#: and ellipses are far shorter; a loop produces hundreds.
MAX_RUN = 40

_RUN = re.compile(r"(.)\1{%d,}" % (MAX_RUN - 1), re.DOTALL)


def looks_degenerate(text: str) -> bool:
    """Is this a decoding loop rather than a reply?"""
    if not text or len(text) < MIN_LENGTH:
        return False

    if _RUN.search(text):
        return True

    alnum = sum(1 for ch in text if ch.isalnum())
    return (alnum / len(text)) < MIN_ALNUM_RATIO


def describe(text: str) -> str:
    """One line for the log, so the failure is diagnosable after the fact."""
    alnum = sum(1 for ch in text if ch.isalnum())
    run = _RUN.search(text)
    detail = f"{len(text)} chars, {alnum / max(1, len(text)):.1%} alphanumeric"
    if run:
        detail += f", {len(run.group(0))}x {run.group(1)!r} in a row"
    return detail
