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
