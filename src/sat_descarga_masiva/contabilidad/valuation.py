"""Valuation — the conversion step between a proposal and §8:159's gate (§8a:226/230).

A rule proposes legs in the document's own currency: it states the amounts the CFDI states.
§8:193 says a foreign-currency entry is *booked in pesos*, and §8a:226 fixes where that
happens — once, in `contabilidad/`, **before** `PostingEligibilityValidator.decide()`, and
recorded with the entry it produced. This module is that step and nothing else:

- **which rate** is not decided here. `contabilidad.fx.FXRateResolutionPolicy` owns the
  chain (§8a:226–229); this module asks it and obeys the answer, and a refusal is carried
  through untouched;
- **the conversion is per leg and rounded once**, at `MoneyPolicy`'s MXN scale with
  `ROUND_HALF_UP` (§8a:230). `Σ Debe` and `Σ Haber` are then compared as `Decimal`s: if the
  converted entry no longer balances, the outcome is a refusal and **no leg is synthesized,
  dropped or altered to force balance** — no rounding plug, no absorbed cent, no difference
  leg;
- **amounts, roles, keys and dates are untouched otherwise.** Valuation changes what a leg is
  worth in pesos, never the bookkeeping shape;
- **`MXN` is not a valuation at all**: nothing converts, so the outcome carries no valuation
  record and no provider is probed. A `tipo = 'P'` comprobante is the same case for another
  reason — its header states no currency the entry is valued in (§8:192), and the REP-specific
  rung is out of this slice (§8a:232).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from sat_descarga_masiva.contabilidad.fx import (
    FXRateResolutionPolicy,
    FXRequest,
    Valuation,
    ValuationRefusal,
    ValuationRefusalKind,
)
from sat_descarga_masiva.contabilidad.journal import JournalLine, ProposedJournalEntry
from sat_descarga_masiva.domain.enums.comprobante import TipoComprobante
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.policy.money import (
    MoneyPolicy,
    NormalizedAmount,
    UnsupportedCurrency,
)

#: §8:193's one currency this engine can book without a valuation decision.
_NATIONAL_CURRENCY = "MXN"

#: The engine's one monetary policy: the scale and rounding §8a:230 requires.
_MONEY = MoneyPolicy()


@dataclass(frozen=True)
class ValuedEntry:
    """The proposal the gate may post, plus how it became pesos (§8a:231).

    ``valuation is None`` is a *closed* outcome, not a missing one: the document was already in
    pesos (or is a REP whose header is not valued here), so no conversion happened and no
    valuation facts are recorded. A converted entry is always balanced — an imbalance is
    refused by :func:`value_entry` before this type is built.
    """

    entry: ProposedJournalEntry
    valuation: Valuation | None = None

    def __post_init__(self) -> None:
        if self.valuation is not None and not self.entry.is_balanced():
            raise ValueError(
                f"a valued entry must balance after conversion: Debe {self.entry.debe_total()}"
                f" != Haber {self.entry.haber_total()} — §8a:230 refuses the conversion, so"
                " this type is never built from one"
            )


def value_entry(
    entry: ProposedJournalEntry,
    *,
    document: FiscalDocument,
    policy: FXRateResolutionPolicy,
) -> ValuedEntry | ValuationRefusal:
    """``entry`` in pesos, or the refusal that keeps it out of the ledger (§8a:226–230).

    Called once per proposal, before the gate: the policy decides whether a rate exists, and
    the answer — `ValuedEntry` or `ValuationRefusal` — travels to
    `PostingEligibilityValidator.decide`, which turns it into the posting decision. Nothing
    here reads the ledger, a clock or an adapter: the policy is the only collaborator.
    """
    if TipoComprobante.of(document.tipo) is TipoComprobante.PAGO:
        # §8:192 — a REP's header carries no currency the entry is valued in, so its legs stay
        # exactly as proposed; the REP-specific rung is explicitly out of this slice (§8a:232).
        return ValuedEntry(entry=entry)
    outcome = policy.resolve(FXRequest.of(document))
    if outcome is None:
        return ValuedEntry(entry=entry)
    if isinstance(outcome, ValuationRefusal):
        return outcome
    return _converted(entry, outcome, currency=document.moneda)


def _converted(
    entry: ProposedJournalEntry, valuation: Valuation, *, currency: str
) -> ValuedEntry | ValuationRefusal:
    """Convert every leg independently, then insist the result still balances (§8a:230)."""
    converted: list[JournalLine] = []
    for line in entry.lines:
        amount = _in_pesos(line.amount.amount * valuation.rate)
        if amount is None:
            return ValuationRefusal(
                kind=ValuationRefusalKind.CONVERSION_NOT_BOOKABLE,
                detail=(
                    f"leg {line.line_key!r} of {line.amount.amount} {currency} at"
                    f" {valuation.rate} rounds to a non-positive peso amount: a leg is never"
                    " dropped, absorbed or synthesized to make an entry postable (§8a:230)"
                ),
            )
        converted.append(
            JournalLine(
                account_role=line.account_role,
                line_key=line.line_key,
                side=line.side,
                amount=amount,
            )
        )
    valued = replace(entry, lines=tuple(converted))
    if not valued.is_balanced():
        return ValuationRefusal(
            kind=ValuationRefusalKind.POST_CONVERSION_IMBALANCE,
            detail=(
                f"the {currency} entry converted at {valuation.rate} does not balance after"
                f" per-leg rounding: Debe {valued.debe_total()} != Haber"
                f" {valued.haber_total()} — no rounding plug or difference leg is synthesized"
                " (§8a:230)"
            ),
        )
    return ValuedEntry(entry=valued, valuation=valuation)


def _in_pesos(value: Decimal) -> NormalizedAmount | None:
    """One leg in pesos at `MoneyPolicy`'s MXN scale, or ``None`` when nothing survives it.

    ``None`` is not a rounding detail to paper over: a leg that quantizes to zero states no
    movement, and dropping it would change the bookkeeping shape the rule proposed.
    """
    normalized = _MONEY.normalize(value, _NATIONAL_CURRENCY)
    if isinstance(normalized, UnsupportedCurrency):  # pragma: no cover - MXN has a scale
        raise ValueError(f"{_NATIONAL_CURRENCY} has no MoneyPolicy scale: {normalized.reason}")
    if normalized.amount <= 0:
        return None
    return normalized
