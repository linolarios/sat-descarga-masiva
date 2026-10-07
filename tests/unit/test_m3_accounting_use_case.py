"""M3 application layer: `ExecuteAccountingUseCase` — the accounting stage, one document at a time.

The engine already decides everything: §8's rules 4.1/4.2/4.3/4.4 propose, §8:161's
out-of-scope table skips (4.11/4.12/4.14), and the `PostingEligibilityValidator` is the only
word that may say ``POSTED`` (§8:159). What these tests pin is the *orchestration* the
application layer owes them, because that is where the engine's guarantees are easiest to lose:

- **the sequence.** One mapping load, one classification, one `propose`, one `decide`, one
  clock read, one `append`. A second load could version one posting two ways; a second clock
  read could date one record two ways.
- **the review state is not dropped.** §6's projection composes flags from three producers on
  the `ProcessedDocument` while a rule reads the document, so the two are merged before the
  context is built: a signature flag M2 raised must still stop §8:159's posting (A29's
  regression).
- **what is written, and what is not.** A decision that names an entry is appended — including
  a ``PROPOSED`` one, which is §8:167's audit evidence. A *document-level* refusal (no `Fecha`
  to date by, no TFD UUID to key by) names no entry, so nothing is appended and no clock is
  read: inventing §8:194's identity is the one thing the ledger must never do.
- **the second balance gate aborts.** §8:158 is checked twice, deliberately. The validator's
  check is a review case; the commit-time check raises `UnbalancedJournalCommit`, is
  ``POSTED``-only, ``Decimal``-only, and runs *before* `append` — a ``POSTED`` record that does
  not balance is a writer bug, never a downgrade and never a partial write.

Independent collaborators are faked at their ports (a recording `MappingProvider`, a fake
`Clock`, the shared in-memory journal store), so a failure names the seam it belongs to. The
real propose/validator path is exercised end-to-end in
``test_m3_accounting_contract.py`` against the versioned mapping YAML.
"""

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from sat_descarga_masiva.application.ports.accounting import AccountMapping
from sat_descarga_masiva.application.use_cases.execute_accounting import (
    AccountingResult,
    ExecuteAccountingUseCase,
    _assert_balanced_for_commit,
)
from sat_descarga_masiva.contabilidad.classification import AccountingCategory, Classification
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
from sat_descarga_masiva.contabilidad.rules.posting import SUPPORTED_RULES
from sat_descarga_masiva.contabilidad.validator import (
    PostingDecision,
    PostingEligibilityValidator,
)
from sat_descarga_masiva.domain.errors import MappingNotConfigured, UnbalancedJournalCommit
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalDocumentStatus,
    ParseOutcome,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawDoctoRelacionado,
    RawImpuestos,
    RawImpuestosDR,
    RawPago,
    RawPagos20,
    RawTraslado,
    RawTrasladoDR,
)
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlags, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.fiscal.projection import ProcessedDocument
from sat_descarga_masiva.infrastructure.persistence.memory import InMemoryJournalEntryStore

CONTRIBUTOR = Rfc("AAA010101AAA")
EMISOR = Rfc("BBB010101BBB")
#: Another managed client: the same CFDI belongs to two books under two charts (§8:194).
OTHER_CLIENT = Rfc("CCC010101CCC")
UUID = Uuid("333E4567-E89B-12D3-A456-426614174000")
SOURCE_HASH = "c" * 64
MAPPING_VERSION = "2026.01"
#: When the posting becomes durable (§8:166): the fake clock's one answer.
WHEN = datetime(2026, 2, 1, 9, 30, tzinfo=UTC)

#: The ClaveProdServ codes the fixture chart classifies (§8a:207).
GASTO = "84111506"
INVENTARIO = "53131600"
ACTIVO_FIJO = "43211500"

_ACCOUNTS = {
    AccountRole.INGRESOS: "401-001",
    AccountRole.CLIENTES: "105-001",
    AccountRole.CLEARING: "102-099",
    AccountRole.IVA_TRASLADADO_COBRADO: "208-001",
    AccountRole.IVA_TRASLADADO_NO_COBRADO: "209-001",
    AccountRole.GASTO: "601-001",
    AccountRole.INVENTARIO: "115-001",
    AccountRole.PROVEEDORES: "201-001",
    AccountRole.IVA_ACRED_PAGADO: "118-001",
    AccountRole.IVA_ACRED_PENDIENTE: "119-001",
}
_CLASSIFICATION = {
    GASTO: AccountingCategory.GASTO,
    INVENTARIO: AccountingCategory.INVENTARIO,
    ACTIVO_FIJO: AccountingCategory.ACTIVO_FIJO,
}


# --- the collaborators, faked at their ports (§8a:207, §12) ---------------------------------


