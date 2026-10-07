"""M3 step 4b: the `PostingEligibilityValidator` — the only word that may say `POSTED` (§8:159).

**A rule proposes; the validator decides.** These tests walk §8:159's ten conjunctive
conditions one at a time — each failure is `NEEDS_REVIEW` with *that* reason — and pin the
three things the engine's safety rests on:

- the default is refusal: `POSTED` requires every condition to hold, so a fiscal state nobody
  has resolved (`UNKNOWN` — the fiscal layer's only value until the metadata join exists) is
  *no evidence of eligibility*: nothing uncertain is posted, so it waits in the review queue;
- the rules' own answers are honoured but never rewritten: a `Skip` becomes `SKIPPED` only for
  a §8:161 rule id, a `ReviewRequest` stays `PROPOSED` **even when its entry balances**, and
  nothing else can reach `SKIPPED`;
- the verdict is evidence-complete: the mapping version, the policy version and the account of
  every posted leg travel with it, so a posting is explainable and a refusal is actionable.

The mapping is injected as a port, so the tests decide what resolves — and assert that an
unresolved role is never quietly defaulted (§8a:207).
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from sat_descarga_masiva.application.ports.accounting import AccountMapping
from sat_descarga_masiva.contabilidad.journal import (
    JournalLine,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest, Skip
from sat_descarga_masiva.contabilidad.validator import (
    POLICY_VERSION,
    PostingDecision,
    PostingEligibilityValidator,
)
from sat_descarga_masiva.domain.errors import MappingNotConfigured
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalDocumentStatus,
    Impuestos,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlags, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount

CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
UUID = Uuid("4e80345d-917f-40bb-a98f-4a73939353c5")
SOURCE_HASH = "a" * 64
FECHA = date(2026, 1, 15)
#: When a decision is written down: §8:166's record carries the moment it became durable.
WHEN = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)
MAPPING_VERSION = "2026.01"
RULE_ID = "4.1"
RULE_VERSION = "1"
SUPPORTED_RULES = {RULE_ID: RULE_VERSION}

_ACCOUNTS = {
    AccountRole.CLEARING: "102-099",
    AccountRole.INGRESOS: "401-001",
    AccountRole.IVA_TRASLADADO_COBRADO: "208-001",
}


class _Provider:
    """A `MappingProvider` that records who asked — the port the engine really consumes."""

    def __init__(self, mapping: AccountMapping | None = None) -> None:
        self.mapping = (
            mapping if mapping is not None else AccountMapping(MAPPING_VERSION, _ACCOUNTS)
        )
        self.asked: list[Rfc] = []

    def mapping_for(self, client_rfc: Rfc) -> AccountMapping:
        self.asked.append(client_rfc)
        if self.mapping is None:
            raise MappingNotConfigured(f"no account mapping for {client_rfc.value}")
        return self.mapping

    def refuses(self) -> None:
        self.mapping = None


def _validator(
    provider: _Provider | None = None, **overrides: object
) -> PostingEligibilityValidator:
    settings: dict[str, object] = {"supported_rules": SUPPORTED_RULES}
    settings.update(overrides)
    return PostingEligibilityValidator(provider or _Provider(), **settings)  # type: ignore[arg-type]


def _document(
    *,
    tipo: str = "I",
    moneda: str = "MXN",
    #: §8:159: only a *resolved* VIGENTE posts, so the happy path declares one explicitly.
    status: FiscalDocumentStatus = FiscalDocumentStatus.VIGENTE,
    review_flags: ReviewFlags | None = None,
    fecha: date | None = FECHA,
    source_uuid: Uuid | None = UUID,
    source_hash: str = SOURCE_HASH,
) -> FiscalDocument:
    return FiscalDocument(
        tipo=tipo,
        version="4.0",
        moneda=moneda,
        tipo_cambio=None,
        emisor_rfc=CONTRIBUTOR,
        receptor_rfc=RECEPTOR,
        conceptos=(),
        impuestos=Impuestos(),
        total=NormalizedAmount(Decimal("116.00")),
        source_hash=source_hash,
        status=status,
        review_flags=review_flags if review_flags is not None else ReviewFlags(),
        source_uuid=source_uuid,
        fecha=fecha,
    )


def _context(document: FiscalDocument | None = None) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document if document is not None else _document(),
        perspective=Perspective.EMITIDO,
    )


def _leg(
    role: AccountRole = AccountRole.CLEARING,
    key: str = "total",
    side: LineSide = LineSide.DEBE,
    amount: str = "116.00",
) -> JournalLine:
    return JournalLine(
        account_role=role, line_key=key, side=side, amount=NormalizedAmount(Decimal(amount))
    )


def _proposal(
    *,
    lines: tuple[JournalLine, ...] | None = None,
    rule_id: str = RULE_ID,
    rule_version: str = RULE_VERSION,
    source_uuid: Uuid | None = UUID,
    source_hash: str = SOURCE_HASH,
) -> ProposedJournalEntry:
    """The balanced 4.1-shaped proposal the happy path posts (a hand-built entry, no rule)."""
    default_lines = (
        _leg(AccountRole.CLEARING, "total", LineSide.DEBE, "116.00"),
        _leg(AccountRole.INGRESOS, "base", LineSide.HABER, "100.00"),
        _leg(AccountRole.IVA_TRASLADADO_COBRADO, "iva", LineSide.HABER, "16.00"),
    )
    return ProposedJournalEntry(
        rule_id=rule_id,
        rule_version=rule_version,
        contributor_rfc=CONTRIBUTOR,
        source_uuid=source_uuid,
        source_hash=source_hash,
        entry_date=FECHA,
        lines=default_lines if lines is None else lines,
    )


def _flag_types(decision: PostingDecision) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in decision.review_flags]


# --- the happy path ------------------------------------------------------------------


def test_a_fully_evidenced_entry_is_posted() -> None:
    """§8:159: every condition holds, so — and only then — the entry is an accounting fact."""
    decision = _validator().decide(_context(), _proposal())
    assert decision.posting_state is PostingState.POSTED
    assert decision.review_flags == ()
    assert decision.mapping_version == MAPPING_VERSION
    assert decision.policy_version == POLICY_VERSION
    assert decision.account_for(AccountRole.CLEARING) == "102-099"
    assert decision.account_for(AccountRole.INGRESOS) == "401-001"
    assert decision.account_for(AccountRole.IVA_TRASLADADO_COBRADO) == "208-001"


def test_a_posted_entry_is_keyed_by_the_decisions_own_mapping_version() -> None:
    """§8:194: the mapping version is part of the identity, and the decision carries it."""
    decision = _validator().decide(_context(), _proposal())
    assert decision.entry is not None
    assert decision.entry.fingerprint(decision.mapping_version).mapping_version == MAPPING_VERSION
    assert len(decision.entry.line_fingerprints(decision.mapping_version)) == 3


def test_the_contributors_mapping_is_what_decides_the_accounts() -> None:
    """§8:194/§8a:207: the books belong to the contributor, so the chart is theirs."""
    provider = _Provider()
    _validator(provider).decide(_context(), _proposal())
    assert provider.asked == [CONTRIBUTOR]


def test_a_client_without_a_mapping_is_a_configuration_failure() -> None:
    """Not a per-document review case: it would fail for every document the client owns."""
    provider = _Provider()
    provider.refuses()
    with pytest.raises(MappingNotConfigured):
        _validator(provider).decide(_context(), _proposal())


def test_the_engine_records_the_policy_version_it_used() -> None:
    """§8:159: `policy_version` is recorded with the posting, so it is not a hidden constant."""
    decision = _validator(policy_version="7").decide(_context(), _proposal())
    assert decision.policy_version == "7"


@pytest.mark.parametrize("policy_version", ["", "  "])
def test_a_blank_policy_version_cannot_be_configured(policy_version: str) -> None:
    with pytest.raises(ValueError, match="policy version"):
        _validator(policy_version=policy_version)


# --- condition 1: the source state at posting time ------------------------------------


def test_a_vigente_source_may_post() -> None:
    """§8:159: "source state eligible (vigente) at posting time" — plus every other condition."""
    document = replace(_document(), status=FiscalDocumentStatus.VIGENTE)
    decision = _validator().decide(_context(document), _proposal())
    assert decision.posting_state is PostingState.POSTED
    assert decision.review_flags == ()


@pytest.mark.parametrize(
    "status,reason",
    [
        (FiscalDocumentStatus.CANCELLED, ReviewFlagType.INELIGIBLE_SOURCE_STATE),
        (FiscalDocumentStatus.UNKNOWN, ReviewFlagType.UNKNOWN_SOURCE_STATE),
    ],
)
def test_a_source_that_is_not_known_vigente_is_never_posted(
    status: FiscalDocumentStatus, reason: ReviewFlagType
) -> None:
    """§8:159 needs an *eligible* source: a known cancellation and a status nobody resolved
    both fail the gate — and each says so with its own reason (§8:167)."""
    document = replace(_document(), status=status)
    decision = _validator().decide(_context(document), _proposal())
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [reason]


def test_an_unknown_source_state_can_never_produce_posted() -> None:
    """The golden rule: an unresolved status is not evidence of eligibility — never a posting."""
    document = replace(_document(), status=FiscalDocumentStatus.UNKNOWN)
    decision = _validator().decide(_context(document), _proposal())
    assert decision.posting_state is not PostingState.POSTED
    assert ReviewFlagType.UNKNOWN_SOURCE_STATE in _flag_types(decision)


# --- condition 2: the source fields a rule needs --------------------------------------


def test_a_proposal_without_legs_is_not_a_posting() -> None:
    """§8:173: a posting rule that computed nothing did not have its source fields."""
    decision = _validator().decide(_context(), _proposal(lines=()))
    assert _flag_types(decision) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    assert decision.posting_state is PostingState.PROPOSED


# --- condition 3: deterministic account mapping ---------------------------------------


def test_an_unmapped_role_is_flagged_and_never_defaulted() -> None:
    """§8a:207: a role the mapping does not name is a review case, not a plausible account."""
    accounts = {AccountRole.CLEARING: "102-099", AccountRole.IVA_TRASLADADO_COBRADO: "208-001"}
    provider = _Provider(AccountMapping(MAPPING_VERSION, accounts))
    decision = _validator(provider).decide(_context(), _proposal())
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.UNMAPPED_ACCOUNT]
    assert decision.accounts == {}  # nothing was resolved for a document that did not post


# --- condition 4: deterministic FX valuation -----------------------------------------


def test_a_foreign_currency_document_awaits_a_resolved_rate() -> None:
    """§8:193: without the rate chain, a conversion would be the engine's own invention."""
    decision = _validator().decide(_context(replace(_document(), moneda="USD")), _proposal())
    assert _flag_types(decision) == [ReviewFlagType.AMBIGUOUS_FX]


