"""M3/T7 — valuation: the one conversion between a proposal and §8:159's gate (§8a:226–231).

A rule proposes legs in the document's own currency; §8:193 books them in pesos. These tests
pin the seam that does the conversion and the evidence it leaves behind:

- **`MXN` converts nothing.** No rate, no source, no date, and no provider is asked — ``None``
  is a closed outcome, not a missing answer;
- **the source rate decides alone** (§8a:227), an **absent** rate may be answered by the
  configured rung, and an **invalid** one is refused without consulting anybody (§8a:228);
- **the conversion is per leg, rounded once** at `MoneyPolicy`'s scale, and the pesos must
  balance exactly. When rounding breaks the balance the answer is a refusal — never a
  synthesized rounding leg (§8a:230);
- **the evidence survives persistence** (§8a:231): `valuation_date`, `valuation_source` and
  `valuation_rate` round-trip through SQLite, and an `MXN` record reads back as
  ``(None, None, None)``.

The rule side is not re-tested here: which legs a document proposes is the rules' contract
(§8:179–185), and this module starts from a proposal already shaped like one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from sat_descarga_masiva.application.ports.accounting import AccountMapping, FXRate, FXSource
from sat_descarga_masiva.contabilidad.fx import (
    FXRateResolutionPolicy,
    ValuationRefusal,
    ValuationRefusalKind,
)
from sat_descarga_masiva.contabilidad.journal import (
    JournalEntryRecord,
    JournalLine,
    JournalLineRecord,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext
from sat_descarga_masiva.contabilidad.validator import (
    PostingDecision,
    PostingEligibilityValidator,
)
from sat_descarga_masiva.contabilidad.valuation import ValuedEntry, value_entry
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalDocumentStatus,
    ParseOutcome,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import RawCfd, RawImpuestos, RawTraslado
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.persistence.sqlite import (
    SqliteJournalEntryStore,
    init_schema,
)

CONTRIBUTOR = Rfc("AAA010101AAA")
EMISOR = Rfc("BBB010101BBB")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SOURCE_HASH = "a" * 64
FECHA = date(2026, 1, 15)
MAPPING_VERSION = "2026.01"
RULE_ID = "4.3"
RULE_VERSION = "1"
SUPPORTED_RULES = {RULE_ID: RULE_VERSION}
GASTO = "84111506"

_ACCOUNTS = {
    AccountRole.GASTO: "601-001",
    AccountRole.IVA_ACRED_PAGADO: "118-001",
    AccountRole.CLEARING: "102-099",
}


class _Provider:
    """A `MappingProvider` over a fixed chart — the port the gate really consumes."""

    def mapping_for(self, client_rfc: Rfc) -> AccountMapping:  # noqa: ARG002 - fixed chart
        return AccountMapping(MAPPING_VERSION, _ACCOUNTS)


class _RecordingRateProvider:
    """An `FXRateProvider` that answers one rate and records every question it was asked."""

    def __init__(self, rate: str | None = None) -> None:
        self._rate = None if rate is None else Decimal(rate)
        self.asked: list[tuple[str, date]] = []

    def get_rate(self, currency: str, on: date) -> FXRate | None:
        self.asked.append((currency, on))
        if self._rate is None:
            return None
        return FXRate(currency=currency, on=on, rate=self._rate)


# --- the fixtures a valuation test starts from ----------------------------------------------


def _document(
    *, moneda: str = "MXN", tipo_cambio: str | None = None, tipo: str = "I"
) -> FiscalDocument:
    """A coherent purchase as *source* facts, in the currency the test needs (§8:193)."""
    raw = RawCfd(
        tipo=tipo,
        version="4.0",
        moneda=moneda,
        tipo_cambio=tipo_cambio,
        emisor_rfc=EMISOR.value,
        receptor_rfc=CONTRIBUTOR.value,
        conceptos=(),
        impuestos=RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),), total_traslados="16.00"
        ),
        total="116.00",
        uuid=UUID.value,
        subtotal="100.00",
        fecha="2026-01-15T10:30:00",
        metodo_pago="PUE",
    )
    parsed = build_fiscal_document(raw, SOURCE_HASH)
    assert parsed.document is not None, parsed.outcome
    assert parsed.outcome is ParseOutcome.PARSED
    # §8:159 — only a *resolved* vigente source can post; the parse's own default is UNKNOWN.
    return replace(parsed.document, status=FiscalDocumentStatus.VIGENTE)


def _line(role: AccountRole, key: str, side: LineSide, amount: str) -> JournalLine:
    return JournalLine(
        account_role=role, line_key=key, side=side, amount=NormalizedAmount(Decimal(amount))
    )


def _proposal(
    *, amounts: tuple[str, str, str] = ("100.00", "16.00", "116.00")
) -> ProposedJournalEntry:
    """The 4.3 shape in the document's own currency — what a rule hands over (§8:181)."""
    base, iva, total = amounts
    return ProposedJournalEntry(
        rule_id=RULE_ID,
        rule_version=RULE_VERSION,
        contributor_rfc=CONTRIBUTOR,
        source_uuid=UUID,
        source_hash=SOURCE_HASH,
        entry_date=FECHA,
        lines=(
            _line(AccountRole.GASTO, "base", LineSide.DEBE, base),
            _line(AccountRole.IVA_ACRED_PAGADO, "iva", LineSide.DEBE, iva),
            _line(AccountRole.CLEARING, "total", LineSide.HABER, total),
        ),
    )


