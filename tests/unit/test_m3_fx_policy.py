"""T7b: `FXRateResolutionPolicy` — which rate values a foreign entry, and what a refusal is.

§8:193 fixes an *order* of rate sources; §8a:226–229 rule what each rung may do. These tests pin
the four claims that order rests on, because each is a place where a plausible implementation
would be wrong:

- **the source rate decides alone.** A usable ``TipoCambio`` values the entry with no provider
  call at all — the provider is wired and would answer *differently*, and the assertion is that
  it was never asked (§8a:227). Precedence is availability, not a poll;
- **absent and invalid are different facts.** Absent ⇒ the fallback is asked. Present-but-zero,
  negative or non-finite ⇒ `ValuationRefusal`, and the provider call count stays zero, so a bad
  source rate cannot disappear behind a configured one (§8a:228);
- **the national currency is not a valuation.** ``MXN`` answers ``None`` — nothing converted,
  nothing to record, no rung consulted — and ``None`` never means "could not determine"; every
  such case is a refusal instead (§8a:229);
- **a provider bug is not a document's problem.** An answer for another currency or another date
  raises, because `AMBIGUOUS_FX` would file an engine bug in a client's review queue.

No test here rounds anything: valuation returns the rate exactly, and §8a:230's per-leg
quantization belongs to the conversion that will consume it.
"""

from datetime import date
from decimal import Decimal

import pytest

from sat_descarga_masiva.application.ports.accounting import FXRate, FXSource
from sat_descarga_masiva.contabilidad.fx import (
    FXRateResolutionPolicy,
    FXRequest,
    Valuation,
    ValuationRefusal,
    ValuationRefusalKind,
)
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalDocumentStatus,
    Impuestos,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
SOURCE_HASH = "a" * 64
#: §8a:231's valuation date is the document's own Fecha — never "today", never the run's clock.
FECHA = date(2026, 1, 15)
#: A date the document is *not* dated, for the stale-answer contract test.
OTRA_FECHA = date(2026, 1, 14)


def _document(
    *,
    moneda: str = "USD",
    tipo_cambio: Decimal | None = None,
    fecha: date | None = FECHA,
) -> FiscalDocument:
    return FiscalDocument(
        tipo="I",
        version="4.0",
        moneda=moneda,
        tipo_cambio=tipo_cambio,
        emisor_rfc=CONTRIBUTOR,
        receptor_rfc=RECEPTOR,
        conceptos=(),
        impuestos=Impuestos(),
        total=NormalizedAmount(Decimal("116.00")),
        source_hash=SOURCE_HASH,
        status=FiscalDocumentStatus.VIGENTE,
        source_uuid=Uuid("4e80345d-917f-40bb-a98f-4a73939353c5"),
        fecha=fecha,
    )