def test_a_rep_is_exempt_from_the_header_currency_condition() -> None:
    """§8:192/§8:193: a `P` comprobante's header states no currency the entry is valued in.

    A REP states its currency per payment (`MonedaP`/`MonedaDR`), so refusing the whole document
    for its header would be a reason about a value nothing in the entry came from. Whether the
    payments themselves can be valued is the *rule's* question — it flags
    `FX_DIFFERENCE_UNCONFIRMED` and books no leg for an amount nobody valued — so the validator
    must not pre-empt it here.
    """
    decision = _validator().decide(
        _context(replace(_document(tipo="P"), moneda="USD", tipo_cambio=Decimal("17.5"))),
        _proposal(),
    )
    assert _flag_types(decision) == []
    assert decision.posting_state is PostingState.POSTED


def test_the_header_currency_condition_still_refuses_every_other_type() -> None:
    """The regression that keeps the exemption narrow: only `PAGO` skips condition 4 (§8:193)."""
    for tipo in ("I", "E", "T", "N", "R", "X"):
        decision = _validator().decide(
            _context(replace(_document(tipo=tipo), moneda="USD")), _proposal()
        )
        assert ReviewFlagType.AMBIGUOUS_FX in _flag_types(decision), tipo


# --- condition 5: valid Decimal amounts -----------------------------------------------


