"""A hard ceiling on what Argon may spend at a metered provider.

Niranjan asked for five dollars a month, strictly, hardcoded — so the number
lives here as a constant rather than in config, where a stray edit or a bad
migration could quietly raise it.

**Only metered models count.** NIM and Groq are prepaid and not billed per token
here, so a model absent from `PRICES` costs nothing and is never blocked. The cap
exists to stop an OpenAI bill running away, not to ration the endpoints he has
already paid for.

Going over does not take Argon off the air. `spend cap` in the raised message is
matched by `_is_provider_refusal`, so the existing standby path catches it and
the turn is served by the fallback provider. Degrading to a cheaper model is the
correct failure; going silent is not.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from argon import clock
from argon.core import store

#: Dollars per calendar month. Hardcoded on purpose — see the module docstring.
MONTHLY_CAP_USD = 5.00

#: Warn once past this fraction, so the ceiling is not a surprise.
WARN_AT = 0.80

#: (input, output, cached input) in dollars per million tokens.
#: A model missing from this table is treated as unmetered and never blocked.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-luna": (0.20, 1.20, 0.02),
    "gpt-5.6-terra": (1.25, 10.00, 0.125),
    "gpt-5.6-sol": (5.00, 40.00, 0.50),
}

_DOC = "spend"


def _month() -> str:
    return clock.now().strftime("%Y-%m")


def price_of(model: str) -> tuple[float, float, float] | None:
    """Rates for *model*, or None when it is not metered."""
    name = (model or "").split("/")[-1].strip()
    return PRICES.get(name)


def cost_of(model: str, prompt: int, completion: int, cached: int = 0) -> float:
    rates = price_of(model)
    if rates is None:
        return 0.0
    pin, pout, pcached = rates
    billed = max(0, prompt - cached)
    return (billed * pin + cached * pcached + completion * pout) / 1_000_000


def spent() -> float:
    """This calendar month's metered spend."""
    doc = store.get_doc(_DOC, {}) or {}
    return float(doc.get(_month(), 0.0))


def remaining() -> float:
    return max(0.0, MONTHLY_CAP_USD - spent())


def record(model: str, prompt: int, completion: int, cached: int = 0) -> float:
    """Add one call to the month's total. Returns its cost."""
    cost = cost_of(model, prompt, completion, cached)
    if cost <= 0:
        return 0.0
    month = _month()
    with store.edit_doc(_DOC, {}) as doc:
        before = float(doc.get(month, 0.0))
        after = before + cost
        doc[month] = after
        # Keep only the current and previous month; this is a meter, not history.
        for key in [k for k in doc if k < month][:-1]:
            doc.pop(key, None)
    if before < MONTHLY_CAP_USD * WARN_AT <= after:
        logger.warning(
            "Spend is at ${:.2f} of the ${:.2f} monthly cap", after, MONTHLY_CAP_USD
        )
    return cost


class SpendCapError(RuntimeError):
    """Raised instead of calling a metered model that would exceed the cap.

    The words "spend cap" matter: `_is_provider_refusal` matches on "spend", so
    this routes through the standby path and the turn is served by the fallback
    provider rather than failing.
    """


def check(model: str) -> None:
    """Raise if *model* is metered and the month's cap is already reached."""
    if price_of(model) is None:
        return
    used = spent()
    if used >= MONTHLY_CAP_USD:
        raise SpendCapError(
            f"monthly spend cap reached (${used:.2f} of ${MONTHLY_CAP_USD:.2f}); "
            f"refusing {model}"
        )


def status() -> dict[str, Any]:
    """For `argon doctor` and the status endpoint."""
    used = spent()
    return {
        "month": _month(),
        "spent": round(used, 4),
        "cap": MONTHLY_CAP_USD,
        "remaining": round(max(0.0, MONTHLY_CAP_USD - used), 4),
        "capped": used >= MONTHLY_CAP_USD,
    }
