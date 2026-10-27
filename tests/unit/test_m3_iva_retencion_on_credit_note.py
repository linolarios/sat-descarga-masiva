"""M3: retained IVA/ISR on a credit note never posts — and is never partly posted (§8a:233/234).

A credit note that adjusts IVA withholding attaches to the **original operation's** withholding
treatment, never merely to the presence of a `retenciones` node on the note, and nothing this
engine reads says whether a retention was remitted to the SAT. LIVA Article 7 may therefore
require a *declaration* treatment beyond the note's own entry, and ISR has no ruling at all.
Both are ⚠ decisions: the **whole** decision is `PROPOSED` + a blocking flag, never `POSTED`, and
never base + IVA-transferred legs posted while the retained-IVA effect stays "in review".

What this file pins is the gate and its approved vocabulary:
`RETENCION_IVA_ADJUSTMENT_REQUIRES_REVIEW` (§8a:233) is the flag a supported CFDI `E` carrying
IVA retenido raises, and the routing is scoped to that case — every other retención shape keeps
`amounts.unbookable_tax`'s shared `UNSUPPORTED_RULE` refusal, so 4.1–4.5 and non-`E` documents are
unchanged. The narrow `RETENCION_IVA_YA_ENTERADA` flag is a **deliberate rejection**: no evidence
port can prove a retention was remitted, so the reason records *unknown* remittance status.
"""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from sat_descarga_masiva.contabilidad.journal import PostingState
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.amounts import unbookable_tax
from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    RelatedDocument,
    ReviewRequest,
)
from sat_descarga_masiva.contabilidad.rules.credit_notes import rule_4_6, rule_4_9
from sat_descarga_masiva.contabilidad.rules.posting import SUPPORTED_RULES, propose
from sat_descarga_masiva.contabilidad.validator import PostingDecision, PostingEligibilityValidator
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
    RawRetencion,
    RawTraslado,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_MAPPINGS = _FIXTURES / "mappings"
_SOURCE_HASH = "b" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
COUNTERPARTY = Rfc("BBB010101BBB")
NOTE_UUID = "6e80345d-917f-40bb-a98f-4a73939353c5"
ORIGINAL_UUID = Uuid("9e80345d-917f-40bb-a98f-4a73939353c5")
PUE = "PUE"
PPD = "PPD"
_GASTO = "84111506"

#: A 16% IVA traslado with its two withheld taxes: IVA (002) and ISR (001). The note's own
#: arithmetic states them — Total = base + IVA - retenciones = 100 + 16 - 10.67 - 10.00.
RETENCION_IVA = RawRetencion("002", "0.106667", "10.67")
RETENCION_ISR = RawRetencion("001", "0.100000", "10.00")


def _money(text: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(text))


def _raw(*, issued: bool, **overrides: object) -> RawCfd:
    """A coherent MXN `E` note carrying IVA withholding, as *source* facts."""
    base: dict[str, object] = {
        "tipo": "E",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": CONTRIBUTOR.value if issued else COUNTERPARTY.value,
        "receptor_rfc": COUNTERPARTY.value if issued else CONTRIBUTOR.value,
        "conceptos": (RawConcepto(_GASTO, "1", "100.00", "100.00", None),),
        "impuestos": RawImpuestos(
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),),
            retenciones=(RETENCION_IVA, RETENCION_ISR),
            total_traslados="16.00",
            total_retenciones="20.67",
        ),
        "total": "95.33",
        "uuid": NOTE_UUID,
        "subtotal": "100.00",
        "fecha": "2026-05-15T12:00:00+00:00",
        "metodo_pago": None,
        "cfdi_relacionados": (RawCfdiRelacionados("01", (ORIGINAL_UUID.value,)),),
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(*, issued: bool) -> FiscalDocument:
    result = build_fiscal_document(_raw(issued=issued), _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _original(metodo_pago: str) -> RelatedDocument:
    return RelatedDocument(
        uuid=ORIGINAL_UUID,
        tipo_relacion="01",
        metodo_pago=metodo_pago,
        total=_money("116.00"),
        posted=True,
        moneda="MXN",
    )


def _context(*, perspective: Perspective, metodo_pago: str) -> PostingContext:
    """The note plus the original it names — the facts a rule is allowed to read."""
    issued = perspective is Perspective.EMITIDO
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=_document(issued=issued),
        perspective=perspective,
        classification=None if issued else AccountRole.GASTO,
        related_documents=(_original(metodo_pago),),
    )


def _decide(context: PostingContext) -> PostingDecision:
    """Source facts → rule → validator, with the source resolved as vigente (§8:159)."""
    document = replace(context.document, status=FiscalDocumentStatus.VIGENTE)
    context = replace(context, document=document)
    return PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, propose(context))


def _flag_types(decision: PostingDecision) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in decision.review_flags]


def _refused(proposal: object) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


# --- the gate: one decision, never posted -------------------------------------------------


def test_a_received_note_affecting_iva_withholding_never_posts() -> None:
    """§8a:233: retained-IVA review is the approved flag, and the whole decision is blocked."""
    decision = _decide(_context(perspective=Perspective.RECIBIDO, metodo_pago=PPD))
    assert decision.posting_state is PostingState.PROPOSED
    assert decision.posting_state is not PostingState.POSTED
    assert _flag_types(decision) == [ReviewFlagType.RETENCION_IVA_ADJUSTMENT_REQUIRES_REVIEW]