def _value(
    document: FiscalDocument, *, configured: str | None = None
) -> ValuedEntry | ValuationRefusal:
    policy = FXRateResolutionPolicy(
        configured=None if configured is None else _RecordingRateProvider(configured)
    )
    return value_entry(_proposal(), document=document, policy=policy)


def _context(document: FiscalDocument) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR, document=document, perspective=Perspective.RECIBIDO
    )


def _gate(
    document: FiscalDocument, valued: ValuedEntry | ValuationRefusal | None
) -> PostingDecision:
    validator = PostingEligibilityValidator(_Provider(), supported_rules=SUPPORTED_RULES)
    return validator.decide(_context(document), _proposal(), valued=valued)


def _flag_types(decision: PostingDecision) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in decision.review_flags]


# --- A. MXN: nothing converts, nobody is asked ----------------------------------------------


def test_an_mxn_document_is_not_valued_and_no_provider_is_consulted() -> None:
    """§8a:226: `MXN` needs no valuation, so the outcome records none and asks nothing."""
    provider = _RecordingRateProvider("17.5")
    policy = FXRateResolutionPolicy(configured=provider)

    outcome = value_entry(_proposal(), document=_document(), policy=policy)

    assert isinstance(outcome, ValuedEntry)
    assert outcome.valuation is None
    assert outcome.entry == _proposal()  # the legs the rule proposed, untouched
    assert provider.asked == []


# --- B. a stated rate wins on its own -------------------------------------------------------


def test_the_rate_the_document_states_values_every_leg() -> None:
    """§8a:227/230: the source rate decides, and each leg is converted and rounded once."""
    provider = _RecordingRateProvider("99.0")  # would disagree; it is never asked
    policy = FXRateResolutionPolicy(configured=provider)

    outcome = value_entry(
        _proposal(),
        document=_document(moneda="USD", tipo_cambio="17.5"),
        policy=policy,
    )

    assert isinstance(outcome, ValuedEntry)
    assert provider.asked == []
    assert outcome.valuation is not None
    assert outcome.valuation.rate == Decimal("17.5")
    assert outcome.valuation.source is FXSource.SOURCE_DOCUMENT
    assert outcome.valuation.valuation_date == FECHA
    assert [line.amount.amount for line in outcome.entry.lines] == [
        Decimal("1750.00"),
        Decimal("280.00"),
        Decimal("2030.00"),
    ]
    assert outcome.entry.is_balanced()


def test_a_valued_proposal_is_posted_with_its_evidence() -> None:
    """§8:159/§8a:231: the gate posts the pesos and the decision carries the valuation."""
    valued = _value(_document(moneda="USD", tipo_cambio="17.5"))

    decision = _gate(_document(moneda="USD", tipo_cambio="17.5"), valued)

    assert decision.posting_state is PostingState.POSTED
    assert _flag_types(decision) == []
    assert decision.valuation is not None
    assert decision.valuation.rate == Decimal("17.5")
    assert decision.entry is not None
    assert [line.amount.amount for line in decision.entry.lines] == [
        Decimal("1750.00"),
        Decimal("280.00"),
        Decimal("2030.00"),
    ]


# --- C/D. an absent rate: the configured rung may answer, or nobody does ---------------------


def test_an_absent_rate_may_be_answered_by_the_configured_rung() -> None:
    """§8:193/§8a:228: a provider is a fallback for a rate the document does *not* state."""
    provider = _RecordingRateProvider("20.0")
    policy = FXRateResolutionPolicy(configured=provider)
    document = _document(moneda="USD")

    outcome = value_entry(_proposal(), document=document, policy=policy)

    assert isinstance(outcome, ValuedEntry)
    assert provider.asked == [("USD", FECHA)]
    assert outcome.valuation is not None
    assert outcome.valuation.source is FXSource.CONFIGURED
    assert outcome.valuation.rate == Decimal("20.0")
    assert [line.amount.amount for line in outcome.entry.lines] == [
        Decimal("2000.00"),
        Decimal("320.00"),
        Decimal("2320.00"),
    ]


def test_an_absent_rate_with_no_answer_is_ambiguous_fx_never_a_guess() -> None:
    """§8a:229: priority exhausted is `AMBIGUOUS_FX` on a `PROPOSED` entry."""
    document = _document(moneda="USD")

    outcome = _value(document)

    assert isinstance(outcome, ValuationRefusal)
    assert outcome.kind is ValuationRefusalKind.PRIORITY_EXHAUSTED
    assert outcome.flag.flag_type is ReviewFlagType.AMBIGUOUS_FX

    decision = _gate(document, outcome)
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.AMBIGUOUS_FX]


# --- E. a stated-but-unusable rate is refused, never repaired --------------------------------


