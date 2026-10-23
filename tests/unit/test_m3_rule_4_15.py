"""M3 step T1/T2: rule 4.15 — a cancelled source, reversed or skipped (§8:194).

§8:194 is the one place where a *fiscal state* — not a document shape — decides the accounting
answer, and it gives two answers to two different histories:

- **4.15a** — cancelled and never posted: nothing moved, so nothing is reversed. The entry is
  ``SKIPPED``, carries no legs and needs no human decision (§8:161).
- **4.15b** — cancelled after being posted: the original record is permanent (§8:166), so the
  correction is a new compensating entry that inverts every leg — same role, same amount, same
  *posted* account, opposite side — dated by the cancellation's effective date and flagged
  ``CANCELLED_AFTER_POSTING``. A human accepts it before it becomes a fact.

What these tests pin, in that order: the exact inversion (leg for leg), the historical accounts
(not today's chart), the date provenance (never ``Fecha``/``retrieved_at``/today), the blocking
flag with ``PROPOSED`` and never ``POSTED``, the skip path, the two refusals (no effective date;
two ``POSTED`` records), and the fact that every other fiscal state is untouched by 4.15.
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from sat_descarga_masiva.application.ports.accounting import AccountMapping
from sat_descarga_masiva.contabilidad.classification import AccountingCategory
from sat_descarga_masiva.contabilidad.journal import (
    JournalEntryRecord,
    JournalLineRecord,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.cancellation import (
    CANCELLATION_SKIP_RULE_IDS,
    RULE_4_15A,
    RULE_4_15B,
    RULE_VERSION,
    cancellation,
)
from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    Proposal,
    ReviewRequest,
    Skip,
)
from sat_descarga_masiva.contabilidad.rules.posting import SUPPORTED_RULES, propose
from sat_descarga_masiva.contabilidad.validator import PostingDecision, PostingEligibilityValidator
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalDocumentStatus,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawImpuestos,
    RawTraslado,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document

CONTRIBUTOR = Rfc("AAA010101AAA")
EMISOR = Rfc("BBB010101BBB")
UUID = Uuid("333E4567-E89B-12D3-A456-426614174000")
SOURCE_HASH = "c" * 64

#: The invoice's own date (`Fecha`) — what a reversal must *not* be dated by (§8a:209).
INVOICE_DATE = date(2024, 5, 15)
#: The cancellation's effective date — the one a reversal *is* dated by (§8a:203).
CANCELLED_ON = date(2026, 2, 3)
#: When the original posting became durable (§8:166), and when it was read (§6). Neither dates a
#: reversal.
POSTED_AT = datetime(2024, 5, 15, 12, 0, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 2, 4, 9, 30, tzinfo=UTC)

#: The chart the *reversal* would be mapped against today: deliberately different from the chart
#: the original was posted to, so "historical accounts" is a claim a test can falsify.
MAPPING_VERSION = "2026.01"
_ACCOUNTS = {
    AccountRole.GASTO: "601-999",
    AccountRole.IVA_ACRED_PAGADO: "118-999",
    AccountRole.CLEARING: "102-999",
}
_CLASSIFICATION = {"84111506": AccountingCategory.GASTO}


def _money(text: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(text))


class _Chart:
    """A `MappingProvider` that answers one fixed chart (§8a:207), and records who asked."""

    def __init__(self) -> None:
        self.mapping = AccountMapping(MAPPING_VERSION, _ACCOUNTS, _CLASSIFICATION)
        self.asked: list[Rfc] = []


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN PUE purchase as *source* facts, so a test can corrupt one."""
    base: dict[str, object] = {
        "tipo": "I",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": EMISOR.value,
        "receptor_rfc": CONTRIBUTOR.value,
        "conceptos": (RawConcepto("84111506", "1", "100.00", "100.00", None),),
        "impuestos": RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),), total_traslados="16.00"
        ),
        "total": "116.00",
        "uuid": UUID.value,
        "subtotal": "100.00",
        "fecha": "2024-05-15T10:30:00",
        "metodo_pago": "PUE",
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(
    *, status: FiscalDocumentStatus = FiscalDocumentStatus.CANCELLED, **overrides: object
) -> FiscalDocument:
    """The source as the metadata join would hand it over: cancelled by default (§6:123)."""
    parsed = build_fiscal_document(_raw(**overrides), SOURCE_HASH)
    assert parsed.document is not None, (parsed.outcome, overrides)
    return replace(parsed.document, status=status)