class _Provider:
    """A `MappingProvider` that records who asked, so a double load cannot hide.

    ``configured=False`` is the §8a:207 configuration failure, not a document outcome: it must
    raise rather than become one flag per document.
    """

    def __init__(self, mapping: AccountMapping | None = None, *, configured: bool = True) -> None:
        self.mapping = (
            mapping
            if mapping is not None
            else AccountMapping(MAPPING_VERSION, _ACCOUNTS, _CLASSIFICATION)
        )
        self.configured = configured
        self.asked: list[Rfc] = []

    def mapping_for(self, client_rfc: Rfc) -> AccountMapping:
        self.asked.append(client_rfc)
        if not self.configured:
            raise MappingNotConfigured(f"no account mapping for {client_rfc.value}")
        return self.mapping


class _Clock:
    """A `Clock` that counts: §8:166's ``recorded_at`` is read once per written record."""

    def __init__(self, now: datetime = WHEN) -> None:
        self._now = now
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self._now


@dataclass
class _Harness:
    """One wired use case plus the fakes a test may inspect after a run."""

    provider: _Provider
    store: InMemoryJournalEntryStore
    clock: _Clock
    use_case: ExecuteAccountingUseCase

    def execute(
        self, processed: ProcessedDocument, *, contributor_rfc: Rfc = CONTRIBUTOR
    ) -> AccountingResult:
        return self.use_case.execute(processed, contributor_rfc=contributor_rfc)

    @property
    def records(self) -> tuple[JournalEntryRecord, ...]:
        """Everything the store holds, read back through the port's own reader."""
        return self.store.for_source(CONTRIBUTOR, UUID)