@pytest.mark.parametrize(
    "rate",
    ["0", "-17.5", "NaN", "Infinity"],
    ids=["zero", "negative", "nan", "infinity"],
)
def test_a_stated_rate_that_cannot_be_used_is_refused_without_a_fallback(rate: str) -> None:
    """§8a:228: an invalid source fact must never disappear behind a configured one."""
    provider = _RecordingRateProvider("20.0")
    policy = FXRateResolutionPolicy(configured=provider)
    document = _document(moneda="USD", tipo_cambio=rate)

    outcome = value_entry(_proposal(), document=document, policy=policy)

    assert isinstance(outcome, ValuationRefusal)
    assert outcome.kind is ValuationRefusalKind.INVALID_SOURCE_RATE
    assert provider.asked == []  # absent and invalid take different paths

    decision = _gate(document, outcome)
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.AMBIGUOUS_FX]


# --- F. per-leg rounding that breaks the balance --------------------------------------------


def test_conversion_that_cannot_balance_is_refused_and_no_leg_is_synthesized() -> None:
    """§8a:230: 100 + 16 at 2.00245 round to 200.25 + 32.04 ≠ 232.28 — and nothing plugs it."""
    document = _document(moneda="USD", tipo_cambio="2.00245")

    outcome = _value(document)

    assert isinstance(outcome, ValuationRefusal)
    assert outcome.kind is ValuationRefusalKind.POST_CONVERSION_IMBALANCE

    decision = _gate(document, outcome)
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.UNBALANCED_ENTRY]
    # The proposal keeps exactly the legs the rule wrote: no third-cent leg, no difference leg.
    assert decision.entry is not None
    assert [line.line_key for line in decision.entry.lines] == ["base", "iva", "total"]


# --- G/H. the evidence survives persistence -------------------------------------------------


def _record(
    *,
    valuation: tuple[date, FXSource, Decimal] | None = None,
) -> JournalEntryRecord:
    """One posting as the ledger writes it, with or without §8a:231's evidence."""
    valuation_date, valuation_source, valuation_rate = (
        (None, None, None) if valuation is None else valuation
    )
    return JournalEntryRecord(
        entry_key="k-1",
        contributor_rfc=CONTRIBUTOR,
        source_uuid=UUID,
        source_hash=SOURCE_HASH,
        rule_id=RULE_ID,
        rule_version=RULE_VERSION,
        mapping_version=MAPPING_VERSION,
        policy_version="2",
        posting_state=PostingState.POSTED,
        entry_date=FECHA,
        recorded_at=datetime(2026, 2, 1, 9, 30, tzinfo=UTC),
        lines=(
            JournalLineRecord(
                account_role=AccountRole.GASTO,
                line_key="base",
                ordinal=0,
                side=LineSide.DEBE,
                amount=NormalizedAmount(Decimal("1750.00")),
                resolved_account="601-001",
            ),
        ),
        valuation_date=valuation_date,
        valuation_source=valuation_source,
        valuation_rate=valuation_rate,
    )


def _store() -> SqliteJournalEntryStore:
    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    return SqliteJournalEntryStore(conn)


def test_a_conversion_reads_back_exactly_as_it_was_recorded() -> None:
    """§8a:231: date, source and rate round-trip through SQLite without being re-derived."""
    store = _store()
    record = _record(valuation=(FECHA, FXSource.SOURCE_DOCUMENT, Decimal("17.5")))

    store.append(record)

    assert store.get(record.entry_key) == record
    stored = store.get(record.entry_key)
    assert stored is not None
    assert (stored.valuation_date, stored.valuation_source, stored.valuation_rate) == (
        FECHA,
        FXSource.SOURCE_DOCUMENT,
        Decimal("17.5"),
    )


def test_an_mxn_posting_reads_back_with_no_valuation_evidence() -> None:
    """§8a:226: nothing converted, so the three columns stay NULL — not zero, not blank."""
    store = _store()
    record = _record()

    store.append(record)

    stored = store.get(record.entry_key)
    assert stored is not None
    assert (stored.valuation_date, stored.valuation_source, stored.valuation_rate) == (
        None,
        None,
        None,
    )


def test_a_half_recorded_valuation_is_refused_by_the_record_itself() -> None:
    """§8a:231: a rate without its date and source cannot re-derive anything, so it never exists."""
    with pytest.raises(ValueError, match="recorded whole"):
        _record(valuation=(None, FXSource.SOURCE_DOCUMENT, Decimal("17.5")))


# --- the REP header is not a valuation ------------------------------------------------------


def test_a_rep_header_is_not_valued_here() -> None:
    """§8:192/§8a:232: a `P` comprobante's header states no currency the entry is valued in."""
    provider = _RecordingRateProvider("20.0")
    policy = FXRateResolutionPolicy(configured=provider)

    outcome = value_entry(
        _proposal(), document=_document(tipo="P", moneda="USD", tipo_cambio="17.5"), policy=policy
    )

    assert isinstance(outcome, ValuedEntry)
    assert outcome.valuation is None
    assert provider.asked == []