def _context(
    document: FiscalDocument | None = None,
    *,
    previous: tuple[JournalEntryRecord, ...] = (),
    effective_on: date | None = CANCELLED_ON,
    classification: AccountRole | None = None,
) -> PostingContext:
    """The context §8:194 reads: the source, its history and the cancellation's date."""
    resolved = document if document is not None else _document()
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=resolved,
        perspective=Perspective.RECIBIDO,
        classification=classification,
        previous_entries=previous,
        cancellation_effective_on=effective_on,
    )


def _leg(  # noqa: PLR0917 (a leg's six facts, in the order they are read)
    role: AccountRole,
    key: str,
    ordinal: int,
    side: LineSide,
    amount: str,
    account: str | None,
) -> JournalLineRecord:
    return JournalLineRecord(
        account_role=role,
        line_key=key,
        ordinal=ordinal,
        side=side,
        amount=_money(amount),
        resolved_account=account,
    )


def _posted_record(
    *,
    entry_key: str = "original-entry-key",
    posting_state: PostingState = PostingState.POSTED,
    lines: tuple[JournalLineRecord, ...] | None = None,
    rule_id: str = "4.3",
) -> JournalEntryRecord:
    """The accounting fact the client's books already hold for this source (§8:194).

    A received PUE purchase under §8:181: `DR Gasto=base · DR IVA Acred. Pagado=IVA · CR Total`,
    posted against a *previous* mapping version so the historical accounts are distinguishable.
    A ``SKIPPED`` record carries no legs by construction (§8:161), and is used by the tests that
    prove a skip is not a reversal candidate.
    """
    if lines is None and posting_state is PostingState.SKIPPED:
        lines = ()
    return JournalEntryRecord(
        entry_key=entry_key,
        contributor_rfc=CONTRIBUTOR,
        source_uuid=UUID,
        source_hash=SOURCE_HASH,
        rule_id=rule_id,
        rule_version="1",
        mapping_version="2025.01",
        policy_version="1",
        posting_state=posting_state,
        entry_date=INVOICE_DATE,
        recorded_at=POSTED_AT,
        lines=(
            lines
            if lines is not None
            else (
                _leg(AccountRole.GASTO, "base", 0, LineSide.DEBE, "100.00", "601-001"),
                _leg(AccountRole.IVA_ACRED_PAGADO, "iva", 1, LineSide.DEBE, "16.00", "118-001"),
                _leg(AccountRole.CLEARING, "total", 2, LineSide.HABER, "116.00", "102-099"),
            )
        ),
    )


def _entry(proposal: Proposal) -> ProposedJournalEntry:
    """The entry a proposal names, whichever of the three answers it is."""
    if isinstance(proposal, ProposedJournalEntry):
        return proposal
    assert proposal.entry is not None, proposal
    return proposal.entry


def _refusal(proposal: Proposal) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


def _legs(entry: ProposedJournalEntry) -> list[tuple[AccountRole, LineSide, str]]:
    return [(line.account_role, line.side, format(line.amount.amount, "f")) for line in entry.lines]


def _flags(proposal: Proposal) -> list[ReviewFlagType]:
    if isinstance(proposal, ProposedJournalEntry):
        return []
    return [flag.flag_type for flag in proposal.flags]


def _decide(context: PostingContext, chart: _Chart | None = None) -> PostingDecision:
    """Propose and decide through the engine's real door and validator (§8:159/173)."""
    resolved = chart if chart is not None else _Chart()
    validator = PostingEligibilityValidator(resolved, supported_rules=SUPPORTED_RULES)
    return validator.decide(context, propose(context), mapping=resolved.mapping)

    def mapping_for(self, client_rfc: Rfc) -> AccountMapping:
        self.asked.append(client_rfc)
        return self.mapping


# --- 4.15a: cancelled before it was ever posted (§8:161/194) ---------------------------