def test_a_corrupt_amount_is_refused_even_when_it_reaches_the_validator() -> None:
    """§8:159/§12: the leg's constructor is the first guard; this is the second."""
    leg = _leg()
    object.__setattr__(leg, "amount", NormalizedAmount(Decimal("NaN")))
    decision = _validator().decide(_context(), _proposal(lines=(leg,)))
    assert ReviewFlagType.INVALID_AMOUNT in _flag_types(decision)
    assert decision.posting_state is PostingState.PROPOSED


# --- condition 6: balanced Debe == Haber ----------------------------------------------


def test_an_unbalanced_entry_is_flagged_not_posted() -> None:
    """§8:158: the first of the two balance checks; the pre-commit one aborts."""
    lines = (
        _leg(AccountRole.CLEARING, "total", LineSide.DEBE, "999.00"),
        _leg(AccountRole.INGRESOS, "base", LineSide.HABER, "100.00"),
    )
    decision = _validator().decide(_context(), _proposal(lines=lines))
    assert _flag_types(decision) == [ReviewFlagType.UNBALANCED_ENTRY]


# --- condition 7: unresolved review flags ---------------------------------------------


def test_a_documents_own_review_flag_outranks_the_posting() -> None:
    """§8:161: an open M2 flag (signature, currency, perspective) is review state, not noise."""
    flags = ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "no sello"))
    decision = _validator().decide(_context(replace(_document(), review_flags=flags)), _proposal())
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.CFDI_SIGNATURE]


