"""M3 step 4d: rule 4.2 — an issued PPD income comprobante (§8:180).

§8's second row, and the mirror of 4.1 with one difference that matters: nothing is presumed
paid. The receivable lands on `CLIENTES` (not the clearing account), the IVA is the *not yet
collected* kind (`IVA_TRASLADADO_NO_COBRADO`), and the entry carries **no** `ASSUMED_PUE`
(§8:171/§8a:208) — a PPD comprobante is not payment evidence at all.

The calculation is 4.1's, so these tests focus where 4.2 differs: which account the money lands
on, which IVA role it books, that the PUE row cannot claim a PPD document (and vice versa), and
one golden end-to-end posting through the mapping file.
"""

from dataclasses import replace
from pathlib import Path

from sat_descarga_masiva.contabilidad.journal import (
    AccountingAssumption,
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import PostingContext, ReviewRequest
from sat_descarga_masiva.contabilidad.rules.posting import (
    RULE_4_2,
    RULE_4_2_VERSION,
    SUPPORTED_RULES,
    rule_4_1,
    rule_4_2,
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
_SOURCE_HASH = "b" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
PPD = "PPD"


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN PPD income comprobante as *source* facts."""
    base: dict[str, object] = {
        "tipo": "I",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": CONTRIBUTOR.value,
        "receptor_rfc": RECEPTOR.value,
        "conceptos": (RawConcepto("01010101", "1", "100.00", "100.00", None),),
        "impuestos": RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),), total_traslados="16.00"
        ),
        "total": "116.00",
        "uuid": "223E4567-E89B-12D3-A456-426614174000",
        "subtotal": "100.00",
        "fecha": "2024-02-15T12:00:00+00:00",
        "metodo_pago": PPD,
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(**overrides: object) -> FiscalDocument:
    result = build_fiscal_document(_raw(**overrides), _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _context(document: FiscalDocument | None = None) -> PostingContext:
    document = document if document is not None else _document()
    return PostingContext(
        contributor_rfc=document.emisor_rfc, document=document, perspective=Perspective.EMITIDO
    )


def _legs(proposal: ProposedJournalEntry) -> list[tuple[AccountRole, LineSide, str]]:
    return [
        (line.account_role, line.side, format(line.amount.amount, "f")) for line in proposal.lines
    ]


def _posted(proposal: ProposedJournalEntry | ReviewRequest) -> ProposedJournalEntry:
    assert isinstance(proposal, ProposedJournalEntry), proposal
    return proposal


def _refused(proposal: ProposedJournalEntry | ReviewRequest) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


def _flag_types(review: ReviewRequest) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in review.flags]


# --- the golden entry -------------------------------------------------------------------


def test_rule_4_2_posts_the_issued_ppd_comprobante() -> None:
    """§8:180: `DR Clientes=Total · CR Ingresos=base · CR IVA Trasl. No Cobrado=IVA`."""
    document = _document()
    entry = _posted(rule_4_2(_context(document)))
    assert _legs(entry) == [
        (AccountRole.CLIENTES, LineSide.DEBE, "116.00"),
        (AccountRole.INGRESOS, LineSide.HABER, "100.00"),
        (AccountRole.IVA_TRASLADADO_NO_COBRADO, LineSide.HABER, "16.00"),
    ]
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_2
    assert entry.rule_version == RULE_4_2_VERSION
    assert entry.posting_state is PostingState.PROPOSED


def test_a_ppd_entry_presumes_no_payment() -> None:
    """§8:171/§8a:208: `ASSUMED_PUE` belongs to a PUE comprobante — a PPD one assumes nothing."""
    entry = _posted(rule_4_2(_context()))
    assert entry.assumptions == ()
    assert AccountingAssumption.ASSUMED_PUE not in entry.assumptions
    assert not any(AccountingAssumption.ASSUMED_PUE.value in line.line_key for line in entry.lines)
    assert AccountRole.CLEARING not in {line.account_role for line in entry.lines}


def test_rule_4_2_shares_the_base_calculation() -> None:
    """§8:175/191 + §8a:204: the two rows differ in where the money lands, not in the base."""
    document = _document(
        subtotal="200.00",
        descuento="20.00",
        impuestos=RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "28.80"),), total_traslados="28.80"
        ),
        total="208.80",
    )
    entry = _posted(rule_4_2(_context(document)))
    assert _legs(entry) == [
        (AccountRole.CLIENTES, LineSide.DEBE, "208.80"),
        (AccountRole.INGRESOS, LineSide.HABER, "180.00"),
        (AccountRole.IVA_TRASLADADO_NO_COBRADO, LineSide.HABER, "28.80"),
    ]


def test_the_entry_carries_the_documents_own_provenance() -> None:
    """§8:169/194 + §8a:209: contributor, source, date — a posting is reproducible, not guessed."""
    document = _document()
    entry = _posted(rule_4_2(_context(document)))
    assert entry.contributor_rfc == document.emisor_rfc
    assert entry.source_uuid == document.source_uuid
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == document.fecha


# --- the rows do not overlap ------------------------------------------------------------


def test_a_pue_comprobante_is_not_rule_4_2s() -> None:
    """The other direction of 4.1's guard: neither row may claim the other's method (§8:179/180)."""
    refusal = _refused(rule_4_2(_context(_document(metodo_pago="PUE"))))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_rule_4_1_does_not_book_a_ppd_comprobante() -> None:
    """The mirror: a PPD document must never carry 4.1's clearing/PUE assumption."""
    refusal = _refused(rule_4_1(_context()))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


# --- a mutation, and the whole seam -----------------------------------------------------


def test_a_total_that_disagrees_with_the_documents_arithmetic_is_reviewed() -> None:
    """The shared preconditions hold for this row too: corrupt source → a reason, never an entry."""
    refusal = _refused(rule_4_2(_context(_document(total="999.00"))))
    assert _flag_types(refusal) == [ReviewFlagType.INVALID_AMOUNT]
    assert refusal.entry is not None and refusal.entry.lines == ()


def test_the_ppd_document_posts_end_to_end() -> None:
    """Fixture-free but full: source facts → fiscal model → rule → validator → `POSTED`.

    §8:159 needs a *resolved* source state, so the document is stamped `VIGENTE` as the
    metadata join would (§8:194).
    """
    context = _context(replace(_document(), status=FiscalDocumentStatus.VIGENTE))
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, rule_4_2(context))
    assert decision.posting_state is PostingState.POSTED
    assert decision.account_for(AccountRole.CLIENTES) == "105-001"
    assert decision.account_for(AccountRole.INGRESOS) == "401-001"
    assert decision.account_for(AccountRole.IVA_TRASLADADO_NO_COBRADO) == "209-001"
    assert decision.entry is not None
    assert decision.entry.fingerprint(decision.mapping_version).canonical()