def test_4_15a_a_cancelled_source_with_no_history_is_skipped() -> None:
    """§8:194: nothing was posted, so there is no accounting fact to reverse."""
    proposal = cancellation(_context(previous=()))

    assert isinstance(proposal, Skip), proposal
    assert proposal.entry.rule_id == RULE_4_15A
    assert proposal.entry.rule_version == RULE_VERSION
    assert proposal.entry.lines == ()
    assert proposal.entry.source_uuid == UUID
    assert proposal.entry.contributor_rfc == CONTRIBUTOR


def test_4_15a_is_dated_by_the_cancellation_or_by_the_document_but_never_by_today() -> None:
    """§8a:203/209: a cancellation with a date dates the skip; without one, the invoice does."""
    dated = cancellation(_context(previous=(), effective_on=CANCELLED_ON))
    assert _entry(dated).entry_date == CANCELLED_ON

    undated = cancellation(_context(previous=(), effective_on=None))
    assert _entry(undated).entry_date == INVOICE_DATE


def test_4_15a_is_the_only_cancellation_skip_and_the_validator_honours_it() -> None:
    """§8:161: `SKIPPED` is a closed set; 4.15a is the one id this module adds to it."""
    assert frozenset({RULE_4_15A}) == CANCELLATION_SKIP_RULE_IDS

    decision = _decide(_context(previous=()))
    assert decision.posting_state is PostingState.SKIPPED
    assert decision.review_flags == ()
    assert decision.accounts == {}
    assert decision.entry is not None
    assert decision.entry.rule_id == RULE_4_15A


@pytest.mark.parametrize(
    "history",
    [
        (PostingState.PROPOSED,),
        (PostingState.SKIPPED,),
        (PostingState.PROPOSED, PostingState.SKIPPED),
    ],
)
def test_4_15a_only_posted_history_is_a_reversal_candidate(
    history: tuple[PostingState, ...],
) -> None:
    """§8:194: a refusal and a skip moved nothing, so neither becomes a compensated fact."""
    previous = tuple(
        _posted_record(entry_key=f"history-{index}", posting_state=state)
        for index, state in enumerate(history)
    )
    proposal = cancellation(_context(previous=previous))
    assert isinstance(proposal, Skip), proposal
    assert proposal.entry.rule_id == RULE_4_15A


def test_a_mixed_history_still_finds_the_one_posted_fact() -> None:
    """§8:194: the `POSTED` record is the fact; the refusals beside it are not alternatives."""
    posted = _posted_record()
    proposed = replace(_posted_record(entry_key="refusal"), posting_state=PostingState.PROPOSED)

    proposal = cancellation(_context(previous=(proposed, posted)))
    assert isinstance(proposal, ReviewRequest)
    assert _entry(proposal).rule_id == RULE_4_15B


# --- 4.15b: the exact compensating reversal (§8:194) ----------------------------------


def test_4_15b_inverts_every_posted_leg_exactly() -> None:
    """§8:194: same role, same amount, opposite side — the mirror, leg for leg, in order."""
    entry = _entry(_refusal(cancellation(_context(previous=(_posted_record(),)))))

    assert entry.rule_id == RULE_4_15B
    assert entry.rule_version == RULE_VERSION
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.HABER, "100.00"),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.HABER, "16.00"),
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
    ]


def test_4_15b_reverses_an_emitted_sale_as_readily_as_a_purchase() -> None:
    """§8:194: the reversal is computed from the record, so the original's shape is irrelevant."""
    sale = _posted_record(
        rule_id="4.1",
        lines=(
            _leg(AccountRole.CLEARING, "clearing", 0, LineSide.DEBE, "116.00", "102-099"),
            _leg(AccountRole.INGRESOS, "base", 1, LineSide.HABER, "100.00", "401-001"),
            _leg(AccountRole.IVA_TRASLADADO_COBRADO, "iva", 2, LineSide.HABER, "16.00", "208-001"),
        ),
    )
    entry = _entry(_refusal(cancellation(_context(previous=(sale,)))))
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.HABER, "116.00"),
        (AccountRole.INGRESOS, LineSide.DEBE, "100.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.DEBE, "16.00"),
    ]


def test_4_15b_balances_exactly_as_the_original_did() -> None:
    """§8:158: inverting both sides preserves the balance, so no IVA or base is recalculated."""
    entry = _entry(_refusal(cancellation(_context(previous=(_posted_record(),)))))
    assert entry.debe_total() == Decimal("116.00")
    assert entry.haber_total() == Decimal("116.00")
    assert entry.is_balanced()