def _harness(*, provider: _Provider | None = None) -> _Harness:
    """The use case wired with the engine's real propose and validator (§8:159, §8:173)."""
    resolved = provider if provider is not None else _Provider()
    store = InMemoryJournalEntryStore()
    clock = _Clock()
    return _Harness(
        provider=resolved,
        store=store,
        clock=clock,
        use_case=ExecuteAccountingUseCase(
            mapping=resolved,
            validator=PostingEligibilityValidator(resolved, supported_rules=SUPPORTED_RULES),
            store=store,
            clock=clock,
        ),
    )


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN PUE purchase as *source* facts, so a test can corrupt one."""
    base: dict[str, object] = {
        "tipo": "I",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": EMISOR.value,
        "receptor_rfc": CONTRIBUTOR.value,
        "conceptos": (RawConcepto(GASTO, "1", "100.00", "100.00", None),),
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
    *, status: FiscalDocumentStatus = FiscalDocumentStatus.VIGENTE, **overrides: object
) -> FiscalDocument:
    """A parseable document with a *resolved* status: §8:159 posts only a VIGENTE."""
    parsed = build_fiscal_document(_raw(**overrides), SOURCE_HASH)
    assert parsed.document is not None, (parsed.outcome, overrides)
    return replace(parsed.document, status=status)


def _processed(
    document: FiscalDocument | None = None,
    *,
    perspective: Perspective = Perspective.RECIBIDO,
    review_flags: ReviewFlags | None = None,
    quarantine_reason: str | None = None,
) -> ProcessedDocument:
    """The projection result the M2.8 stage would hand over (§6)."""
    return ProcessedDocument(
        outcome=ParseOutcome.PARSED,
        document=document if document is not None else _document(),
        perspective=perspective,
        review_flags=review_flags if review_flags is not None else ReviewFlags(),
        quarantine_reason=quarantine_reason,
    )


def _entry(decision: PostingDecision) -> ProposedJournalEntry:
    """The entry a decision names, asserted present — the readable half of every assertion."""
    assert decision.entry is not None, decision
    return decision.entry


def _flag_types(result: AccountingResult) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in result.decision.review_flags]


def _legs(record: JournalEntryRecord) -> list[tuple[AccountRole, LineSide, str, str | None]]:
    return [
        (line.account_role, line.side, str(line.amount.amount), line.resolved_account)
        for line in record.lines
    ]


# --- A1-A4: the four built rules post, and the record is the decision (§8:181/§8:166) -------


def test_a1_a_received_pue_purchase_is_posted_and_appended() -> None:
    """A1 §8:181/§8:159/§8:166: one POSTED verdict becomes exactly one durable record."""
    harness = _harness()
    result = harness.execute(_processed(_document()))

    assert result.decision.posting_state is PostingState.POSTED
    entry = _entry(result.decision)
    assert entry.rule_id == "4.3"
    assert result.record is not None
    assert result.record.posting_state is PostingState.POSTED
    assert result.record.entry_key == entry.fingerprint(MAPPING_VERSION).canonical()
    assert result.record.recorded_at == WHEN
    assert result.record.entry_date == date(2024, 5, 15)
    assert _legs(result.record) == [
        (AccountRole.GASTO, LineSide.DEBE, "100.00", "601-001"),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.DEBE, "16.00", "118-001"),
        (AccountRole.CLEARING, LineSide.HABER, "116.00", "102-099"),
    ]
    assert harness.records == (result.record,)  # §8:166: appended once, readable back


_RULE_CASES = (
    pytest.param(
        Perspective.EMITIDO,
        "PUE",
        "4.1",
        [
            (AccountRole.CLEARING, LineSide.DEBE, "116.00", "102-099"),
            (AccountRole.INGRESOS, LineSide.HABER, "100.00", "401-001"),
            (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "16.00", "208-001"),
        ],
        id="4.1-emitido-pue",
    ),
    pytest.param(
        Perspective.EMITIDO,
        "PPD",
        "4.2",
        [
            (AccountRole.CLIENTES, LineSide.DEBE, "116.00", "105-001"),
            (AccountRole.INGRESOS, LineSide.HABER, "100.00", "401-001"),
            (AccountRole.IVA_TRASLADADO_NO_COBRADO, LineSide.HABER, "16.00", "209-001"),
        ],
        id="4.2-emitido-ppd",
    ),
    pytest.param(
        Perspective.RECIBIDO,
        "PPD",
        "4.4",
        [
            (AccountRole.GASTO, LineSide.DEBE, "100.00", "601-001"),
            (AccountRole.IVA_ACRED_PENDIENTE, LineSide.DEBE, "16.00", "119-001"),
            (AccountRole.PROVEEDORES, LineSide.HABER, "116.00", "201-001"),
        ],
        id="4.4-recibido-ppd",
    ),
)


@pytest.mark.parametrize(("perspective", "metodo_pago", "rule_id", "legs"), _RULE_CASES)
def test_a2_a4_every_built_rule_posts_and_resolves_every_leg(
    perspective: Perspective, metodo_pago: str, rule_id: str, legs: list[Any]
) -> None:
    """A2-A4 §8:179/180/182 + §8a:207: the other three rows post with their accounts resolved."""
    harness = _harness()
    result = harness.execute(
        _processed(_document(metodo_pago=metodo_pago), perspective=perspective)
    )

    assert result.decision.posting_state is PostingState.POSTED
    assert _entry(result.decision).rule_id == rule_id
    assert result.record is not None
    assert _legs(result.record) == legs
    assert harness.records == (result.record,)


# --- A5-A7: §8:161's SKIPPED set, which is a decision and therefore persisted (§8:169) -----


def test_a5_a_received_payroll_receipt_is_skipped_and_recorded() -> None:
    """A5 §8:161: rule 4.11 — `N` recibido is deliberately out of scope, so nothing posts."""
    harness = _harness()
    result = harness.execute(_processed(_document(tipo="N")))

    assert result.decision.posting_state is PostingState.SKIPPED
    assert result.decision.review_flags == ()  # a skip needs no human decision
    assert _entry(result.decision).rule_id == "4.11"
    assert result.record is not None
    assert result.record.posting_state is PostingState.SKIPPED
    assert result.record.lines == ()
    assert result.record.rule_id == "4.11"
    assert harness.records == (result.record,)


def test_a6_a_traslado_is_skipped_for_either_perspective() -> None:
    """A6 §8:161: rule 4.12 — `T` carries no financial operation, emitted or received."""
    for perspective in (Perspective.EMITIDO, Perspective.RECIBIDO):
        harness = _harness()
        result = harness.execute(_processed(_document(tipo="T"), perspective=perspective))

        assert result.decision.posting_state is PostingState.SKIPPED
        assert _entry(result.decision).rule_id == "4.12"
        assert harness.records[0].posting_state is PostingState.SKIPPED


def test_a7_a_received_retenciones_acuse_is_skipped_and_recorded() -> None:
    """A7 §8:161: rule 4.14 — `R` recibido feeds DIOT instead of the journal."""
    harness = _harness()
    result = harness.execute(_processed(_document(tipo="R")))

    assert result.decision.posting_state is PostingState.SKIPPED
    assert _entry(result.decision).rule_id == "4.14"
    assert harness.records[0].posting_state is PostingState.SKIPPED


# --- A9-A13: a refusal that names no entry writes nothing and reads no clock (§8:167) ------

#: What every document-level refusal asserts: `PROPOSED` with a reason, and no §8:194 identity
#: to append under — inventing one is exactly what §8:167 forbids.
_DOCUMENT_LEVEL = (
    "§8:167: the document itself could not be keyed or read, so the review fact attaches to"
    " the document and there is nothing to append"
)


def _assert_document_level(result: AccountingResult, harness: _Harness) -> None:
    assert result.decision.posting_state is PostingState.PROPOSED, _DOCUMENT_LEVEL
    assert result.decision.entry is None
    assert result.decision.review_flags  # §8:167: a refusal always says why
    assert result.record is None
    assert harness.records == ()
    assert harness.clock.calls == 0  # nothing became durable, so nothing was dated (§8:166)


@pytest.mark.parametrize("tipo", ["E", "Z"], ids=["egreso", "not-a-cfdi-type"])
def test_a9_an_unsupported_document_type_is_reviewed_never_posted(tipo: str) -> None:
    """A9 §8:161/§8:177: an unbuilt row of §8's table waits for a human; `Z` is not a type."""
    harness = _harness()
    result = harness.execute(_processed(_document(tipo=tipo)))

    _assert_document_level(result, harness)
    assert _flag_types(result) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_a10_an_undetermined_perspective_is_never_decided_for_the_human() -> None:
    """A10 §8:163: an undetermined perspective is precisely what cannot be decided."""
    harness = _harness()
    result = harness.execute(_processed(_document(), perspective=Perspective.UNDETERMINED))

    _assert_document_level(result, harness)