# --- conditions 8 and 10: a supported rule, at a version the engine stands behind ------


@pytest.mark.parametrize(
    "rule_id,rule_version",
    [("9.9", RULE_VERSION), ("", RULE_VERSION), (RULE_ID, "0"), (RULE_ID, "")],
)
def test_only_a_current_supported_rule_may_post(rule_id: str, rule_version: str) -> None:
    """§8:159/§8:194: the version is part of what the posting is keyed by — a stale one is out."""
    entry = _proposal(rule_id=rule_id, rule_version=rule_version)
    decision = _validator().decide(_context(), entry)
    assert _flag_types(decision) == [ReviewFlagType.UNSUPPORTED_RULE]


# --- condition 9: the posting identity -------------------------------------------------


@pytest.mark.parametrize("source_hash", ["", "   "])
def test_a_blank_source_hash_has_no_posting_identity(source_hash: str) -> None:
    decision = _validator().decide(_context(), _proposal(source_hash=source_hash))
    assert _flag_types(decision) == [ReviewFlagType.MISSING_POSTING_IDENTITY]


def test_an_entry_without_a_source_uuid_has_no_posting_identity() -> None:
    """§8:194: without it there is no key to record the fact under."""
    decision = _validator().decide(_context(), _proposal(source_uuid=None))
    assert _flag_types(decision) == [ReviewFlagType.MISSING_POSTING_IDENTITY]


# --- the rules' own answers -------------------------------------------------------------


def test_a_skip_is_honoured_with_its_own_entry() -> None:
    """§8:161/169: the skip is recorded — dated, identified and versioned, with no legs."""
    skip = Skip(entry=_proposal(lines=(), rule_id="4.12"), detail="tipo T carries no operation")
    decision = _validator().decide(_context(), skip)
    assert decision.posting_state is PostingState.SKIPPED
    assert decision.entry is skip.entry
    assert decision.review_flags == ()
    assert decision.accounts == {}
    assert decision.mapping_version == MAPPING_VERSION


def test_a_fabricated_skip_is_refused_rather_than_recorded() -> None:
    """`SKIPPED` asserts no human decision is needed: an unknown rule cannot claim that."""
    skip = Skip(entry=_proposal(lines=(), rule_id="9.9"), detail="not a §8:161 case")
    decision = _validator().decide(_context(), skip)
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_a_review_request_stays_proposed_even_when_its_entry_balances() -> None:
    """§8a:212: a rule may propose the commercial correction and still refuse to post it."""
    review = ReviewRequest(
        flags=(ReviewFlag(ReviewFlagType.MISSING_REP_ORIGINAL, "no REP in the ledger"),),
        detail="the cash movement cannot be tied to its original",
        entry=_proposal(),
    )
    decision = _validator().decide(_context(), review)
    assert decision.posting_state is PostingState.PROPOSED
    assert decision.review_flags == review.flags
    assert decision.entry is review.entry
    assert decision.accounts == {}


