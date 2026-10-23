"""M3 step E2: rule 4.8 — a credit note over a PUE original, received (§8a:212).

A received credit note reverses §8:181 (4.3): the cash comes back as a debit to `CLEARING`,
the classified base role (`Gasto`/`Inventario`) is credited, and so is the IVA that was booked
as paid (`IVA_ACRED_PAGADO`). The base role is the one thing the row does not fix, so the
client's `ClaveProdServ → AccountingCategory → AccountRole` answer arrives on the context and a
note that does not reduce to one postable role is reviewed whole (§8a:207).

The refund itself is not observable yet, so every entry carries `EGRESO_IVA_EVIDENCE_MISSING`
and stays `PROPOSED` with complete, balanced legs.
"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sat_descarga_masiva.contabilidad.classification import (
    Classification,
    ClassificationRefusal,
    ClassificationRefusalKind,
)
from sat_descarga_masiva.contabilidad.journal import LineSide, PostingState
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    RelatedDocument,
    ReviewRequest,
)
from sat_descarga_masiva.contabilidad.rules.credit_notes import (
    RULE_4_8,
    RULE_4_8_VERSION,
    rule_4_8,
)
from sat_descarga_masiva.contabilidad.rules.posting import SUPPORTED_RULES, propose
from sat_descarga_masiva.contabilidad.validator import PostingEligibilityValidator
from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocument,
    FiscalDocumentStatus,
)
from sat_descarga_masiva.domain.model.perspective import Perspective
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawCfdiRelacionados,
    RawConcepto,
    RawImpuestos,
    RawTraslado,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_MAPPINGS = _FIXTURES / "mappings"
_SOURCE_HASH = "c" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
EMISOR = Rfc("BBB010101BBB")
NOTE_UUID = "be80345d-917f-40bb-a98f-4a73939353c5"
ORIGINAL_UUID = Uuid("ce80345d-917f-40bb-a98f-4a73939353c5")
PUE = "PUE"
PPD = "PPD"
_GASTO = "84111506"


def _money(text: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(text))


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN received `E` note naming one original, as *source* facts."""
    base: dict[str, object] = {
        "tipo": "E",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": EMISOR.value,
        "receptor_rfc": CONTRIBUTOR.value,
        "conceptos": (RawConcepto(_GASTO, "1", "100.00", "100.00", None),),
        "impuestos": RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),), total_traslados="16.00"
        ),
        "total": "116.00",
        "uuid": NOTE_UUID,
        "subtotal": "100.00",
        "fecha": "2026-03-15T12:00:00+00:00",
        "metodo_pago": None,
        "cfdi_relacionados": (RawCfdiRelacionados("01", (ORIGINAL_UUID.value,)),),
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(**overrides: object) -> FiscalDocument:
    result = build_fiscal_document(_raw(**overrides), _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _original(**overrides: object) -> RelatedDocument:
    """The resolved original the note names: PUE and `POSTED` unless a test says otherwise."""
    values: dict[str, object] = {
        "uuid": ORIGINAL_UUID,
        "tipo_relacion": "01",
        "metodo_pago": PUE,
        "total": _money("116.00"),
        "posted": True,
        "moneda": "MXN",
    }
    values.update(overrides)
    return RelatedDocument(**values)  # type: ignore[arg-type]


def _context(
    document: FiscalDocument | None = None,
    classification: Classification | None = AccountRole.GASTO,
    *,
    original: RelatedDocument | None = None,
) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document if document is not None else _document(),
        perspective=Perspective.RECIBIDO,
        classification=classification,
        related_documents=(original if original is not None else _original(),),
    )


def _refused(proposal: object) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


def _flag_types(review: ReviewRequest) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in review.flags]


def _legs(review: ReviewRequest) -> list[tuple[AccountRole, LineSide, str]]:
    entry = review.entry
    assert entry is not None
    return [(line.account_role, line.side, str(line.amount.amount)) for line in entry.lines]


# --- the golden reversal (§8a:212) -------------------------------------------------------


