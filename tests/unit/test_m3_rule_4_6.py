"""M3 step E2: rule 4.6 — a credit note over a PUE original, issued (§8a:212).

A credit note over a PUE original reverses what §8:179 booked (4.1): the revenue and the IVA
that was presumed collected come back as debits, and the cash leaves `CLEARING`. The mirror
is read off the original, which the note names but does not contain: the row is selected by
the **original's** `MetodoPago`, carried on `PostingContext.related_documents`.

What this slice cannot prove is that the money actually went back, so every entry carries
`EGRESO_IVA_EVIDENCE_MISSING` and stays `PROPOSED`. The legs are complete and balanced:
the flag is about evidence, not about arithmetic.
"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sat_descarga_masiva.contabilidad.journal import LineSide, PostingState
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    RelatedDocument,
    ReviewRequest,
)
from sat_descarga_masiva.contabilidad.rules.credit_notes import (
    RULE_4_6,
    RULE_4_6_VERSION,
    rule_4_6,
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
_SOURCE_HASH = "a" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
NOTE_UUID = "7e80345d-917f-40bb-a98f-4a73939353c5"
ORIGINAL_UUID = Uuid("8e80345d-917f-40bb-a98f-4a73939353c5")
PUE = "PUE"
PPD = "PPD"


def _money(text: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(text))


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN `E` note naming one original, as *source* facts."""
    base: dict[str, object] = {
        "tipo": "E",
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
        "uuid": NOTE_UUID,
        "subtotal": "100.00",
        "fecha": "2026-01-15T12:00:00+00:00",
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
    *,
    original: RelatedDocument | None = None,
    perspective: Perspective = Perspective.EMITIDO,
) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document if document is not None else _document(),
        perspective=perspective,
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