def test_a_review_request_without_an_entry_flags_the_document() -> None:
    """§8:167: an undatable/unidentified document is flagged on itself, not on a fake entry."""
    review = ReviewRequest(
        flags=(ReviewFlag(ReviewFlagType.MISSING_POSTING_IDENTITY, "no TFD UUID"),),
        detail="the document carries no identity to key an entry by",
    )
    decision = _validator().decide(_context(), review)
    assert decision.entry is None
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.MISSING_POSTING_IDENTITY]


# --- several failures at once -----------------------------------------------------------


@pytest.mark.parametrize(
    "source_status,source_reason",
    [
        (FiscalDocumentStatus.CANCELLED, ReviewFlagType.INELIGIBLE_SOURCE_STATE),
        (FiscalDocumentStatus.UNKNOWN, ReviewFlagType.UNKNOWN_SOURCE_STATE),
    ],
)
def test_every_failing_condition_is_reported_in_8_159_order(
    source_status: FiscalDocumentStatus, source_reason: ReviewFlagType
) -> None:
    """A human clearing the entry wants all the reasons, in a deterministic order — and the
    source-state failure does not stop the collection at the first condition (§8:159)."""
    document = _document(
        moneda="USD",
        status=source_status,
        review_flags=ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "no sello")),
    )
    lines = (
        _leg(AccountRole.CLIENTES, "total", LineSide.DEBE, "999.00"),
        _leg(AccountRole.INGRESOS, "base", LineSide.HABER, "100.00"),
    )
    entry = _proposal(lines=lines, rule_version="0", source_uuid=None)
    decision = _validator().decide(_context(document), entry)
    assert _flag_types(decision) == [
        source_reason,
        ReviewFlagType.UNMAPPED_ACCOUNT,
        ReviewFlagType.AMBIGUOUS_FX,
        ReviewFlagType.UNBALANCED_ENTRY,
        ReviewFlagType.CFDI_SIGNATURE,
        ReviewFlagType.UNSUPPORTED_RULE,
        ReviewFlagType.MISSING_POSTING_IDENTITY,
    ]
    assert decision.posting_state is PostingState.PROPOSED


# --- the decision cannot contradict itself ---------------------------------------------


def test_a_posted_decision_must_resolve_every_leg() -> None:
    with pytest.raises(ValueError, match="resolves every leg"):
        PostingDecision(
            entry=_proposal(),
            posting_state=PostingState.POSTED,
            mapping_version=MAPPING_VERSION,
            policy_version=POLICY_VERSION,
        )


def test_a_posted_decision_must_be_balanced() -> None:
    entry = _proposal(
        lines=(
            _leg(AccountRole.CLEARING, "total", LineSide.DEBE, "999.00"),
            _leg(AccountRole.INGRESOS, "base", LineSide.HABER, "100.00"),
        )
    )
    with pytest.raises(ValueError, match="unbalanced entry is never POSTED"):
        PostingDecision(
            entry=entry,
            posting_state=PostingState.POSTED,
            mapping_version=MAPPING_VERSION,
            policy_version=POLICY_VERSION,
            accounts={AccountRole.CLEARING: "102-099", AccountRole.INGRESOS: "401-001"},
        )


def test_a_proposed_decision_must_say_why() -> None:
    with pytest.raises(ValueError, match="always carries a reason"):
        PostingDecision(
            entry=_proposal(),
            posting_state=PostingState.PROPOSED,
            mapping_version=MAPPING_VERSION,
            policy_version=POLICY_VERSION,
        )


def test_a_skipped_decision_posts_nothing() -> None:
    with pytest.raises(ValueError, match="carries no legs"):
        PostingDecision(
            entry=_proposal(),
            posting_state=PostingState.SKIPPED,
            mapping_version=MAPPING_VERSION,
            policy_version=POLICY_VERSION,
        )


