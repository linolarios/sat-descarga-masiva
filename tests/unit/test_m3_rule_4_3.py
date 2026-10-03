"""M3 step 4e: rule 4.3 — a received PUE purchase comprobante (§8:181).

A *received* `I` PUE comprobante is a purchase in the contributor's books, and §8's row reads
`DR Gasto/Inv=base · DR IVA Acred. Pagado=IVA · CR Clearing=Total`, with the same `ASSUMED_PUE`
assumption 4.1 carries (§8:171).

Two things make this rule different from every earlier one:

- the **base role is classified**, not fixed: which of `Gasto`/`Inventario` the debit lands on
  comes from the client's ``ClaveProdServ → AccountingCategory → AccountRole`` mapping (§8a:207),
  carried on the `PostingContext`. These tests drive that through a document whose concepts
  classify to one role, and through every way it can fail to (no classification, an unclassified
  product, capital goods, mixed concepts) — each of which is a *review*, never a guessed Gasto;
- the legs are the **mirror** of 4.1: the base and the creditable IVA are debits and the money is
  the credit, and the total still equals `base + IVA` because §8:159 checks the balance.
"""

from dataclasses import replace
from pathlib import Path

from sat_descarga_masiva.contabilidad.classification import (
    Classification,
    ClassificationRefusal,
    ClassificationRefusalKind,
)
from sat_descarga_masiva.contabilidad.journal import (
    AccountingAssumption,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest
from sat_descarga_masiva.contabilidad.rules.posting import (
    RULE_4_3,
    RULE_4_3_VERSION,
    SUPPORTED_RULES,
    rule_4_3,
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
    RawRetencion,
    RawTraslado,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_MAPPINGS = _FIXTURES / "mappings"
_SOURCE_HASH = "c" * 64
#: The contributor is the *receptor* here: a received comprobante is a purchase in their books.
CONTRIBUTOR = Rfc("AAA010101AAA")
EMISOR = Rfc("BBB010101BBB")
PUE = "PUE"

#: ClaveProdServ codes the fixture mapping classifies (§8a:207).
_GASTO = "84111506"
_INVENTARIO = "53131600"
_ACTIVO_FIJO = "43211500"


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN PUE purchase as *source* facts, so a test can corrupt one."""
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
        "uuid": "333E4567-E89B-12D3-A456-426614174000",
        "subtotal": "100.00",
        "fecha": "2024-03-15T12:00:00+00:00",
        "metodo_pago": PUE,
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


# --- the golden entry (§8:181) ----------------------------------------------------------


def test_rule_4_3_posts_the_received_pue_purchase() -> None:
    """§8:181: `DR Gasto=base · DR IVA Acred. Pagado=IVA · CR Clearing=Total`."""
    entry = _posted(rule_4_3(_context()))
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.DEBE, "100.00"),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.DEBE, "16.00"),
        (AccountRole.CLEARING, LineSide.HABER, "116.00"),
    ]
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_3
    assert entry.rule_version == RULE_4_3_VERSION
    assert entry.posting_state is PostingState.PROPOSED  # a rule cannot say anything else


def test_the_base_leg_carries_the_classified_role() -> None:
    """§8a:207: the classification, not the row, decides which of Gasto/Inventario debits."""
    entry = _posted(rule_4_3(_context(classification=AccountRole.INVENTARIO)))
    assert _legs(entry)[0] == (AccountRole.INVENTARIO, LineSide.DEBE, "100.00")


def test_the_entry_carries_the_pue_assumption() -> None:
    """§8:171/§8a:208: 4.3 presumes PUE exactly as 4.1 does, and says so on the entry."""
    entry = _posted(rule_4_3(_context()))
    assert entry.assumptions == (AccountingAssumption.ASSUMED_PUE,)


def test_the_line_keys_stay_structural() -> None:
    """§8:194: the money/base/IVA leg identities do not carry the assumption or the role."""
    entry = _posted(rule_4_3(_context()))
    assert [line.line_key for line in entry.lines] == ["base", "iva", "clearing"]


def test_the_entry_carries_the_documents_own_provenance() -> None:
    """§8:169/194 + §8a:209: a received posting is keyed by the *contributor's* books."""
    document = _document()
    entry = _posted(rule_4_3(_context(document)))
    assert entry.contributor_rfc == CONTRIBUTOR
    assert entry.source_uuid == document.source_uuid
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == document.fecha


def test_the_base_is_subtotal_minus_descuento() -> None:
    """§8:175/191 + §8a:204: `base = SubTotal − Descuento`, the M3 `net` discount policy."""
    document = _document(
        subtotal="100.00",
        descuento="10.00",
        impuestos=RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "14.40"),), total_traslados="14.40"
        ),
        total="104.40",
    )
    entry = _posted(rule_4_3(_context(document)))
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.DEBE, "90.00"),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.DEBE, "14.40"),
        (AccountRole.CLEARING, LineSide.HABER, "104.40"),
    ]


def test_an_exempt_purchase_posts_without_an_iva_leg() -> None:
    """No IVA to book means no IVA leg — not a zero leg, which is not a leg at all."""
    document = _document(
        impuestos=RawImpuestos(traslados=(RawTraslado("002", "Exento", None, None),)),
        total="100.00",
    )
    entry = _posted(rule_4_3(_context(document)))
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.DEBE, "100.00"),
        (AccountRole.CLEARING, LineSide.HABER, "100.00"),
    ]
    assert entry.is_balanced()


