"""M3 step 4c: rule 4.1 — an issued PUE income comprobante (§8:179).

§8's first row, and the one that carries an *assumption*: a PUE comprobante is presumed paid in
one exhibition (§8:171), so the cash leg goes to `CLEARING` and the entry says `ASSUMED_PUE` —
an accounting assumption, never payment evidence (§8a:208).

These tests are written the way §12 asks: a golden entry from a real CFDI fixture
(`cfdi_tfd_4_0.xml`, an `I` EMITIDO PUE comprobante with IVA), and mutation tests that corrupt
one source fact at a time — Total, SubTotal, the IVA amount, the currency, the identity — each
of which must produce a *review* (with a reason), never a posting and never a crash. The last
test walks the whole seam: fixture XML → fiscal model → rule → validator → `POSTED` with real
accounts from a mapping file.
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
    RULE_4_1,
    RULE_4_1_VERSION,
    SUPPORTED_RULES,
    propose,
    rule_4_1,
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
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_TFD_FIXTURE = "cfdi_tfd_4_0.xml"
_MAPPINGS = _FIXTURES / "mappings"
_SOURCE_HASH = "a" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
PUE = "PUE"

_parser = SatcfdiFiscalParser()


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN PUE income comprobante as *source* facts, so a test can corrupt one."""
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
        "uuid": "123E4567-E89B-12D3-A456-426614174000",
        "subtotal": "100.00",
        "fecha": "2024-01-15T12:00:00+00:00",
        "metodo_pago": PUE,
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(**overrides: object) -> FiscalDocument:
    result = build_fiscal_document(_raw(**overrides), _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _fixture_document(name: str = _TFD_FIXTURE) -> FiscalDocument:
    raw = _parser.parse((_FIXTURES / name).read_bytes(), _SOURCE_HASH)
    result = build_fiscal_document(raw, _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _context(document: FiscalDocument | None = None) -> PostingContext:
    document = document if document is not None else _fixture_document()
    return PostingContext(
        contributor_rfc=document.emisor_rfc, document=document, perspective=Perspective.EMITIDO
    )


def _legs(proposal: ProposedJournalEntry) -> list[tuple[AccountRole, LineSide, str]]:
    """Every leg as (role, side, amount text) — the entry §8's row says it should be."""
    return [
        (line.account_role, line.side, format(line.amount.amount, "f")) for line in proposal.lines
    ]


def _posted(proposal: ProposedJournalEntry | ReviewRequest) -> ProposedJournalEntry:
    assert isinstance(proposal, ProposedJournalEntry), proposal
    return proposal


def _refused(proposal: ProposedJournalEntry | ReviewRequest) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


# --- the golden entry (§12: one real fixture) ------------------------------------------


def test_rule_4_1_posts_the_issued_pue_comprobante() -> None:
    """§8:179: `DR Clearing=Total · CR Ingresos=base · CR IVA Trasl. Cobrado=IVA`."""
    document = _fixture_document()
    entry = _posted(rule_4_1(_context(document)))
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
        (AccountRole.INGRESOS, LineSide.HABER, "100.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "16.00"),
    ]
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_1
    assert entry.rule_version == RULE_4_1_VERSION
    assert entry.posting_state is PostingState.PROPOSED  # a rule cannot say anything else


def test_the_entry_carries_the_documents_own_provenance() -> None:
    """§8:169/194 + §8a:209: contributor, source, date — a posting is reproducible, not guessed."""
    document = _fixture_document()
    entry = _posted(rule_4_1(_context(document)))
    assert entry.contributor_rfc == document.emisor_rfc
    assert entry.source_uuid == document.source_uuid
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == document.fecha


def test_the_clearing_leg_records_the_pue_assumption() -> None:
    """§8:171/§8a:208: the assumption is explicit evidence on the entry, not a comment."""
    entry = _posted(rule_4_1(_context()))
    assert entry.assumptions == (AccountingAssumption.ASSUMED_PUE,)
    assert AccountingAssumption.ASSUMED_PUE == "assumed_pue"


def test_the_line_keys_stay_structural() -> None:
    """§8:194: `line_key` is the leg's identity, so no assumption text may leak into it."""
    entry = _posted(rule_4_1(_context()))
    assert [line.line_key for line in entry.lines] == ["clearing", "base", "iva"]
    assert not any(AccountingAssumption.ASSUMED_PUE.value in line.line_key for line in entry.lines)


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
    entry = _posted(rule_4_1(_context(document)))
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "104.40"),
        (AccountRole.INGRESOS, LineSide.HABER, "90.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "14.40"),
    ]


def test_an_exempt_comprobante_posts_without_an_iva_leg() -> None:
    """No IVA to book means no IVA leg — not a zero leg, which is not a leg at all."""
    document = _document(
        impuestos=RawImpuestos(traslados=(RawTraslado("002", "Exento", None, None),)),
        total="100.00",
    )
    entry = _posted(rule_4_1(_context(document)))
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "100.00"),
        (AccountRole.INGRESOS, LineSide.HABER, "100.00"),
    ]
    assert entry.is_balanced()


def test_several_iva_traslados_are_summed_into_the_one_leg() -> None:
    """§8:179 names one IVA line: several rates are the same tax, so they aggregate."""
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
    entry = _posted(rule_4_1(_context(document)))
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "118.00"),
        (AccountRole.INGRESOS, LineSide.HABER, "100.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "18.00"),
    ]


# --- mutation tests (§12: one corrupted source fact at a time) ---------------------------