def test_rule_4_8_reverses_the_received_pue_entry() -> None:
    """§8a:212: `DR Clearing=Total · CR Gasto=base · CR IVA Acred. Pagado=IVA` — 4.3 mirrored."""
    review = _refused(rule_4_8(_context()))
    assert _legs(review) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
        (AccountRole.GASTO, LineSide.HABER, "100.00"),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.HABER, "16.00"),
    ]
    entry = review.entry
    assert entry is not None
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_8
    assert entry.rule_version == RULE_4_8_VERSION
    assert entry.posting_state is PostingState.PROPOSED


def test_the_entry_names_the_evidence_it_cannot_observe() -> None:
    """§8a:212: nothing observes the refund, so the complete entry must not post."""
    review = _refused(rule_4_8(_context()))
    assert _flag_types(review) == [ReviewFlagType.EGRESO_IVA_EVIDENCE_MISSING]


def test_the_base_leg_carries_the_classified_role() -> None:
    """§8a:207: the mapping decides the base role for a received note, exactly as for 4.3."""
    review = _refused(rule_4_8(_context(classification=AccountRole.INVENTARIO)))
    assert _legs(review)[1] == (AccountRole.INVENTARIO, LineSide.HABER, "100.00")


def test_the_note_presumes_nothing_paid() -> None:
    """§8:171/§8a:212: a note is not paid, so its mirror books no `ASSUMED_PUE`."""
    review = _refused(rule_4_8(_context()))
    entry = review.entry
    assert entry is not None
    assert entry.assumptions == ()


def test_the_line_keys_stay_structural() -> None:
    """§8:194: clearing/base/iva identify the legs; the evidence flag is not part of a key."""
    review = _refused(rule_4_8(_context()))
    entry = review.entry
    assert entry is not None
    assert [line.line_key for line in entry.lines] == ["clearing", "base", "iva"]


def test_an_exempt_note_books_no_iva_leg() -> None:
    """An exempt received note reverses no IVA, so it books no IVA leg."""
    document = _document(
        impuestos=RawImpuestos(traslados=(RawTraslado("002", "Exento", None, None),)),
        total="100.00",
    )
    review = _refused(rule_4_8(_context(document)))
    assert _legs(review) == [
        (AccountRole.CLEARING, LineSide.DEBE, "100.00"),
        (AccountRole.GASTO, LineSide.HABER, "100.00"),
    ]


def test_the_entry_carries_the_notes_own_provenance() -> None:
    """§8:169/194 + §8a:209: keyed and dated by the note, never by the original."""
    document = _document()
    review = _refused(rule_4_8(_context(document)))
    entry = review.entry
    assert entry is not None
    assert entry.contributor_rfc == CONTRIBUTOR
    assert entry.source_uuid == document.source_uuid
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == document.fecha


# --- the classification is a precondition -------------------------------------------------


def test_a_received_note_without_a_classification_is_reviewed() -> None:
    """§8a:207: no resolved role is a missing precondition, never a presumed Gasto."""
    review = _refused(rule_4_8(_context(classification=None)))
    assert _flag_types(review) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    entry = review.entry
    assert entry is not None and entry.lines == ()


def test_a_capital_goods_note_is_refused_by_kind() -> None:
    """§8:167: the refusal's machine-readable kind maps the flag, not its prose."""
    refusal = ClassificationRefusal(
        ClassificationRefusalKind.CAPITAL_GOODS,
        "an activo fijo product is not a postable category (§8a:196)",
    )
    review = _refused(rule_4_8(_context(classification=refusal)))
    assert _flag_types(review) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_an_unclassified_product_is_an_unmapped_account() -> None:
    """§8a:207: an unknown ClaveProdServ leaves the account unmapped, and says so."""
    refusal = ClassificationRefusal(
        ClassificationRefusalKind.UNCLASSIFIED_PRODUCT,
        "no AccountingCategory for this ClaveProdServ",
    )
    review = _refused(rule_4_8(_context(classification=refusal)))
    assert _flag_types(review) == [ReviewFlagType.UNMAPPED_ACCOUNT]