def test_a11_a_document_without_a_fecha_cannot_be_dated_and_is_not_appended() -> None:
    """A11 §8a:209/§8:167: no `Fecha` means no entry date, so no §8:194 identity."""
    harness = _harness()
    result = harness.execute(_processed(_document(fecha=None)))

    _assert_document_level(result, harness)
    assert ReviewFlagType.MISSING_SOURCE_FIELD in _flag_types(result)


def test_a12_a_document_without_a_tfd_uuid_cannot_be_keyed_and_is_not_appended() -> None:
    """A12 §8:194: the TFD UUID is the identity; without it there is nothing to key."""
    harness = _harness()
    result = harness.execute(_processed(_document(uuid=None)))

    _assert_document_level(result, harness)
    assert ReviewFlagType.MISSING_POSTING_IDENTITY in _flag_types(result)


@pytest.mark.parametrize("metodo_pago", [None, "XYZ"], ids=["absent", "unknown"])
def test_a13_an_unknown_metodo_pago_chooses_neither_row_of_the_table(
    metodo_pago: str | None,
) -> None:
    """A13 §8:171/§8:179-182: presuming PUE would book a payment the document never stated."""
    harness = _harness()
    result = harness.execute(_processed(_document(metodo_pago=metodo_pago)))

    _assert_document_level(result, harness)
    assert _flag_types(result) == [ReviewFlagType.MISSING_SOURCE_FIELD]


# --- A8, A14-A18: a refusal that names an entry is still recorded (§8:167's audit evidence) -


def _assert_proposed_record(result: AccountingResult, harness: _Harness) -> JournalEntryRecord:
    """§8:167/§8:169: a refusal is real evidence — `PROPOSED`, dated, appended, never posted."""
    assert result.decision.posting_state is PostingState.PROPOSED
    assert result.decision.review_flags
    assert result.record is not None
    assert result.record.posting_state is PostingState.PROPOSED
    assert result.record.recorded_at == WHEN
    assert harness.records == (result.record,)
    assert harness.clock.calls == 1
    return result.record


def test_a8_an_issued_payroll_draft_names_a_zero_line_entry_never_a_skip() -> None:
    """A8 §8a:205: `N` *emitido* is a draft — a recorded, zero-line `NEEDS_REVIEW`, never a skip."""
    harness = _harness()
    result = harness.execute(_processed(_document(tipo="N"), perspective=Perspective.EMITIDO))

    record = _assert_proposed_record(result, harness)
    assert record.rule_id == "4.10"  # §8:194: keyed by the rule that raised the draft
    assert record.lines == ()
    assert record.entry_date == date(2024, 5, 15)
    assert _flag_types(result) == [ReviewFlagType.PAYROLL_DRAFT_UNSUPPORTED]


@pytest.mark.parametrize(
    "status",
    [FiscalDocumentStatus.UNKNOWN, FiscalDocumentStatus.CANCELLED],
    ids=["unknown", "cancelled"],
)
def test_a14_an_unresolved_source_state_is_refused_and_the_refusal_is_recorded(
    status: FiscalDocumentStatus,
) -> None:
    """A14 §8:159/§8:194: nothing uncertain is posted, and the entry it refused is explainable."""
    harness = _harness()
    result = harness.execute(_processed(_document(status=status)))

    record = _assert_proposed_record(result, harness)
    assert record.posting_state is not PostingState.POSTED
    assert _legs(record) == [
        (AccountRole.GASTO, LineSide.DEBE, "100.00", None),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.DEBE, "16.00", None),
        (AccountRole.CLEARING, LineSide.HABER, "116.00", None),
    ]


def test_a15_a_foreign_currency_document_is_refused_unless_its_rate_is_resolved() -> None:
    """A15 §8:193: no resolved rate means no deterministic valuation, so no posting."""
    harness = _harness()
    result = harness.execute(_processed(_document(moneda="USD", tipo_cambio="17.5")))

    _assert_proposed_record(result, harness)
    assert ReviewFlagType.AMBIGUOUS_FX in _flag_types(result)