def _flag_types(review: ReviewRequest) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in review.flags]


def test_a_total_that_disagrees_with_the_documents_arithmetic_is_reviewed() -> None:
    """A corrupt Total must not become a plausible entry: `Total == base + IVA` is checked."""
    refusal = _refused(rule_4_1(_context(_document(total="999.00"))))
    assert _flag_types(refusal) == [ReviewFlagType.INVALID_AMOUNT]
    assert refusal.entry is not None
    assert refusal.entry.lines == ()


def test_a_missing_subtotal_is_reviewed() -> None:
    """§8:175: the base comes from SubTotal — absent means the rule cannot compute, not zero."""
    refusal = _refused(rule_4_1(_context(_document(subtotal=None))))
    assert _flag_types(refusal) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_a_corrupt_total_is_reviewed_never_posted() -> None:
    """§12: a non-finite amount is unusable, and it must not reach the entry's arithmetic."""
    refusal = _refused(rule_4_1(_context(_document(total="NaN"))))
    assert _flag_types(refusal) == [ReviewFlagType.INVALID_AMOUNT]


def test_a_traslado_with_a_rate_but_no_importe_is_reviewed() -> None:
    """Half a tax fact is not a tax fact: the IVA leg cannot be computed from it."""
    document = _document(
        impuestos=RawImpuestos(traslados=(RawTraslado("002", "Tasa", "0.16", None),))
    )
    refusal = _refused(rule_4_1(_context(document)))
    assert _flag_types(refusal) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_retenciones_are_never_silently_dropped() -> None:
    """§8:179's entry books no retenciones, and an entry that ignores them is a wrong entry."""
    document = _document(
        impuestos=RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),),
            retenciones=(RawRetencion("002", "0.106667", "10.67"),),
            total_retenciones="10.67",
        )
    )
    refusal = _refused(rule_4_1(_context(document)))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_an_ieps_traslado_is_not_booked_by_4_1() -> None:
    """§8a:210: IEPS has its own role, so 4.1 cannot quietly fold it into the IVA leg."""
    document = _document(
        impuestos=RawImpuestos(
            traslados=(
                RawTraslado("002", "Tasa", "0.16", "16.00"),
                RawTraslado("003", "Tasa", "0.08", "8.00"),
            ),
            total_traslados="24.00",
        ),
        total="124.00",
    )
    refusal = _refused(rule_4_1(_context(document)))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_a_foreign_currency_document_is_reviewed() -> None:
    """§8:193: FX valuation is a policy this engine does not have yet, so it never values one."""
    refusal = _refused(rule_4_1(_context(_document(moneda="EUR"))))
    assert _flag_types(refusal) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_a_ppd_comprobante_is_not_rule_4_1s() -> None:
    """§8:180: a PPD document must never be booked under the PUE presumption (§8:171)."""
    refusal = _refused(rule_4_1(_context(_document(metodo_pago="PPD"))))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_the_method_is_compared_as_sourced() -> None:
    """Nothing is normalized: `MetodoPago` is the word §8:179/180 are chosen by."""
    refusal = _refused(rule_4_1(_context(_document(metodo_pago="pue"))))
    assert _flag_types(refusal) == [ReviewFlagType.UNSUPPORTED_RULE]


def test_a_document_that_cannot_be_keyed_is_flagged_on_the_document() -> None:
    """§8:194 + §8a:209: without a UUID or a Fecha there is no entry to record a review against."""
    without_uuid = _refused(rule_4_1(_context(_document(uuid=None))))
    assert without_uuid.entry is None
    assert _flag_types(without_uuid) == [ReviewFlagType.MISSING_POSTING_IDENTITY]

    without_date = _refused(rule_4_1(_context(_document(fecha=None))))
    assert without_date.entry is None
    assert _flag_types(without_date) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_a_keyed_refusal_carries_the_same_provenance_a_posting_would() -> None:
    """§8:169: a refusal is an auditable fact, so it is dated, identified and versioned too."""
    document = _document(total="999.00")
    refusal = _refused(rule_4_1(_context(document)))
    entry = refusal.entry
    assert entry is not None
    assert entry.rule_id == RULE_4_1
    assert entry.rule_version == RULE_4_1_VERSION
    assert entry.contributor_rfc == document.emisor_rfc
    assert entry.source_uuid == document.source_uuid
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == document.fecha
    assert entry.posting_state is PostingState.PROPOSED


# --- the whole seam (§12's golden path) -------------------------------------------------


def test_the_fixture_document_posts_end_to_end() -> None:
    """Fixture XML → fiscal model → rule → validator → `POSTED`, with the client's accounts.

    §8:159 needs a *resolved* source state, so the document the metadata join would have
    stamped `VIGENTE` is the one that posts (§8:194).
    """
    context = _context(replace(_fixture_document(), status=FiscalDocumentStatus.VIGENTE))
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, propose(context))
    assert decision.posting_state is PostingState.POSTED
    assert decision.account_for(AccountRole.CLEARING) == "102-099"
    assert decision.account_for(AccountRole.INGRESOS) == "401-001"
    assert decision.account_for(AccountRole.IVA_TRASLADADO_COBRADO) == "208-001"
    assert decision.entry is not None
    assert decision.entry.fingerprint(decision.mapping_version).canonical()


def test_the_registry_the_validator_is_wired_with_matches_the_rule() -> None:
    """§8:159's "supported rule": the id and version the validator checks are this rule's."""
    assert SUPPORTED_RULES[RULE_4_1] == RULE_4_1_VERSION