def test_several_iva_traslados_are_summed_into_the_one_leg() -> None:
    """§8:181 names one IVA line: several rates are the same tax, so they aggregate."""
    document = _document(
        impuestos=RawImpuestos(
            traslados=(
                RawTraslado("002", "Tasa", "0.16", "16.00"),
                RawTraslado("002", "Tasa", "0.08", "2.00"),
            ),
            total_traslados="18.00",
        ),
        total="118.00",
    )
    entry = _posted(rule_4_3(_context(document)))
    assert _legs(entry) == [
        (AccountRole.GASTO, LineSide.DEBE, "100.00"),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.DEBE, "18.00"),
        (AccountRole.CLEARING, LineSide.HABER, "118.00"),
    ]


# --- the classification precondition (§8a:196/207) --------------------------------------


def test_a_document_without_a_classification_is_reviewed() -> None:
    """No resolved classification is a missing precondition, never a presumed Gasto (§8a:207)."""
    refusal = _refused(rule_4_3(_context(classification=None)))
    assert _flag_types(refusal) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    assert refusal.entry is not None and refusal.entry.lines == ()


def test_an_unclassified_product_is_reviewed_never_defaulted() -> None:
    """A ClaveProdServ the mapping does not name is `UNMAPPED_ACCOUNT`, not a Gasto guess."""
    refusal = _refused(
        rule_4_3(
            _context(
                classification=ClassificationRefusal(
                    ClassificationRefusalKind.UNCLASSIFIED_PRODUCT,
                    "ClaveProdServ '99999999' is not classified by the client's mapping",
                )
            )
        )
    )
    assert _flag_types(refusal) == [ReviewFlagType.UNMAPPED_ACCOUNT]


def test_capital_goods_are_reviewed_not_posted() -> None:
    """§8a:196: a fixed asset is a review — 4.3 must never post it as Gasto or Inventario."""
    refusal = _refused(
        rule_4_3(
            _context(
                classification=ClassificationRefusal(
                    ClassificationRefusalKind.CAPITAL_GOODS,
                    "ClaveProdServ '43211500' is classified activo_fijo",
                )
            )
        )
    )
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_a_mixed_purchase_is_reviewed_whole() -> None:
    """§8:173: concepts that disagree are reviewed — never first-wins, never summed."""
    refusal = _refused(
        rule_4_3(
            _context(
                classification=ClassificationRefusal(
                    ClassificationRefusalKind.MIXED_CATEGORIES,
                    "the concepts classify to more than one role (gasto, inventario)",
                )
            )
        )
    )
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_the_classification_comes_from_the_mappings_product_table() -> None:
    """§8a:207: the client's ClaveProdServ table — not the rule — decides the base role."""
    document = _document(conceptos=(RawConcepto(_INVENTARIO, "1", "100.00", "100.00", None),))
    mapping = YamlMappingProvider(_MAPPINGS).mapping_for(CONTRIBUTOR)
    entry = _posted(rule_4_3(_context(document, classification=mapping.classify(document))))
    assert _legs(entry)[0] == (AccountRole.INVENTARIO, LineSide.DEBE, "100.00")


def test_a_capital_good_classified_by_the_mapping_is_reviewed() -> None:
    """§8a:196: the mapping's `activo_fijo` products are reviewed, never posted as Gasto."""
    document = _document(conceptos=(RawConcepto(_ACTIVO_FIJO, "1", "100.00", "100.00", None),))
    mapping = YamlMappingProvider(_MAPPINGS).mapping_for(CONTRIBUTOR)
    refusal = _refused(rule_4_3(_context(document, classification=mapping.classify(document))))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


# --- mutations, and the rows do not overlap ---------------------------------------------


def test_a_total_that_disagrees_with_the_documents_arithmetic_is_reviewed() -> None:
    """The shared preconditions hold here too: a corrupt source is a reason, never an entry."""
    refusal = _refused(rule_4_3(_context(_document(total="999.00"))))
    assert _flag_types(refusal) == [ReviewFlagType.INVALID_AMOUNT]


def test_retenciones_are_never_silently_dropped() -> None:
    """§8:181's entry books no retenciones, and an entry that ignores them is a wrong entry."""
    document = _document(
        impuestos=RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),),
            retenciones=(RawRetencion("002", "0.106667", "10.67"),),
            total_traslados="16.00",
            total_retenciones="10.67",
        )
    )
    refusal = _refused(rule_4_3(_context(document)))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_a_ppd_comprobante_is_not_rule_4_3s() -> None:
    """§8:182: a PPD purchase must never be booked under the PUE presumption (§8:171)."""
    refusal = _refused(rule_4_3(_context(_document(metodo_pago="PPD"))))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_an_issued_document_is_not_rule_4_3s() -> None:
    """The row is chosen by perspective as well as method: an issued comprobante is 4.1's."""
    context = replace(_context(), perspective=Perspective.EMITIDO)
    refusal = _refused(rule_4_3(context))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


# --- the whole seam (§12's golden path) -------------------------------------------------


def test_the_received_purchase_posts_end_to_end() -> None:
    """Source facts → fiscal model → classify (§8a:207) → rule → validator → `POSTED`."""
    document = replace(_document(), status=FiscalDocumentStatus.VIGENTE)
    mapping = YamlMappingProvider(_MAPPINGS).mapping_for(CONTRIBUTOR)
    context = _context(document, classification=mapping.classify(document))
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, rule_4_3(context))
    assert decision.posting_state is PostingState.POSTED
    assert decision.account_for(AccountRole.GASTO) == "601-001"
    assert decision.account_for(AccountRole.IVA_ACRED_PAGADO) == "118-001"
    assert decision.account_for(AccountRole.CLEARING) == "102-099"


def test_the_registry_the_validator_is_wired_with_matches_the_rule() -> None:
    """§8:159's \"supported rule\": the id and version the validator checks are this rule's."""
    assert SUPPORTED_RULES[RULE_4_3] == RULE_4_3_VERSION