def test_4_15b_keys_every_line_by_the_historical_leg_it_reverses() -> None:
    """§8:194: the inversion's identity is derived from the leg's own key, never invented."""
    entry = _entry(_refusal(cancellation(_context(previous=(_posted_record(),)))))
    assert [line.line_key for line in entry.lines] == [
        "reversal:base",
        "reversal:iva",
        "reversal:total",
    ]


def test_4_15b_is_dated_by_the_cancellation_and_by_nothing_else() -> None:
    """§8a:203/209: not `Fecha`, not `recorded_at`, not `retrieved_at`, and not today."""
    entry = _entry(_refusal(cancellation(_context(previous=(_posted_record(),)))))
    assert entry.entry_date == CANCELLED_ON
    assert entry.entry_date != INVOICE_DATE
    assert entry.entry_date != POSTED_AT.date()
    assert entry.entry_date != RETRIEVED_AT.date()


def test_4_15b_books_the_accounts_the_original_was_posted_to() -> None:
    """§8a:207/§8:194: the reversal settles against history, not against today's chart."""
    context = _context(previous=(_posted_record(),))
    decision = _decide(context)

    assert decision.posting_state is PostingState.PROPOSED
    assert decision.account_for(AccountRole.GASTO) == "601-001"
    assert decision.account_for(AccountRole.IVA_ACRED_PAGADO) == "118-001"
    assert decision.account_for(AccountRole.CLEARING) == "102-099"
    # The chart this run resolved maps the same roles elsewhere; history wins (§8:194).
    assert _ACCOUNTS[AccountRole.GASTO] != decision.account_for(AccountRole.GASTO)


def test_4_15b_records_the_historical_account_on_every_persisted_leg() -> None:
    """§8:166: the record keeps what was posted, even though today's mapping version is used."""
    decision = _decide(_context(previous=(_posted_record(),)))
    record = decision.to_record(recorded_at=RETRIEVED_AT)

    assert [line.resolved_account for line in record.lines] == ["601-001", "118-001", "102-099"]
    assert record.mapping_version == MAPPING_VERSION
    assert record.rule_id == RULE_4_15B
    assert record.posting_state is PostingState.PROPOSED


def test_4_15b_carries_the_blocking_cancellation_flag_and_never_posts() -> None:
    """§8:189/194: the reversal is a human decision before it is an accounting fact."""
    proposal = _refusal(cancellation(_context(previous=(_posted_record(),))))
    assert _flags(proposal) == [ReviewFlagType.CANCELLED_AFTER_POSTING]

    decision = _decide(_context(previous=(_posted_record(),)))
    assert decision.posting_state is PostingState.PROPOSED
    assert [flag.flag_type for flag in decision.review_flags] == [
        ReviewFlagType.CANCELLED_AFTER_POSTING
    ]


def test_4_15b_never_claims_an_unspecified_partial_payment_treatment() -> None:
    """§8a: the partial-payment question is unanswered, so it may not be asserted here."""
    proposal = _refusal(cancellation(_context(previous=(_posted_record(),))))
    assert ReviewFlagType.PARTIAL_PAYMENT_REVERSAL_UNSPECIFIED not in _flags(proposal)
    assert ReviewFlagType.INELIGIBLE_SOURCE_STATE not in _flags(proposal)


