"""MoneyPolicy — centralized monetary normalization (AGENT.md §4, M2.3).

Pure classifier: `amount + currency -> NormalizedAmount | UnsupportedCurrency`.
It NEVER raises, logs, or knows about NEEDS_REVIEW — the fiscal layer owns the
translation to a review outcome. Per-currency scale map (additive).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class NormalizedAmount:
    amount: Decimal


@dataclass(frozen=True)
class UnsupportedCurrency:
    """Deliberately NO numeric interface (not a Decimal, no __add__/__float__).

    Forgetting to handle this failure branch is a mypy error, not a runtime
    surprise.
    """

    currency: str
    reason: str


class MoneyPolicy:
    _SCALES = {"MXN": 2, "USD": 2, "CAD": 2}
    _ROUNDING = ROUND_HALF_UP

    def normalize(
        self, amount: str | Decimal, currency: str
    ) -> NormalizedAmount | UnsupportedCurrency:
        scale = self._SCALES.get(currency.upper())
        if scale is None:
            return UnsupportedCurrency(currency=currency, reason="unsupported currency")
        quant = Decimal(1).scaleb(-scale)
        return NormalizedAmount(
            amount=Decimal(str(amount)).quantize(quant, rounding=self._ROUNDING)
        )


def amount_as_text(amount: NormalizedAmount) -> str:
    """Canonical TEXT form of a normalized amount — the only money serializer.

    Persistence stores money as TEXT, never REAL/FLOAT (§4: "`float` is
    prohibited in fiscal/accounting code"). ``format(value, "f")`` is used
    instead of ``str()`` because ``str(Decimal("1E+2"))`` is ``"1E+2"``:
    a scientific-notation amount would round-trip as a *string* while breaking
    exact text comparison in queries. The scale is preserved verbatim — rounding
    is `MoneyPolicy.normalize`'s job, not the codec's.
    """
    return format(amount.amount, "f")


def amount_from_text(text: str) -> NormalizedAmount:
    """Inverse of :func:`amount_as_text`: ``Decimal`` straight from the text.

    Never via ``float`` (that is where precision is lost), per §4.
    """
    return NormalizedAmount(Decimal(text))
