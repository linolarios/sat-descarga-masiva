"""M3 step 4f: rule 4.4 — a received PPD purchase comprobante (§8:182).

The mirror of 4.3 with one difference that matters: nothing is presumed paid. The payable lands
on `PROVEEDORES` (not the clearing account), the IVA is the *pending* creditable kind
(`IVA_ACRED_PENDIENTE`), and the entry carries **no** `ASSUMED_PUE` (§8:171/§8a:208). The
classified base role (`Gasto`/`Inventario`) is 4.3's, resolved the same way (§8a:196/207).

So these tests focus where 4.4 differs: which leg the money lands on, which IVA role it books,
that the PUE row cannot claim a PPD document (and vice versa), and one golden end-to-end posting
through the mapping file.
"""

from dataclasses import replace
from pathlib import Path

from sat_descarga_masiva.contabilidad.classification import Classification
from sat_descarga_masiva.contabilidad.journal import (
    AccountingAssumption,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest
from sat_descarga_masiva.contabilidad.rules.posting import (
    RULE_4_4,
    RULE_4_4_VERSION,
    SUPPORTED_RULES,
    rule_4_4,
)
from sat_descarga_masiva.contabilidad.validator import PostingEligibilityValidator
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
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_MAPPINGS = _FIXTURES / "mappings"
_SOURCE_HASH = "d" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
EMISOR = Rfc("BBB010101BBB")
PPD = "PPD"

_GASTO = "84111506"


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN PPD purchase as *source* facts."""
    base: dict[str, object] = {
        "tipo": "I",
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
        "uuid": "443E4567-E89B-12D3-A456-426614174000",
        "subtotal": "100.00",
        "fecha": "2024-04-15T12:00:00+00:00",
        "metodo_pago": PPD,
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(**overrides: object) -> FiscalDocument:
    result = build_fiscal_document(_raw(**overrides), _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _context(
    document: FiscalDocument | None = None,
    classification: Classification | None = AccountRole.GASTO,
) -> PostingContext:
    document = document if document is not None else _document()
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document,
        perspective=Perspective.RECIBIDO,
        classification=classification,
    )


def _posted(proposal: ProposedJournalEntry | ReviewRequest) -> ProposedJournalEntry:
    assert isinstance(proposal, ProposedJournalEntry), proposal
    return proposal


def _refused(proposal: ProposedJournalEntry | ReviewRequest) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


def _flag_types(review: ReviewRequest) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in review.flags]


def _legs(entry: ProposedJournalEntry) -> list[tuple[AccountRole, LineSide, str]]:
    return [(line.account_role, line.side, str(line.amount.amount)) for line in entry.lines]


# --- the golden entry (§8:182) ----------------------------------------------------------


def test_rule_4_4_posts_the_received_ppd_purchase() -> None:
    """§8:182: `DR Gasto=base · DR IVA Acred. Pendiente=IVA · CR Proveedores=Total`."""
    entry = _posted(rule_4_4(_context()))
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.DEBE, "100.00"),
        (AccountRole.IVA_ACRED_PENDIENTE, LineSide.DEBE, "16.00"),
        (AccountRole.PROVEEDORES, LineSide.HABER, "116.00"),
    ]
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_4
    assert entry.rule_version == RULE_4_4_VERSION
    assert entry.posting_state is PostingState.PROPOSED


def test_the_base_leg_carries_the_classified_role() -> None:
    """§8a:207: the mapping decides the base role for 4.4 exactly as it does for 4.3."""
    entry = _posted(rule_4_4(_context(classification=AccountRole.INVENTARIO)))
    assert _legs(entry)[0] == (AccountRole.INVENTARIO, LineSide.DEBE, "100.00")


def test_a_ppd_purchase_presumes_nothing_paid() -> None:
    """§8:171/§8:182: the payable proves neither payment nor PUE — no assumption, no clearing."""
    entry = _posted(rule_4_4(_context()))
    assert entry.assumptions == ()
    assert AccountingAssumption.ASSUMED_PUE not in entry.assumptions
    assert AccountRole.CLEARING not in {line.account_role for line in entry.lines}


def test_the_line_keys_stay_structural() -> None:
    """§8:194: the leg identities are base/iva/total — the assumption is not in a key."""
    entry = _posted(rule_4_4(_context()))
    assert [line.line_key for line in entry.lines] == ["base", "iva", "total"]


def test_the_entry_carries_the_documents_own_provenance() -> None:
    """§8:169/194 + §8a:209: dated and keyed by the document, never by 'today'."""
    document = _document()
    entry = _posted(rule_4_4(_context(document)))
    assert entry.contributor_rfc == CONTRIBUTOR
    assert entry.source_uuid == document.source_uuid
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == document.fecha


def test_rule_4_4_shares_the_base_and_discount_calculation() -> None:
    """§8:175/191 + §8a:204: the row differs in where the money lands, not in the base."""
    document = _document(
        subtotal="200.00",
        descuento="20.00",
        impuestos=RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "28.80"),), total_traslados="28.80"
        ),
        total="208.80",
    )
    entry = _posted(rule_4_4(_context(document)))
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.DEBE, "180.00"),
        (AccountRole.IVA_ACRED_PENDIENTE, LineSide.DEBE, "28.80"),
        (AccountRole.PROVEEDORES, LineSide.HABER, "208.80"),
    ]


def test_an_exempt_purchase_posts_without_an_iva_leg() -> None:
    """No IVA to book means no IVA leg — not a zero leg, which is not a leg at all."""
    document = _document(
        impuestos=RawImpuestos(traslados=(RawTraslado("002", "Exento", None, None),)),
        total="100.00",
    )
    entry = _posted(rule_4_4(_context(document)))
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.DEBE, "100.00"),
        (AccountRole.PROVEEDORES, LineSide.HABER, "100.00"),
    ]
    assert entry.is_balanced()


# --- the classification and the row guard -----------------------------------------------


def test_a_document_without_a_classification_is_reviewed() -> None:
    """No resolved classification is a missing precondition, never a presumed Gasto (§8a:207)."""
    refusal = _refused(rule_4_4(_context(classification=None)))
    assert _flag_types(refusal) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_a_pue_comprobante_is_not_rule_4_4s() -> None:
    """§8:181: a PUE purchase must never carry 4.4's payable/Pendiente treatment."""
    refusal = _refused(rule_4_4(_context(_document(metodo_pago="PUE"))))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_an_issued_document_is_not_rule_4_4s() -> None:
    """The row is chosen by perspective as well as method: an issued comprobante is 4.2's."""
    context = replace(_context(), perspective=Perspective.EMITIDO)
    refusal = _refused(rule_4_4(context))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_a_total_that_disagrees_with_the_documents_arithmetic_is_reviewed() -> None:
    """The shared preconditions hold for this row too: corrupt source → a reason, never an entry."""
    refusal = _refused(rule_4_4(_context(_document(total="999.00"))))
    assert _flag_types(refusal) == [ReviewFlagType.INVALID_AMOUNT]
    assert refusal.entry is not None and refusal.entry.lines == ()


# --- the whole seam (§12's golden path) -------------------------------------------------


def test_the_received_ppd_purchase_posts_end_to_end() -> None:
    """Source facts → fiscal model → classify (§8a:207) → rule → validator → `POSTED`."""
    document = replace(_document(), status=FiscalDocumentStatus.VIGENTE)
    mapping = YamlMappingProvider(_MAPPINGS).mapping_for(CONTRIBUTOR)
    context = _context(document, classification=mapping.classify(document))
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, rule_4_4(context))
    assert decision.posting_state is PostingState.POSTED
    assert decision.account_for(AccountRole.GASTO) == "601-001"
    assert decision.account_for(AccountRole.IVA_ACRED_PENDIENTE) == "119-001"
    assert decision.account_for(AccountRole.PROVEEDORES) == "201-001"


def test_the_registry_the_validator_is_wired_with_matches_the_rule() -> None:
    """§8:159's \"supported rule\": the id and version the validator checks are this rule's."""
    assert SUPPORTED_RULES[RULE_4_4] == RULE_4_4_VERSION