def test_4_15b_without_an_effective_date_refuses_on_the_document() -> None:
    """§8a:203 + §8:167: no date, no entry — an undated reversal is never invented."""
    proposal = _refusal(cancellation(_context(previous=(_posted_record(),), effective_on=None)))

    assert proposal.entry is None
    assert _flags(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    assert "FechaCancelacion" in proposal.flags[0].reason

    decision = _decide(_context(previous=(_posted_record(),), effective_on=None))
    assert decision.entry is None
    assert decision.posting_state is PostingState.PROPOSED


def test_two_posted_records_are_refused_rather_than_guessed() -> None:
    """§8:194: one source has one accounting fact; two of them need a human, not a first pick."""
    first = _posted_record(entry_key="first-posted")
    second = _posted_record(entry_key="second-posted")

    with pytest.raises(ValueError, match="first-posted, second-posted"):
        cancellation(_context(previous=(first, second)))


def test_a_posted_record_with_an_unresolved_leg_cannot_even_be_built() -> None:
    """§8:159: the aggregate refuses it, so 4.15 never sees a POSTED leg with no account."""
    with pytest.raises(ValueError, match="unresolved"):
        _posted_record(
            lines=(
                _leg(AccountRole.GASTO, "base", 0, LineSide.DEBE, "100.00", None),
                _leg(AccountRole.CLEARING, "total", 1, LineSide.HABER, "100.00", "102-099"),
            )
        )


def test_a_role_posted_to_two_accounts_is_refused() -> None:
    """§8a:207: one role resolves to one account; two mean the record contradicts itself."""
    contradictory = _posted_record(
        lines=(
            _leg(AccountRole.GASTO, "base", 0, LineSide.DEBE, "50.00", "601-001"),
            _leg(AccountRole.GASTO, "extra", 1, LineSide.DEBE, "50.00", "601-002"),
            _leg(AccountRole.CLEARING, "total", 2, LineSide.HABER, "100.00", "102-099"),
        )
    )
    with pytest.raises(ValueError, match="two accounts"):
        cancellation(_context(previous=(contradictory,)))


# --- identity, immutability, and the states 4.15 must not touch ------------------------


def test_the_reversal_has_its_own_stable_identity() -> None:
    """§8:194: a retry derives the same key, and it is neither the original's nor 4.15a's."""
    context = _context(previous=(_posted_record(),))
    first = _entry(_refusal(cancellation(context)))
    second = _entry(_refusal(cancellation(context)))

    assert first.fingerprint(MAPPING_VERSION) == second.fingerprint(MAPPING_VERSION)
    assert first.fingerprint(MAPPING_VERSION).canonical() != _posted_record().entry_key
    assert first.fingerprint(MAPPING_VERSION).rule_id == RULE_4_15B


def test_the_reversal_records_idempotently() -> None:
    """§8:166: two identical decisions produce one identical record, so an append is a no-op."""
    context = _context(previous=(_posted_record(),))
    first = _decide(context).to_record(recorded_at=RETRIEVED_AT)
    second = _decide(context).to_record(recorded_at=RETRIEVED_AT)

    assert first == second
    assert first.entry_key == second.entry_key


def test_the_original_posting_is_never_mutated() -> None:
    """§8:166/194: the reversal is a *new* entry; the posted record stays exactly as it was."""
    original = _posted_record()
    before = replace(original, lines=tuple(original.lines))

    cancellation(_context(previous=(original,)))

    assert original == before
    assert original.posting_state is PostingState.POSTED
    assert original.entry_date == INVOICE_DATE


def test_a_vigente_source_is_not_a_cancellation_case() -> None:
    """§8:194: 4.15 answers for a cancelled source, and only for one."""
    context = _context(
        document=_document(status=FiscalDocumentStatus.VIGENTE),
        previous=(_posted_record(),),
    )
    assert cancellation(context) is None
    assert _entry(propose(context)).rule_id == "4.3"


def test_an_unknown_source_is_not_a_cancellation_case() -> None:
    """§8:159/194: an unresolved status is the validator's refusal, never a 4.15 decision."""
    context = _context(
        document=_document(status=FiscalDocumentStatus.UNKNOWN),
        previous=(_posted_record(),),
        effective_on=None,
        classification=AccountRole.GASTO,
    )
    assert cancellation(context) is None

    decision = _decide(context)
    assert decision.posting_state is PostingState.PROPOSED
    assert ReviewFlagType.UNKNOWN_SOURCE_STATE in {flag.flag_type for flag in decision.review_flags}
    assert ReviewFlagType.CANCELLED_AFTER_POSTING not in {
        flag.flag_type for flag in decision.review_flags
    }


def test_the_cancellation_rows_are_registered_under_their_own_ids() -> None:
    """§8:159: the validator refuses a rule id the engine does not know, so both are registered."""
    assert SUPPORTED_RULES[RULE_4_15A] == RULE_VERSION
    assert SUPPORTED_RULES[RULE_4_15B] == RULE_VERSION