def test_a16_an_unmapped_role_is_never_defaulted_to_an_account() -> None:
    """A16 §8a:207: a role the chart does not name is `None`, not something plausible."""
    harness = _harness(
        provider=_Provider(
            AccountMapping(MAPPING_VERSION, {AccountRole.GASTO: "601-001"}, _CLASSIFICATION)
        )
    )
    result = harness.execute(_processed(_document()))

    record = _assert_proposed_record(result, harness)
    assert ReviewFlagType.UNMAPPED_ACCOUNT in _flag_types(result)
    # nothing was posted, so no leg claims an account: a refused decision resolves nothing and
    # the record says exactly that rather than showing the one role that did resolve
    assert {line.resolved_account for line in record.lines} == {None}
    assert result.decision.accounts == {}


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {"conceptos": (RawConcepto("99999999", "1", "100.00", "100.00", None),)},
            id="unclassified-product",
        ),
        pytest.param(
            {"conceptos": (RawConcepto(ACTIVO_FIJO, "1", "100.00", "100.00", None),)},
            id="capital-goods",
        ),
        pytest.param({"conceptos": ()}, id="no-conceptos"),
    ],
)
def test_a17_a_purchase_that_does_not_classify_is_reviewed_whole(overrides: dict[str, Any]) -> None:
    """A17 §8a:196/§8:173: the rule refuses the whole document and proposes no legs at all."""
    harness = _harness()
    result = harness.execute(_processed(_document(**overrides)))

    record = _assert_proposed_record(result, harness)
    assert record.rule_id == "4.3"  # §8:194: keyed by the rule that refused it
    assert record.lines == ()
    assert record.entry_date == date(2024, 5, 15)


def test_a18_a_documents_own_review_flag_outranks_the_posting_decision() -> None:
    """A18 §8:159: an unresolved review flag on the document stops the posting it would post."""
    flagged = replace(
        _document(),
        review_flags=ReviewFlags(
            (ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "the signature did not verify"),)
        ),
    )
    harness = _harness()
    result = harness.execute(_processed(flagged))

    record = _assert_proposed_record(result, harness)
    assert _flag_types(result) == [ReviewFlagType.CFDI_SIGNATURE]
    assert record.posting_state is PostingState.PROPOSED


# --- A19-A24: the collaborators are touched exactly as often as §8 allows ------------------


class _RecordingValidator:
    """The engine's validator, wrapped to record what ``decide`` was handed (§8a:207).

    ``mapping`` is what makes a decision's outcome versioned (§8:194), so *which* object reached
    it — and how many times — is part of the contract, not an implementation detail.
    """

    def __init__(self, inner: PostingEligibilityValidator) -> None:
        self.inner = inner
        self.calls = 0
        self.mappings: list[AccountMapping | None] = []

    def decide(
        self,
        request: PostingContext,
        proposal: Any,
        *,
        mapping: AccountMapping | None = None,
    ) -> PostingDecision:
        self.calls += 1
        self.mappings.append(mapping)
        return self.inner.decide(request, proposal, mapping=mapping)


class _SpyMapping(AccountMapping):
    """A chart that records which documents ``classify`` was asked about (§8a:207).

    Observing *asking* is the only way §8a:207's gate is visible: the emitter-side rules ignore
    the answer, so a document that should never have been classified would look identical.
    ``asked`` is attached with ``object.__setattr__`` because `AccountMapping` is frozen.
    """

    asked: list[FiscalDocument]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        object.__setattr__(self, "asked", [])

    def classify(self, document: FiscalDocument) -> Classification:
        self.asked.append(document)
        return super().classify(document)


def test_a19_the_mapping_is_loaded_once_and_the_same_object_reaches_the_validator() -> None:
    """A19 §8a:207/§8:194: one load, one version — a second load could version a posting twice."""
    provider = _Provider()
    store = InMemoryJournalEntryStore()
    clock = _Clock()
    validator = _RecordingValidator(
        PostingEligibilityValidator(provider, supported_rules=SUPPORTED_RULES)
    )
    use_case = ExecuteAccountingUseCase(
        mapping=provider, validator=validator, store=store, clock=clock
    )

    result = use_case.execute(_processed(_document()), contributor_rfc=CONTRIBUTOR)

    assert provider.asked == [CONTRIBUTOR]
    assert validator.calls == 1
    assert validator.mappings == [provider.mapping]  # the very object that was loaded
    assert result.record is not None
    assert result.record.mapping_version == MAPPING_VERSION