@pytest.mark.parametrize("field", ["mapping_version", "policy_version"])
def test_a_decision_records_the_versions_it_was_made_under(field: str) -> None:
    """§8:159: without them the posting could not be explained later."""
    with pytest.raises(ValueError, match="version"):
        PostingDecision(
            entry=_proposal(),
            posting_state=PostingState.SKIPPED,
            mapping_version=" " if field == "mapping_version" else MAPPING_VERSION,
            policy_version=" " if field == "policy_version" else POLICY_VERSION,
        )


# --- the decision written down: `to_record` (§8:166) -----------------------------------


def test_a_posted_decision_becomes_the_record_a_store_writes() -> None:
    """§8:194/§8:159/§8a:207: the key, the versions and every resolved account travel with it.

    The record is the decision *projected*, not re-derived: `entry_key` is the entry's own
    fingerprint under the decision's mapping version, and each leg carries the account this
    decision resolved — which is what makes the posting explainable without the mapping.
    """
    decision = _validator().decide(_context(), _proposal())
    record = decision.to_record(recorded_at=WHEN)

    assert decision.entry is not None
    assert record.entry_key == decision.entry.fingerprint(MAPPING_VERSION).canonical()
    assert (record.contributor_rfc, record.source_uuid) == (CONTRIBUTOR, UUID)
    assert (record.rule_id, record.rule_version) == (RULE_ID, RULE_VERSION)
    assert (record.mapping_version, record.policy_version) == (MAPPING_VERSION, POLICY_VERSION)
    assert record.posting_state is PostingState.POSTED
    assert record.entry_date == FECHA
    assert record.recorded_at == WHEN
    assert record.assumptions == ()
    assert [(line.line_key, line.ordinal) for line in record.lines] == [
        ("total", 0),
        ("base", 1),
        ("iva", 2),
    ]
    assert [line.resolved_account for line in record.lines] == ["102-099", "401-001", "208-001"]


def test_a_skipped_decision_becomes_a_zero_line_record() -> None:
    """§8:169: the skip is an auditable record — dated, identified, versioned, legless."""
    skip = Skip(entry=_proposal(lines=(), rule_id="4.12"), detail="tipo T carries no operation")
    record = _validator().decide(_context(), skip).to_record(recorded_at=WHEN)

    assert record.posting_state is PostingState.SKIPPED
    assert record.lines == ()
    assert record.entry_key == skip.entry.fingerprint(MAPPING_VERSION).canonical()


def test_a_proposed_refusal_records_the_leg_it_could_not_resolve() -> None:
    """§8:167/§8a:207: the unmapped role is recorded as ``None`` — the refusal is evidence.

    Nothing may be posted for a role the chart does not name, so the record keeps the leg
    with no account: a reader sees *which* leg a human still has to resolve.
    """
    provider = _Provider(AccountMapping(MAPPING_VERSION, {}))
    decision = _validator(provider).decide(_context(), _proposal())
    record = decision.to_record(recorded_at=WHEN)

    assert decision.posting_state is PostingState.PROPOSED
    assert [line.resolved_account for line in record.lines] == [None, None, None]


def test_a_decision_that_names_no_entry_cannot_become_a_record() -> None:
    """§8:167: an undatable/unidentified document has no §8:194 identity to append under.

    The review fact belongs to the document (an M2 review flag), so there is nothing to
    record here — and inventing a fingerprint for it is what §8:194 forbids.
    """
    review = ReviewRequest(
        flags=(ReviewFlag(ReviewFlagType.MISSING_POSTING_IDENTITY, "no TFD UUID"),),
        detail="the document carries no identity to key an entry by",
    )
    decision = _validator().decide(_context(), review)

    with pytest.raises(ValueError, match="nothing to record"):
        decision.to_record(recorded_at=WHEN)


def test_an_entry_without_a_source_uuid_cannot_become_a_record() -> None:
    """§8:194: no source UUID, no fingerprint — and therefore nothing to append under."""
    decision = _validator().decide(_context(), _proposal(source_uuid=None))
    assert decision.entry is not None  # the entry exists, it simply cannot be keyed

    with pytest.raises(ValueError, match="no fingerprint"):
        decision.to_record(recorded_at=WHEN)