def test_rule_4_6_reverses_the_issued_pue_entry() -> None:
    """§8a:212: `DR Ingresos=base · DR IVA Cobrado=IVA · CR Clearing=Total` — 4.1 mirrored."""
    review = _refused(rule_4_6(_context()))
    assert _legs(review) == [
        (AccountRole.CLEARING, LineSide.HABER, "116.00"),
        (AccountRole.INGRESOS, LineSide.DEBE, "100.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.DEBE, "16.00"),
    ]
    entry = review.entry
    assert entry is not None
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_6
    assert entry.rule_version == RULE_4_6_VERSION
    assert entry.posting_state is PostingState.PROPOSED


def test_the_entry_names_the_evidence_it_cannot_observe() -> None:
    """§8a:212: the reversal is computed, but the refund itself is not observable yet."""
    review = _refused(rule_4_6(_context()))
    assert _flag_types(review) == [ReviewFlagType.EGRESO_IVA_EVIDENCE_MISSING]


def test_the_line_keys_stay_structural() -> None:
    """§8:194: the legs are identified by clearing/base/iva, never by the evidence flag."""
    review = _refused(rule_4_6(_context()))
    entry = review.entry
    assert entry is not None
    assert [line.line_key for line in entry.lines] == ["clearing", "base", "iva"]


def test_an_exempt_note_books_no_iva_leg() -> None:
    """No IVA to reverse means no IVA leg — not a zero leg, which is not a leg at all."""
    document = _document(
        impuestos=RawImpuestos(traslados=(RawTraslado("002", "Exento", None, None),)),
        total="100.00",
    )
    review = _refused(rule_4_6(_context(document)))
    assert _legs(review) == [
        (AccountRole.CLEARING, LineSide.HABER, "100.00"),
        (AccountRole.INGRESOS, LineSide.DEBE, "100.00"),
    ]


def test_the_entry_carries_the_notes_own_provenance() -> None:
    """§8:169/194 + §8a:209: the entry is keyed and dated by the note, never by the original."""
    document = _document()
    review = _refused(rule_4_6(_context(document)))
    entry = review.entry
    assert entry is not None
    assert entry.contributor_rfc == CONTRIBUTOR
    assert entry.source_uuid == document.source_uuid
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == document.fecha


# --- the original is the selector ---------------------------------------------------------


def test_the_notes_own_metodo_pago_is_never_read() -> None:
    """§8a:212: a note is not paid — a PPD header on the note does not make it 4.7's."""
    review = _refused(rule_4_6(_context(_document(metodo_pago=PPD))))
    assert _legs(review)[0] == (AccountRole.CLEARING, LineSide.HABER, "116.00")


def test_a_ppd_original_is_not_rule_4_6s() -> None:
    """§8a:212: the family is chosen by the original's method, so PPD is 4.7's row."""
    review = _refused(rule_4_6(_context(original=_original(metodo_pago=PPD))))
    assert _flag_types(review) == [ReviewFlagType.UNSUPPORTED_RULE]
    entry = review.entry
    assert entry is not None and entry.lines == ()


def test_a_received_note_is_not_rule_4_6s() -> None:
    """The row is chosen by side as well: a received note over a PUE original is 4.8's."""
    review = _refused(rule_4_6(_context(perspective=Perspective.RECIBIDO)))
    assert _flag_types(review) == [ReviewFlagType.UNSUPPORTED_RULE]


# --- the evidence gates -------------------------------------------------------------------


def test_an_unposted_original_is_recorded_and_refused() -> None:
    """§8:173: a note cannot reverse a receivable the ledger never held."""
    review = _refused(rule_4_6(_context(original=_original(posted=False))))
    assert _flag_types(review) == [ReviewFlagType.EGRESO_ORIGINAL_NOT_POSTED]
    entry = review.entry
    assert entry is not None and entry.lines == ()


def test_a_note_that_exceeds_its_original_is_refused() -> None:
    """§8a:212: the original's Total is the single-note cap; a larger reversal overstates it."""
    review = _refused(rule_4_6(_context(original=_original(total=_money("115.00")))))
    assert _flag_types(review) == [ReviewFlagType.EGRESO_EXCEEDS_ORIGINAL]
    entry = review.entry
    assert entry is not None and entry.lines == ()


def test_a_note_equal_to_its_original_is_allowed() -> None:
    """The cap is inclusive: a full credit note reverses exactly the original's Total."""
    review = _refused(rule_4_6(_context(original=_original(total=_money("116.00")))))
    assert _flag_types(review) == [ReviewFlagType.EGRESO_IVA_EVIDENCE_MISSING]


def test_an_original_without_a_total_is_reviewed() -> None:
    """No cap is not a zero cap: the reversal cannot be proven to correct anything."""
    review = _refused(rule_4_6(_context(original=_original(total=None))))
    assert _flag_types(review) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    entry = review.entry
    assert entry is not None and entry.lines == ()


def test_a_note_in_another_currency_is_not_valued() -> None:
    """§8:193: no rate chain valued this note, so a reversal in USD would be a conversion."""
    document = replace(_document(), moneda="USD")
    review = _refused(rule_4_6(_context(document)))
    assert _flag_types(review) == [ReviewFlagType.AMBIGUOUS_FX]


def test_an_original_valued_differently_is_not_netted() -> None:
    """§8:193: two currencies cannot be netted, even when the note itself is in MXN."""
    review = _refused(rule_4_6(_context(original=_original(moneda="USD"))))
    assert _flag_types(review) == [ReviewFlagType.AMBIGUOUS_FX]


# --- the whole seam (§12's golden path) ---------------------------------------------------


def test_the_registry_contains_rule_4_6_at_its_version() -> None:
    """§8:159's supported rule: the validator is wired with this row's id and version."""
    assert SUPPORTED_RULES[RULE_4_6] == RULE_4_6_VERSION


def test_the_selector_finds_rule_4_6_for_a_credit_note() -> None:
    """§8:161: an `E` note is claimed by the credit-note rows, never left unowned."""
    review = _refused(propose(_context()))
    entry = review.entry
    assert entry is not None
    assert entry.rule_id == RULE_4_6


def test_the_reversal_reaches_the_validator_and_stays_proposed() -> None:
    """Source facts → rule → validator: the legs resolve, the evidence keeps it `PROPOSED`."""
    document = replace(_document(), status=FiscalDocumentStatus.VIGENTE)
    context = _context(document)
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
        AccountRole.INGRESOS,
        AccountRole.IVA_TRASLADADO_COBRADO,
    }
    # The validator resolves accounts only for an entry it would post; a blocked review keeps
    # the deterministic legs and their roles, and resolves nothing it cannot post (§8:159).
    assert decision.accounts == {}