def test_a20_an_unconfigured_client_stops_the_stage_instead_of_flagging_every_document() -> None:
    """A20 §8a:207: a missing chart of accounts is a configuration failure, not a review case."""
    harness = _harness(provider=_Provider(configured=False))

    with pytest.raises(MappingNotConfigured):
        harness.execute(_processed(_document()))

    assert harness.records == ()
    assert harness.clock.calls == 0


def test_a21_only_a_received_income_comprobante_asks_the_classification_table() -> None:
    """A21 §8a:207: §8:181/182's base role is the *only* thing a chart has to classify."""
    spy = _SpyMapping(MAPPING_VERSION, _ACCOUNTS, _CLASSIFICATION)
    harness = _harness(provider=_Provider(spy))

    # an emitted document: §8:179/180 fix the base role, so the chart is never consulted
    emitted = harness.execute(
        _processed(
            _document(conceptos=(RawConcepto("99999999", "1", "100.00", "100.00", None),)),
            perspective=Perspective.EMITIDO,
        )
    )
    assert emitted.decision.posting_state is PostingState.POSTED
    assert spy.asked == []

    # a received purchase: §8:181's base role *is* the client's classification decision
    harness.execute(_processed(_document()))
    assert len(spy.asked) == 1


def test_a22_a_received_document_that_is_not_an_ingreso_is_never_classified() -> None:
    """A22 §8:161/§8a:207: a skip is decided without asking a question it does not need."""
    spy = _SpyMapping(MAPPING_VERSION, _ACCOUNTS, _CLASSIFICATION)
    harness = _harness(provider=_Provider(spy))

    for tipo in ("T", "N", "R"):
        result = harness.execute(_processed(_document(tipo=tipo)))
        assert result.decision.posting_state is PostingState.SKIPPED

    assert spy.asked == []


def test_a23_the_clock_is_read_once_per_written_record_and_never_without_one() -> None:
    """A23 §8:166: ``recorded_at`` is when the posting became durable — one fact, one moment."""
    posted = _harness()
    posted.execute(_processed(_document()))
    assert posted.clock.calls == 1

    # a refusal that names an entry is still a durable fact, so it is dated too
    refused = _harness()
    refused.execute(_processed(_document(moneda="USD")))
    assert refused.clock.calls == 1

    # a document-level refusal writes nothing, so there is no moment to record
    unmatched = _harness()
    unmatched.execute(_processed(_document(uuid=None)))
    assert unmatched.clock.calls == 0


def test_a24_the_result_carries_no_second_posting_state_vocabulary() -> None:
    """A24 §8:166: the verdict lives on the decision; a record exists iff one was named."""
    harness = _harness()
    posted = harness.execute(_processed(_document()))
    assert posted.record is not None
    assert posted.decision.entry is not None
    assert not hasattr(posted, "posting_state")  # nothing to drift from §8:166's words
    assert posted.decision.posting_state is posted.record.posting_state

    refused = _harness().execute(_processed(_document(uuid=None)))
    assert refused.decision.entry is None
    assert refused.record is None


# --- A26-A30: the commit gates, and the one guard that runs before anything else -----------


def _line(role: AccountRole, key: str, side: LineSide, amount: str) -> JournalLine:
    """One proposed leg, hand-built so the balance gate can be reached with bad arithmetic."""
    return JournalLine(
        account_role=role, line_key=key, side=side, amount=NormalizedAmount(Decimal(amount))
    )


def _journal_line(
    ordinal: int, role: AccountRole, side: LineSide, amount: str, account: str | None
) -> JournalLineRecord:
    """One *written* leg, hand-built for the same reason."""
    return JournalLineRecord(
        account_role=role,
        line_key=f"leg-{ordinal}",
        ordinal=ordinal,
        side=side,
        amount=NormalizedAmount(Decimal(amount)),
        resolved_account=account,
    )


def _unbalanced_entry() -> ProposedJournalEntry:
    """A proposal whose `Debe` is 100.00 and `Haber` 90.00 — §8:158's exact failure."""
    return ProposedJournalEntry(
        rule_id="4.3",
        rule_version="1",
        contributor_rfc=CONTRIBUTOR,
        source_uuid=UUID,
        source_hash=SOURCE_HASH,
        entry_date=date(2024, 5, 15),
        lines=(
            _line(AccountRole.GASTO, "base", LineSide.DEBE, "100.00"),
            _line(AccountRole.CLEARING, "clearing", LineSide.HABER, "90.00"),
        ),
    )


def _unbalanced_record(posting_state: PostingState) -> JournalEntryRecord:
    """The record a buggy writer would hand the store: every leg resolved, and out of balance."""
    return JournalEntryRecord(
        entry_key='["AAA010101AAA","333E4567","4.3","1","","2026.01"]',
        contributor_rfc=CONTRIBUTOR,
        source_uuid=UUID,
        source_hash=SOURCE_HASH,
        rule_id="4.3",
        rule_version="1",
        mapping_version=MAPPING_VERSION,
        policy_version="1",
        posting_state=posting_state,
        entry_date=date(2024, 5, 15),
        recorded_at=WHEN,
        lines=(
            _journal_line(0, AccountRole.GASTO, LineSide.DEBE, "100.00", "601-001"),
            _journal_line(1, AccountRole.CLEARING, LineSide.HABER, "90.00", "102-099"),
        ),
    )