# --- the original is the selector ---------------------------------------------------------


def test_a_ppd_note_over_a_pue_original_is_still_rule_4_8s() -> None:
    """§8a:212: the note's own method is ignored; the original's PUE selects this row."""
    review = _refused(rule_4_8(_context(_document(metodo_pago=PPD))))
    assert _legs(review)[0] == (AccountRole.CLEARING, LineSide.DEBE, "116.00")


def test_a_ppd_original_is_not_rule_4_8s() -> None:
    """§8a:212: a PPD original is 4.9's row, never this one's clearing treatment."""
    review = _refused(rule_4_8(_context(original=_original(metodo_pago=PPD))))
    assert _flag_types(review) == [ReviewFlagType.UNSUPPORTED_RULE]
    entry = review.entry
    assert entry is not None and entry.lines == ()


def test_an_issued_note_is_not_rule_4_8s() -> None:
    """The side selects too: an issued note over a PUE original is 4.6's."""
    context = replace(_context(), perspective=Perspective.EMITIDO)
    review = _refused(rule_4_8(context))
    assert _flag_types(review) == [ReviewFlagType.UNSUPPORTED_RULE]


# --- the evidence gates -------------------------------------------------------------------


def test_an_unposted_original_is_recorded_and_refused() -> None:
    """§8:173: a note cannot reverse a payable the ledger never held."""
    review = _refused(rule_4_8(_context(original=_original(posted=False))))
    assert _flag_types(review) == [ReviewFlagType.EGRESO_ORIGINAL_NOT_POSTED]
    entry = review.entry
    assert entry is not None and entry.lines == ()


def test_a_note_that_exceeds_its_original_is_refused() -> None:
    """§8a:212: the original's Total caps a single note; more would overstate the reversal."""
    review = _refused(rule_4_8(_context(original=_original(total=_money("115.00")))))
    assert _flag_types(review) == [ReviewFlagType.EGRESO_EXCEEDS_ORIGINAL]


def test_an_original_without_a_total_is_reviewed() -> None:
    """The cap cannot be presumed: an unreadable Total is a precondition, not a zero."""
    review = _refused(rule_4_8(_context(original=_original(total=None))))
    assert _flag_types(review) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_a_note_in_another_currency_is_not_valued() -> None:
    """§8:193: no rate chain valued the note, so its reversal would be a conversion."""
    document = replace(_document(), moneda="USD")
    review = _refused(rule_4_8(_context(document)))
    assert _flag_types(review) == [ReviewFlagType.AMBIGUOUS_FX]


# --- the whole seam (§12's golden path) ---------------------------------------------------


def test_the_registry_contains_rule_4_8_at_its_version() -> None:
    """§8:159's supported rule: the validator is wired with this row's id and version."""
    assert SUPPORTED_RULES[RULE_4_8] == RULE_4_8_VERSION


def test_the_selector_finds_rule_4_8_for_a_received_pue_original() -> None:
    """§8:161: a received `E` note over a PUE original is claimed by 4.8."""
    review = _refused(propose(_context()))
    entry = review.entry
    assert entry is not None
    assert entry.rule_id == RULE_4_8


def test_the_reversal_reaches_the_validator_and_stays_proposed() -> None:
    """Source facts → classify → rule → validator: legs resolve, evidence keeps it open."""
    document = replace(_document(), status=FiscalDocumentStatus.VIGENTE)
    mapping = YamlMappingProvider(_MAPPINGS).mapping_for(CONTRIBUTOR)
    context = _context(document, classification=mapping.classify(document))
    assert isinstance(context.classification, AccountRole)
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, propose(context))
    assert decision.posting_state is PostingState.PROPOSED
    assert ReviewFlagType.EGRESO_IVA_EVIDENCE_MISSING in [
        flag.flag_type for flag in decision.review_flags
    ]
    entry = decision.entry
    assert entry is not None and entry.is_balanced()
    assert {line.account_role for line in entry.lines} == {
        AccountRole.CLEARING,
        AccountRole.GASTO,
        AccountRole.IVA_ACRED_PAGADO,
    }