class _Provider:
    """An `FXRateProvider` that records every question — the port the policy really consumes.

    Recording is the point: "the provider was never asked" is an assertion about `asked`, not a
    promise in a docstring (§8a:227/228).
    """

    def __init__(
        self,
        rate: FXRate | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.rate = rate
        self.error = error
        self.asked: list[tuple[str, date]] = []

    def get_rate(self, currency: str, on: date) -> FXRate | None:
        self.asked.append((currency, on))
        if self.error is not None:
            raise self.error
        return self.rate


def _policy(*, configured: _Provider | None = None) -> FXRateResolutionPolicy:
    return FXRateResolutionPolicy(configured=configured)


def _request(*, currency: str = "USD", rate: Decimal | None = None) -> FXRequest:
    return FXRequest(currency=currency, on=FECHA, source_rate=rate)


def _refusal(outcome: Valuation | ValuationRefusal | None) -> ValuationRefusal:
    assert isinstance(outcome, ValuationRefusal), f"expected a refusal, got {outcome!r}"
    return outcome


def _valuation(outcome: Valuation | ValuationRefusal | None) -> Valuation:
    assert isinstance(outcome, Valuation), f"expected a valuation, got {outcome!r}"
    return outcome


# --- the national currency is not a valuation ------------------------------------------


def test_the_national_currency_needs_no_valuation_and_asks_nobody() -> None:
    """§8:193/§8a:231: nothing converts, so nothing is valued and no rung is probed."""
    provider = _Provider(FXRate(currency="USD", on=FECHA, rate=Decimal("18.00")))
    assert _policy(configured=provider).resolve(_request(currency="MXN")) is None
    assert provider.asked == []


def test_the_national_currency_stays_unconverted_even_with_a_stated_rate() -> None:
    """A ``TipoCambio`` on an MXN document is noise: ``MXN`` short-circuits before any rate."""
    provider = _Provider(FXRate(currency="MXN", on=FECHA, rate=Decimal("18.00")))
    outcome = _policy(configured=provider).resolve(_request(currency="MXN", rate=Decimal("18.00")))
    assert outcome is None
    assert provider.asked == []


def test_currency_comparison_ignores_case_but_accepts_no_other_code() -> None:
    """The national check reads the code the way the fiscal model writes it, nothing looser."""
    assert _policy().resolve(FXRequest(currency="mxn", on=FECHA, source_rate=None)) is None


# --- the source rate decides alone (§8a:227) -------------------------------------------


def test_the_documents_own_rate_values_the_entry_and_asks_nobody() -> None:
    """§8a:227: the source rate is not corroborated — the wired provider is never consulted."""
    provider = _Provider(FXRate(currency="USD", on=FECHA, rate=Decimal("18.00")))
    valuation = _valuation(_policy(configured=provider).resolve(_request(rate=Decimal("17.5"))))
    assert valuation.rate == Decimal("17.5")
    assert valuation.source is FXSource.SOURCE_DOCUMENT
    assert valuation.valuation_date == FECHA
    assert provider.asked == []


def test_a_disagreeing_provider_never_overrides_the_source_rate() -> None:
    """§8a:227: "two rungs disagree" is not a state this policy can produce."""
    provider = _Provider(FXRate(currency="USD", on=FECHA, rate=Decimal("99.99")))
    valuation = _valuation(_policy(configured=provider).resolve(_request(rate=Decimal("17.5"))))
    assert valuation.rate == Decimal("17.5")
    assert provider.asked == []


def test_the_source_rate_is_carried_exactly_without_rounding() -> None:
    """§8a:231: the ledger records the number that was used; §8a:230 rounds per leg, later."""
    valuation = _valuation(_policy().resolve(_request(rate=Decimal("17.123456"))))
    assert valuation.rate == Decimal("17.123456")
    assert valuation.rate.as_tuple().exponent == -6


# --- absent is not invalid (§8a:228) ---------------------------------------------------


def test_an_absent_source_rate_asks_the_configured_source() -> None:
    """A provider is a fallback for a rate the document does not state."""
    provider = _Provider(FXRate(currency="USD", on=FECHA, rate=Decimal("18.25")))
    valuation = _valuation(_policy(configured=provider).resolve(_request(rate=None)))
    assert valuation.rate == Decimal("18.25")
    assert valuation.source is FXSource.CONFIGURED
    assert valuation.valuation_date == FECHA
    assert provider.asked == [("USD", FECHA)]


@pytest.mark.parametrize(
    "stated",
    [
        pytest.param(Decimal("0"), id="zero"),
        pytest.param(Decimal("0.000000"), id="zero-six-decimals"),
        pytest.param(Decimal("-1"), id="negative"),
        pytest.param(Decimal("NaN"), id="nan"),
        pytest.param(Decimal("Infinity"), id="positive-infinity"),
        pytest.param(Decimal("-Infinity"), id="negative-infinity"),
    ],
)
def test_an_invalid_source_rate_is_refused_and_never_repaired(stated: Decimal) -> None:
    """§8a:228: a stated-but-unusable rate is refused — the fallback stays silent."""
    provider = _Provider(FXRate(currency="USD", on=FECHA, rate=Decimal("18.25")))
    refusal = _refusal(_policy(configured=provider).resolve(_request(rate=stated)))
    assert refusal.kind is ValuationRefusalKind.INVALID_SOURCE_RATE
    assert provider.asked == []


def test_an_invalid_source_rate_flags_ambiguous_fx_and_quotes_the_value() -> None:
    """§8a:229 keeps `AMBIGUOUS_FX`'s single meaning, and §8:167 makes the reason explicit."""
    refusal = _refusal(_policy().resolve(_request(rate=Decimal("0"))))
    assert refusal.flag.flag_type is ReviewFlagType.AMBIGUOUS_FX
    assert "0" in refusal.flag.reason
    assert "USD" in refusal.flag.reason
    assert FECHA.isoformat() in refusal.flag.reason


def test_a_request_accepts_an_unusable_rate_without_rejecting_it() -> None:
    """The distinction must survive the seam: an invalid rate is data the policy refuses."""
    request = FXRequest(currency="USD", on=FECHA, source_rate=Decimal("0"))
    assert request.source_rate == Decimal("0")


# --- exhaustion (§8a:229) --------------------------------------------------------------


def test_no_stated_rate_and_no_configured_source_is_a_refusal() -> None:
    """§8a:229: priority exhausted is `AMBIGUOUS_FX`, not an exception and not a zero rate."""
    refusal = _refusal(_policy().resolve(_request(rate=None)))
    assert refusal.kind is ValuationRefusalKind.PRIORITY_EXHAUSTED
    assert refusal.flag.flag_type is ReviewFlagType.AMBIGUOUS_FX


def test_a_configured_source_that_answers_nothing_is_a_refusal() -> None:
    """``None`` from the fallback is "cannot answer", and the chain has nothing left to ask."""
    provider = _Provider(None)
    refusal = _refusal(_policy(configured=provider).resolve(_request(rate=None)))
    assert refusal.kind is ValuationRefusalKind.PRIORITY_EXHAUSTED
    assert provider.asked == [("USD", FECHA)]


# --- a provider bug is not a document's problem ----------------------------------------


def test_a_rate_for_another_currency_is_a_contract_violation() -> None:
    """§8a:228: an answer to another question is an adapter bug, never a review case."""
    provider = _Provider(FXRate(currency="EUR", on=FECHA, rate=Decimal("18.00")))
    with pytest.raises(ValueError, match="answered EUR"):
        _policy(configured=provider).resolve(_request(rate=None))


def test_a_rate_for_another_date_is_a_contract_violation() -> None:
    """§8a:231: a stale rate is a different document's rate, not a lesser answer to this one."""
    provider = _Provider(FXRate(currency="USD", on=OTRA_FECHA, rate=Decimal("18.00")))
    with pytest.raises(ValueError, match="stale rate"):
        _policy(configured=provider).resolve(_request(rate=None))


def test_a_failing_provider_propagates_instead_of_becoming_a_refusal() -> None:
    """An adapter that raises has broken, and a broken engine is not a flagged document."""
    provider = _Provider(error=RuntimeError("the rate service is unreachable"))
    with pytest.raises(RuntimeError, match="unreachable"):
        _policy(configured=provider).resolve(_request(rate=None))


# --- the seam: a document's own facts, and the port's guards ----------------------------


def test_a_request_is_read_from_the_documents_own_facts() -> None:
    """§8a:231: the currency, the date and the stated rate all come from the document."""
    request = FXRequest.of(_document(moneda="USD", tipo_cambio=Decimal("17.5")))
    assert (request.currency, request.on, request.source_rate) == ("USD", FECHA, Decimal("17.5"))


def test_a_document_that_states_no_rate_asks_for_none() -> None:
    """Absence is preserved as ``None``; it is not defaulted to 1 or to any other number."""
    assert FXRequest.of(_document(tipo_cambio=None)).source_rate is None


def test_an_undated_document_cannot_be_valued() -> None:
    """§8a:231: the valuation date is the document's Fecha — the run's clock is not that date."""
    with pytest.raises(ValueError, match="undated"):
        FXRequest.of(_document(fecha=None))


def test_a_request_must_name_the_currency_it_asks_about() -> None:
    with pytest.raises(ValueError, match="currency it is asked to value"):
        FXRequest(currency="  ", on=FECHA, source_rate=Decimal("17.5"))


@pytest.mark.parametrize(
    "bad_rate",
    [
        pytest.param(Decimal("0"), id="zero"),
        pytest.param(Decimal("-0.5"), id="negative"),
        pytest.param(Decimal("NaN"), id="nan"),
        pytest.param(Decimal("Infinity"), id="positive-infinity"),
        pytest.param(Decimal("-Infinity"), id="negative-infinity"),
    ],
)
def test_the_port_rejects_a_rate_that_could_not_value_anything(bad_rate: Decimal) -> None:
    """§8:193: a rung that cannot state a usable rate answers ``None``, it does not answer this."""
    with pytest.raises(ValueError, match="not a usable rate"):
        FXRate(currency="USD", on=FECHA, rate=bad_rate)


def test_the_port_rejects_a_rate_that_names_no_currency() -> None:
    with pytest.raises(ValueError, match="currency it prices"):
        FXRate(currency=" ", on=FECHA, rate=Decimal("17.5"))


def test_a_valuation_must_be_positive_and_finite() -> None:
    """`Valuation` is the *used* rate: a refusal is a `ValuationRefusal`, never a zero here."""
    with pytest.raises(ValueError, match="is not a valuation"):
        Valuation(rate=Decimal("0"), source=FXSource.CONFIGURED, valuation_date=FECHA)


def test_a_refusal_must_say_why_nothing_was_valued() -> None:
    """§8:167: a silent non-valuation is indistinguishable from a document nobody looked at."""
    with pytest.raises(ValueError, match="must say why"):
        ValuationRefusal(kind=ValuationRefusalKind.PRIORITY_EXHAUSTED, detail=" ")


def test_the_persisted_source_words_are_stable() -> None:
    """§8a:231: ``valuation_source`` is written to the ledger, so its values are schema."""
    assert {source.value for source in FXSource} == {"source_document", "configured"}


def test_the_same_question_always_gets_the_same_answer() -> None:
    """§8a:226: valuation is deterministic — two runs cannot value one entry two ways."""
    policy = _policy(configured=_Provider(FXRate(currency="USD", on=FECHA, rate=Decimal("18.25"))))
    first = policy.resolve(_request(rate=None))
    second = _policy(
        configured=_Provider(FXRate(currency="USD", on=FECHA, rate=Decimal("18.25")))
    ).resolve(_request(rate=None))
    assert first == second
    assert _valuation(first) == _valuation(second)


def test_a_foreign_currency_other_than_usd_takes_the_same_path() -> None:
    """§8:193: ``MXN`` is the only currency with a shortcut; every other code is valued."""
    valuation = _valuation(_policy().resolve(_request(currency="CAD", rate=Decimal("13.10"))))
    assert valuation.source is FXSource.SOURCE_DOCUMENT
    refused = _refusal(_policy().resolve(FXRequest(currency="CAD", on=FECHA, source_rate=None)))
    assert refused.kind is ValuationRefusalKind.PRIORITY_EXHAUSTED