def _balanced_record() -> JournalEntryRecord:
    """The same two legs, correctly balanced: what the gate must let through."""
    return replace(
        _unbalanced_record(PostingState.POSTED),
        lines=(
            _journal_line(0, AccountRole.GASTO, LineSide.DEBE, "100.00", "601-001"),
            _journal_line(1, AccountRole.CLEARING, LineSide.HABER, "100.00", "102-099"),
        ),
    )


def _skipped_record() -> JournalEntryRecord:
    """§8:161: a skip carries no legs at all, so it has no balance to be held to."""
    return replace(
        _unbalanced_record(PostingState.PROPOSED), posting_state=PostingState.SKIPPED, lines=()
    )


@dataclass
class _StubVerdict:
    """A `PostingDecision`-shaped verdict whose record contradicts §8:158 — a writer bug."""

    entry: ProposedJournalEntry
    record: JournalEntryRecord

    def to_record(self, *, recorded_at: datetime) -> JournalEntryRecord:
        return replace(self.record, recorded_at=recorded_at)


class _StubValidator:
    """Answers with one hand-built verdict, so the commit gate can be reached directly."""

    def __init__(self, verdict: _StubVerdict) -> None:
        self.verdict = verdict
        self.calls = 0

    def decide(
        self,
        request: PostingContext,
        proposal: Any,
        *,
        mapping: AccountMapping | None = None,
    ) -> _StubVerdict:
        self.calls += 1
        return self.verdict


def test_a26_re_running_the_same_document_appends_one_record() -> None:
    """A26 §8:166: a posting state is assigned once, so a re-run adds no second fact."""
    harness = _harness()
    first = harness.execute(_processed(_document()))
    second = harness.execute(_processed(_document()))

    assert first.record is not None
    assert first.record == second.record
    assert harness.records == (first.record,)


def test_a27_a_posted_record_that_does_not_balance_aborts_before_the_append() -> None:
    """A27 §8:158: the second gate is defense in depth — it aborts, and it never downgrades."""
    store = InMemoryJournalEntryStore()
    clock = _Clock()
    use_case = ExecuteAccountingUseCase(
        mapping=_Provider(),
        validator=_StubValidator(
            _StubVerdict(entry=_unbalanced_entry(), record=_unbalanced_record(PostingState.POSTED))
        ),
        store=store,
        clock=clock,
    )

    with pytest.raises(UnbalancedJournalCommit):
        use_case.execute(_processed(_document()), contributor_rfc=CONTRIBUTOR)

    assert store.for_source(CONTRIBUTOR, UUID) == ()  # §8:166: nothing half-written


def test_a28_the_commit_time_gate_only_holds_posted_records_to_the_balance() -> None:
    """A28 §8:161/§8:167: a skip has no legs, a refusal is not a commitment — neither is blocked."""
    _assert_balanced_for_commit(_balanced_record())  # a real POSTED record passes
    _assert_balanced_for_commit(_unbalanced_record(PostingState.PROPOSED))
    _assert_balanced_for_commit(_skipped_record())


def test_a29_a_flag_carried_only_by_the_projection_still_stops_the_posting() -> None:
    """A29 §6/§8:159: M2's flags ride the projection, so merging them is not optional."""
    flagged = _processed(
        _document(),
        review_flags=ReviewFlags(
            (ReviewFlag(ReviewFlagType.CFDI_SIGNATURE, "the projection rejected the signature"),)
        ),
    )
    assert flagged.document is not None
    assert flagged.document.review_flags.open_flags() == ()  # nothing on the document itself

    harness = _harness()
    result = harness.execute(flagged)

    assert result.decision.posting_state is PostingState.PROPOSED
    assert _flag_types(result) == [ReviewFlagType.CFDI_SIGNATURE]
    assert result.record is not None
    assert result.record.posting_state is PostingState.PROPOSED


def test_a30_a_projection_with_no_document_is_a_calling_error_not_a_document_outcome() -> None:
    """A30 §6: a quarantined artifact was never projectable, so there is nothing to account."""
    harness = _harness()
    quarantined = ProcessedDocument(
        outcome=ParseOutcome.PARSED,
        document=None,
        perspective=Perspective.RECIBIDO,
        quarantine_reason="the TFD UUID disagrees with the artifact's own name",
    )

    with pytest.raises(ValueError):
        harness.execute(quarantined)

    # nothing downstream ran at all: no chart read, no mapping version, no timestamp
    assert harness.provider.asked == []
    assert harness.records == ()
    assert harness.clock.calls == 0