def test_the_retention_effect_is_not_dropped_to_post_the_rest() -> None:
    """§8a:233: no base/IVA mirror may post while the retention effect is "in review"."""
    decision = _decide(_context(perspective=Perspective.RECIBIDO, metodo_pago=PPD))
    entry = decision.entry
    assert entry is not None
    # Today the shared precondition refuses before any leg is built, so there is no entry whose
    # lines are 4.9's mirror with the retention absent — and nothing is posted.
    assert entry.lines == ()
    assert decision.accounts == {}
    assert not [line for line in entry.lines if "RETENCION" in line.account_role.name]


def test_the_same_gate_applies_to_an_issued_note() -> None:
    """§8a:233: the gate is not specific to one side — 4.6's shape is equally review-gated."""
    decision = _decide(_context(perspective=Perspective.EMITIDO, metodo_pago=PUE))
    assert decision.posting_state is PostingState.PROPOSED
    assert _flag_types(decision) == [ReviewFlagType.RETENCION_IVA_ADJUSTMENT_REQUIRES_REVIEW]
    entry = decision.entry
    assert entry is not None and entry.lines == ()


def test_the_rule_itself_refuses_before_proposing_legs() -> None:
    """§8:173: the refusal is the rule's, so neither side proposes a partial mirror."""
    received = _refused(rule_4_9(_context(perspective=Perspective.RECIBIDO, metodo_pago=PPD)))
    issued = _refused(rule_4_6(_context(perspective=Perspective.EMITIDO, metodo_pago=PUE)))
    for review in (received, issued):
        assert [flag.flag_type for flag in review.flags] == [
            ReviewFlagType.RETENCION_IVA_ADJUSTMENT_REQUIRES_REVIEW
        ]
        entry = review.entry
        assert entry is not None and entry.lines == ()
        assert entry.posting_state is PostingState.PROPOSED


def test_the_flag_is_scoped_to_a_credit_note_that_retenes_iva() -> None:
    """§8a:233: only the `E` + IVA-retenido condition gets the new word — nothing else moves."""
    flag = unbookable_tax(_document(issued=False))
    assert flag is not None
    assert flag.flag_type is ReviewFlagType.RETENCION_IVA_ADJUSTMENT_REQUIRES_REVIEW
    assert "IVA retenido" in flag.reason
    # A withholding on any other document type keeps the shared refusal (§8:179–182), so
    # 4.1–4.5 and non-`E` documents are untouched by this vocabulary.
    income = build_fiscal_document(_raw(issued=False, tipo="I"), _SOURCE_HASH).document
    assert income is not None
    assert unbookable_tax(income).flag_type is ReviewFlagType.UNSUPPORTED_RULE
    # ISR alone is not the retained-IVA condition: no ruling exists, so no new word.
    isr_only = RawImpuestos(
        traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),),
        retenciones=(RETENCION_ISR,),
        total_traslados="16.00",
        total_retenciones="10.00",
    )
    isr_note = build_fiscal_document(_raw(issued=False, impuestos=isr_only), _SOURCE_HASH).document
    assert isr_note is not None
    assert unbookable_tax(isr_note).flag_type is ReviewFlagType.UNSUPPORTED_RULE


# --- ISR: no ruling, so no assertion ------------------------------------------------------


def test_iva_and_isr_withholding_asserts_no_isr_reversal_or_proration() -> None:
    """§8a:234: ISR is not ratified by the retained-IVA analysis, so nothing claims it."""
    decision = _decide(_context(perspective=Perspective.RECIBIDO, metodo_pago=PPD))
    entry = decision.entry
    assert entry is not None
    assert entry.assumptions == ()
    assert not [line for line in entry.lines if "RETENCION" in line.account_role.name]
    assert not [flag for flag in decision.review_flags if "isr" in flag.flag_type.value]
    assert not [flag for flag in decision.review_flags if "prorat" in flag.flag_type.value]
    assert decision.posting_state is PostingState.PROPOSED


def test_the_reason_records_an_unknown_remittance_status() -> None:
    """§8a:233: no evidence port exists, so the reason must say *unknown* — never either fact."""
    decision = _decide(_context(perspective=Perspective.RECIBIDO, metodo_pago=PPD))
    reasons = " ".join(flag.reason.lower() for flag in decision.review_flags)
    assert "remittance status" in reasons
    assert "unknown" in reasons
    # Neither status may be asserted: nothing this engine reads proves either.
    assert "was remitted" not in reasons
    assert "not remitted" not in reasons
    assert "already remitted" not in reasons


def test_the_retained_iva_vocabulary_is_approved_and_the_narrow_one_rejected() -> None:
    """§8a:233: the review flag is approved; "the retention was remitted" stays unrepresentable."""
    assert ReviewFlagType.RETENCION_IVA_ADJUSTMENT_REQUIRES_REVIEW.value == (
        "retencion_iva_adjustment_requires_review"
    )
    assert "retencion_iva_ya_enterada" not in {flag.value for flag in ReviewFlagType}
    assert not hasattr(ReviewFlagType, "RETENCION_IVA_YA_ENTERADA")
