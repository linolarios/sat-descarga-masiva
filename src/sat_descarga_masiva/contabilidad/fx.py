"""Which rate values a non-MXN entry, and what happens when none can (§8:193, §8a:226–229).

§8:193 says a foreign-currency comprobante books in pesos; it does not say who converts it or
in what order anybody is asked. That ordering is *policy*, and it lives here rather than in a
rule (§8a:226):

- **resolved once, before the rules decide.** Rules keep proposing legs in the document's own
  currency — they state the amounts the CFDI states — and valuation happens once, upstream.
  No rule `rule_version` moves because of it, and a posted entry is never re-valued;
- **the source rate decides on its own** (§8a:227). A usable header ``TipoCambio`` is not
  corroborated, cross-checked or averaged against anything: §8:193's order is a precedence of
  *availability*, not a poll of opinions, so a second rung answering differently is not a state
  this module can produce;
- **absent and invalid are different facts** (§8a:228). A provider is a fallback for a rate the
  document does **not** state; a rate the document *does* state and that cannot be used (zero,
  negative, non-finite) is **refused**, because letting a configured number replace the
  document's own arithmetic would value the entry at a rate the document never stated;
- **`MXN` is not a valuation at all.** Nothing is converted, so the outcome is ``None`` — no
  rate, no source, no date — and no provider is probed. That is a closed, deliberate outcome,
  not a missing answer: ``None`` never means "could not determine".

When no answer exists the outcome is a `ValuationRefusal` carrying `AMBIGUOUS_FX` (§8a:229),
whose established meaning — *no deterministic MXN valuation was determined* — this keeps
exactly. A refusal is a `PROPOSED` entry with an open flag, never a guess.

Nothing here converts an amount or rounds one: §8a:230's per-leg quantization belongs to the
conversion step, and the rate this module returns is the exact number that step must use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sat_descarga_masiva.application.ports.accounting import (
    FXRate,
    FXRateProvider,
    FXSource,
)
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlagType

#: The one currency this engine can book without a valuation decision (§8:193).
_NATIONAL_CURRENCY = "MXN"


def _is_usable(rate: Decimal | None) -> bool:
    """Whether ``rate`` can value an entry: stated, finite and positive (§8a:227).

    ``is_finite()`` is tested before ``> 0`` because comparing ``Decimal("NaN")`` raises
    ``InvalidOperation``; a guard that cannot survive the value it rejects is no guard.
    """
    return rate is not None and rate.is_finite() and rate > 0


def _same_currency(left: str, right: str) -> bool:
    """Currency codes compared the way the fiscal model writes them: case-insensitively.

    Comparison is case-insensitive; *acceptance* is not. A provider answering a different code
    still fails the contract — this only keeps ``usd`` from being read as a second currency.
    """
    return left.strip().upper() == right.strip().upper()


@dataclass(frozen=True)
class FXRequest:
    """The question valuation asks: which currency, dated when, and what the document states.

    ``source_rate`` is the header ``TipoCambio`` **as parsed**, carried inertly and never
    validated here. ``None`` means the document states no rate; a ``Decimal`` that is not finite
    or not positive means the document states one that cannot be used — two different facts that
    take two different paths (§8a:228). Rejecting an unusable value in this constructor would
    turn a refusal into an exception and lose the distinction on the way in.
    """

    currency: str
    on: date
    source_rate: Decimal | None

    def __post_init__(self) -> None:
        if not self.currency.strip():
            raise ValueError("an FX request needs the currency it is asked to value")

    @classmethod
    def of(cls, document: FiscalDocument) -> FXRequest:
        """The request a document states, dated on its own ``Fecha`` (§8a:231).

        An undated document cannot be valued: §8a:209's date is the valuation date, and this
        engine never substitutes "today" or the run's clock, so the caller gets a ``ValueError``
        and the document is refused upstream instead of being valued on a date it does not have.
        """
        if document.fecha is None:
            raise ValueError(
                "an undated document cannot be valued: §8a:231 values on the document's own"
                " Fecha, and the run's clock is not that date"
            )
        return cls(
            currency=document.moneda,
            on=document.fecha,
            source_rate=document.tipo_cambio,
        )


@dataclass(frozen=True)
class Valuation:
    """A rate the engine will actually use, and where it came from (§8a:231).

    Deliberately narrower than the evidence the posting records: there is no ``policy_version``
    field because the engine has exactly one policy version, kept where a posting already keeps
    it. Duplicating it here would create a second authority for the same fact.
    """

    rate: Decimal
    source: FXSource
    valuation_date: date

    def __post_init__(self) -> None:
        if not self.rate.is_finite() or self.rate <= 0:
            raise ValueError(
                f"{self.rate} is not a valuation: a rate that values an entry is positive and"
                " finite, and a refusal is a ValuationRefusal (§8a:229)"
            )


class ValuationRefusalKind(StrEnum):
    """Why no rate could be resolved (§8a:228/229).

    ``kind`` is the machine-readable bit, so a reworded ``detail`` can never change a decision —
    the same split `ClassificationRefusalKind` makes.
    """

    #: The document states a rate it cannot use (zero, negative, non-finite).
    INVALID_SOURCE_RATE = "invalid_source_rate"
    #: The document states none, and no rung answered.
    PRIORITY_EXHAUSTED = "priority_exhausted"


@dataclass(frozen=True)
class ValuationRefusal:
    """No deterministic valuation exists, and the entry may not post (§8a:229).

    Always `AMBIGUOUS_FX`: the flag keeps its established meaning of *not determined* rather than
    gaining a second one for "contradicted", and no new flag type is introduced.
    """

    kind: ValuationRefusalKind
    detail: str

    def __post_init__(self) -> None:
        if not self.detail.strip():
            raise ValueError(
                "a valuation refusal must say why nothing was valued (§8:167): a silent"
                " non-valuation is indistinguishable from a document nobody looked at"
            )

    @property
    def flag(self) -> ReviewFlag:
        """The review flag this refusal opens — ``AMBIGUOUS_FX``, with the stated reason."""
        return ReviewFlag(ReviewFlagType.AMBIGUOUS_FX, self.detail)


#: The answer to "at what rate is this entry booked?": a valuation, why there is none, or
#: ``None`` for a national-currency entry that needed no valuation at all.
ValuationOutcome = Valuation | ValuationRefusal | None


@dataclass(frozen=True)
class FXRateResolutionPolicy:
    """§8:193's chain in its fixed order: the source document's rate, then a configured one.

    The policy owns the *decisions* — is a rate stated, is it usable, may the fallback be asked,
    is the answer a refusal — while adapters own the numbers. ``configured`` is optional because
    a client may have stated no rates at all; that is not an error, it simply means the chain has
    one rung.
    """

    configured: FXRateProvider | None = None

    def resolve(self, request: FXRequest) -> ValuationOutcome:
        """Value ``request`` at the first rung that can answer (§8a:226–229).

        ``None`` for the national currency — nothing converts, so nothing is valued and no rung
        is consulted; a `Valuation` from the first rung that answers; a `ValuationRefusal` when a
        stated rate cannot be used or when no rung answers an absent one. A provider that breaks
        its contract raises, because that is an adapter bug, not the document's problem.
        """
        if _same_currency(request.currency, _NATIONAL_CURRENCY):
            return None
        if request.source_rate is None:
            return self._from_configured(request)
        if not _is_usable(request.source_rate):
            # §8a:228 — a stated-but-unusable rate is refused, never repaired by a fallback.
            return ValuationRefusal(
                kind=ValuationRefusalKind.INVALID_SOURCE_RATE,
                detail=(
                    f"the document states TipoCambio {request.source_rate} for"
                    f" {request.currency} on {request.on.isoformat()}: zero, negative or"
                    " non-finite is not a rate, and a configured rate does not replace a rate"
                    " the document itself states (§8a:228)"
                ),
            )
        # §8a:227 — the document's own arithmetic decides, and nothing else is asked.
        return Valuation(
            rate=request.source_rate,
            source=FXSource.SOURCE_DOCUMENT,
            valuation_date=request.on,
        )

    def _from_configured(self, request: FXRequest) -> ValuationOutcome:
        """The fallback rung, asked only because the document states no rate at all."""
        if self.configured is None:
            return ValuationRefusal(
                kind=ValuationRefusalKind.PRIORITY_EXHAUSTED,
                detail=(
                    f"the document states no TipoCambio for {request.currency} on"
                    f" {request.on.isoformat()} and no other rate source is configured:"
                    " §8:193's chain has nothing left to ask"
                ),
            )
        rate = self.configured.get_rate(request.currency, request.on)
        if rate is None:
            return ValuationRefusal(
                kind=ValuationRefusalKind.PRIORITY_EXHAUSTED,
                detail=(
                    f"the document states no TipoCambio for {request.currency} on"
                    f" {request.on.isoformat()} and the configured rate source answered nothing:"
                    " §8:193's chain has nothing left to ask"
                ),
            )
        _require_an_answer_to_this_question(request, rate)
        return Valuation(
            rate=rate.rate,
            source=FXSource.CONFIGURED,
            valuation_date=request.on,
        )


def _require_an_answer_to_this_question(request: FXRequest, rate: FXRate) -> None:
    """Fail loudly when a provider answers a question nobody asked (§8a:228).

    A rate for another currency, or a valid rate for another date, is not a lesser answer to this
    one — it is an adapter contract violation. Refusing it as `AMBIGUOUS_FX` would file an engine
    bug as a document's problem, so it raises instead.
    """
    if not _same_currency(rate.currency, request.currency):
        raise ValueError(
            f"the rate source was asked for {request.currency} and answered {rate.currency}:"
            " an answer to another question is not a valuation"
        )
    if rate.on != request.on:
        raise ValueError(
            f"the rate source answered {rate.on.isoformat()} for a request dated"
            f" {request.on.isoformat()}: §8a:231 values on the document's own Fecha, and a"
            " stale rate is a different document's rate"
        )