# --- T23: a REP is accounted against the ledger, not against what it states (§8:184/§8:192) --

#: The CFDI a REP settles — its own UUID, never the REP's (§8:194).
ORIGINAL = Uuid("111E4567-E89B-12D3-A456-426614174000")


def _original(**overrides: object) -> RawDoctoRelacionado:
    """The `DoctoRelacionado` a REP states: a fully paid MXN invoice carrying 16.00 of IVA."""
    base: dict[str, object] = {
        "id_documento": ORIGINAL.value,
        "moneda_dr": "MXN",
        "num_parcialidad": "1",
        "imp_saldo_ant": "116.00",
        "imp_pagado": "116.00",
        "objeto_imp_dr": "01",
        "imp_saldo_insoluto": "0.00",
        "impuestos_dr": RawImpuestosDR(
            traslados_dr=(
                RawTrasladoDR(
                    base_dr="100.00", impuesto_dr="002", tipo_factor_dr="Tasa", importe_dr="16.00"
                ),
            )
        ),
    }
    base.update(overrides)
    return RawDoctoRelacionado(**base)  # type: ignore[arg-type]


def _rep_document() -> FiscalDocument:
    """A `P` comprobante: the client issued the invoice this collection settles (EMITIDO)."""
    raw = RawCfd(
        tipo="P",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=CONTRIBUTOR.value,
        receptor_rfc=EMISOR.value,
        conceptos=(RawConcepto("84111506", "1", "0.00", "0.00", None),),
        impuestos=RawImpuestos(),
        total="0.00",
        subtotal="0.00",
        uuid=UUID.value,
        fecha="2024-03-15T10:30:00",
        metodo_pago=None,
        pagos=RawPagos20(
            version="2.0",
            pagos=(
                RawPago(
                    fecha_pago="2024-04-01T09:00:00",
                    forma_de_pago_p="03",
                    moneda_p="MXN",
                    monto="116.00",
                    doctos_relacionados=(_original(),),
                ),
            ),
        ),
    )
    parsed = build_fiscal_document(raw, SOURCE_HASH)
    assert parsed.document is not None, parsed.outcome
    return replace(parsed.document, status=FiscalDocumentStatus.VIGENTE)


def _posted_original() -> JournalEntryRecord:
    """The client's books holding the settled CFDI as a `POSTED` entry — §8:192's presence seam."""
    return JournalEntryRecord(
        entry_key='["AAA010101AAA","111E4567","4.1","1","","2026.01"]',
        contributor_rfc=CONTRIBUTOR,
        source_uuid=ORIGINAL,
        source_hash=SOURCE_HASH,
        rule_id="4.1",
        rule_version="1",
        mapping_version=MAPPING_VERSION,
        policy_version="1",
        posting_state=PostingState.POSTED,
        entry_date=date(2024, 3, 15),
        recorded_at=WHEN,
        lines=(
            _journal_line(0, AccountRole.CLEARING, LineSide.DEBE, "116.00", "102-099"),
            _journal_line(1, AccountRole.INGRESOS, LineSide.HABER, "100.00", "401-001"),
            _journal_line(
                2, AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "16.00", "208-001"
            ),
        ),
    )


def test_a23_a_rep_is_accounted_against_the_ledger_the_client_holds() -> None:
    """T23 §8:184/§8:192: the store, not the REP, says whether the original was ever booked.

    A REP states payments, never that the CFDI it settles was ever posted, so the application layer
    resolves that presence from the store and the rule reads it as `posted_source_uuids`. With no
    `POSTED` original the collection is refused — and the refusal is still recorded for review;
    once the client's books hold the original as `POSTED`, the same REP reclassifies it and posts.
    """
    collected = _processed(_rep_document(), perspective=Perspective.EMITIDO)

    refused = _harness().execute(collected)
    assert refused.decision.posting_state is PostingState.PROPOSED
    assert _flag_types(refused) == [ReviewFlagType.MISSING_REP_ORIGINAL]
    assert refused.record is not None  # §8:167: the refusal is the audit evidence

    harness = _harness()
    harness.store.append(_posted_original())  # nothing but the ledger changed
    settled = harness.execute(collected)

    assert settled.decision.posting_state is PostingState.POSTED
    assert settled.record is not None
    assert settled.record.rule_id == "4.5a"
    assert _legs(settled.record) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00", "102-099"),
        (AccountRole.CLIENTES, LineSide.HABER, "116.00", "105-001"),
        (AccountRole.IVA_TRASLADADO_NO_COBRADO, LineSide.DEBE, "16.00", "209-001"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "16.00", "208-001"),
    ]
    assert harness.records == (settled.record,)  # §8:166: appended once, readable back
