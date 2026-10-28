"""M3/T4b — the §12 mutation matrix, and the one relation axis no module pinned (§12).

§12 asks that corrupting a source fact never post silently: Total, SubTotal, IVA, TipoCambio,
UUID, ImpPagado, and metadata status, cancellation date, `folio_sustitución`, currency, tax
factor, tax rate and payment relation. That is thirteen axes. Recon says ten are already
pinned and an eleventh only half — the per-rule modules are written exactly this way, "a
coherent MXN PUE income comprobante as *source* facts, so a test can corrupt one"
(`test_m3_rule_4_1.py:63`). A second
copy of a covered axis would prove nothing, so this module adds **no** duplicate: it records
where each axis already lives, and pins only the relation axis no module exercises.

- **Total** — COVERED — `test_m3_rule_4_1.py:224`, and the same shape in 4.2/4.3/4.4.
- **SubTotal** — COVERED — `test_m3_rule_4_1.py:232` (absent ⇒ reviewed, never zero).
- **IVA** — COVERED — `test_m3_rule_4_1.py:244` (rate with no `Importe`), `:266` (IEPS not
  folded in), `:197` (two traslados summed); `test_m3_rule_4_5.py:427/456/483/527`.
- **TipoCambio** — COVERED — `test_m3_rule_4_1.py:282`; the 4.6–4.9
  `..._note_in_another_currency_is_not_valued`; `test_m3_valuation.py:297` (unusable rates,
  parametrized); `test_m3_rule_4_5.py:794` (a payment in another currency).
- **UUID** — COVERED — `test_m3_rule_4_1.py:300` (a document that cannot be keyed),
  `test_m3_rule_4_5.py:715` (the same partialidad twice), `test_m3_rule_4_15.py:518`.
- **ImpPagado** — COVERED — `test_m3_rule_4_5.py:740/750` (unreadable, absent), `:709`
  (`Σ ImpPagado` against `Pago.Monto`), `:699` (the saldo equation).
- **metadata status** — COVERED — `test_m3_metadata_snapshot.py:74`,
  `test_m3_fiscal_state_projection.py:56/83/105`, and the accounting use case's a31/a37.
- **cancellation date** — COVERED — `test_m3_fiscal_state_projection.py:124` (no date ⇒
  refused, never dated by retrieval), `test_m3_rule_4_15.py:271` and `:466`.
- **`folio_sustitución` (`TipoRelacion`)** — PARTIAL → closed here: the projection records the
  substitution *evidence* (`test_m3_fiscal_state_projection.py:133`), but no module asserted
  what a `04`-only note does to the accounting rows, which is what this module adds.
- **currency** — COVERED — `test_m3_rule_4_1.py:282`, `test_m3_rule_4_5.py:794/827`,
  `test_m3_valuation.py:192` (an MXN posting is not valued).
- **tax factor (`TipoFactor`)** — BLOCKED BY CONFIRMED DEFECT, D1 below.
- **tax rate (`TasaOCuota`)** — BLOCKED BY CONFIRMED DEFECT, D2 below.
- **payment relation** — COVERED — `test_m3_rule_4_5.py:709/699/731/715` (the stated `Monto`,
  the saldo equation, no related document, the partialidad key).

**D1 — an out-of-vocabulary `TipoFactor` is booked as an ordinary taxable traslado.** §8a:210
fixes `TipoFactor ∈ {Tasa, Cuota, Exento}`, and §8:161 forbids handling unsupported input
silently. Confirmed by direct probe: a traslado stating `TipoFactor = "XYZ"` with a stated
`Importe` produces the *same* entry as `"Tasa"` — `DR clearing 116.00 · CR ingresos 100.00 ·
CR iva_trasladado_cobrado 16.00` — and raises no flag. Both tax readers are affected:
`contabilidad/rules/amounts.py:161` (4.1–4.4) and `contabilidad/rules/reposting.py:571` (4.5),
which special-case only `Exento` and otherwise book whatever `Importe` states. Not encoded
here on purpose: a test asserting today's entry would pin the defect, and an `xfail` would
normalise it into expected behaviour. The fix — and the choice between `MISSING_SOURCE_FIELD`,
`UNSUPPORTED_RULE` or new vocabulary — belongs to T4c's recon, not to this slice.

**D2 — an inconsistent `TasaOCuota` is not validated.** A traslado stating `TasaOCuota = 0.08`
with `Importe = 16.00` on `Base = 100.00` posts unchanged, because `amount_refusals`
(`amounts.py:177`) relates only `Total` to `base + IVA`; nothing relates the rate to the amount
it is supposed to compute. The axis looked covered because a mutation that *does* break the
total is refused (`test_m3_rule_4_1.py:224`) — but SAT's contract makes `TasaOCuota` a
constrained tax fact and `Importe` a calculated amount, so a rate disagreeing with it is a
validation gap, not an approved boundary. No `Base × TasaOCuota` rule is written here either:
the exact CFDI 4.0 formula, permitted precision and rounding boundaries must first be pinned
from the technical matrix. Deferred to T4c.

What this module asserts is therefore only current, *approved* refusal behaviour: a relation
group that is not `01` claims no credit-note reversal. With no entry computed there is nothing
for the validator to post, so "never `POSTED` under rules 4.6–4.9" holds by construction.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from sat_descarga_masiva.contabilidad.journal import LineSide
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    RelatedDocument,
    ReviewRequest,
)
from sat_descarga_masiva.contabilidad.rules.posting import propose
from sat_descarga_masiva.domain.model.fiscal_document import FiscalDocument
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

_SOURCE_HASH = "a" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
_NOTE_UUID = "7e80345d-917f-40bb-a98f-4a73939353c5"
_ORIGINAL_UUID = Uuid("8e80345d-917f-40bb-a98f-4a73939353c5")
_SUBSTITUTE_UUID = Uuid("9e80345d-917f-40bb-a98f-4a73939353c5")
_PUE = "PUE"


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN `E` note naming one original, as *source* facts, so one can be corrupt."""
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
        "uuid": _NOTE_UUID,
        "subtotal": "100.00",
        "fecha": "2026-01-15T12:00:00+00:00",
        "metodo_pago": None,
        "cfdi_relacionados": (RawCfdiRelacionados("01", (_ORIGINAL_UUID.value,)),),
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(**overrides: object) -> FiscalDocument:
    result = build_fiscal_document(_raw(**overrides), _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _original(uuid: Uuid = _ORIGINAL_UUID) -> RelatedDocument:
    """A resolved original the note names: `PUE` and `POSTED`, as the note family expects."""
    return RelatedDocument(
        uuid=uuid,
        tipo_relacion="01",
        metodo_pago=_PUE,
        total=NormalizedAmount(Decimal("116.00")),
        posted=True,
        moneda="MXN",
    )


def _context(
    document: FiscalDocument,
    *related: RelatedDocument,
    perspective: Perspective = Perspective.EMITIDO,
) -> PostingContext:
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document,
        perspective=perspective,
        related_documents=related or (_original(),),
    )


def _reviewed(proposal: object) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


def _flag_types(review: ReviewRequest) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in review.flags]


def _legs(review: ReviewRequest) -> list[tuple[AccountRole, LineSide, str]]:
    entry = review.entry
    assert entry is not None
    return [(line.account_role, line.side, str(line.amount.amount)) for line in entry.lines]


@pytest.mark.parametrize("tipo_relacion", ("04", "07"), ids=("substitution", "cancellation"))
@pytest.mark.parametrize(
    "perspective", (Perspective.EMITIDO, Perspective.RECIBIDO), ids=lambda value: value.value
)
def test_a_relation_that_is_not_a_credit_note_claims_no_reversal(
    tipo_relacion: str, perspective: Perspective
) -> None:
    """§8a:212 + §12: only `01` names the original a credit note reverses.

    `04` (substitution) and `07` (cancellation) are different claims about the same documents,
    and `_credit_note_uuids` reads a note as sourced rather than as "whatever it linked". The
    dangerous case is the one below: the group names a document the engine *did* resolve, so a
    reader taking any relation group would book a reversal against it. The refusal must be the
    relation one — no original named, no entry computed — and never the note family's evidence
    gate over a reversal the note did not claim.
    """
    document = _document(
        cfdi_relacionados=(RawCfdiRelacionados(tipo_relacion, (_ORIGINAL_UUID.value,)),)
    )

    review = _reviewed(propose(_context(document, perspective=perspective)))

    assert _flag_types(review) == [ReviewFlagType.EGRESO_RELATION_MISSING]
    assert review.entry is None


def test_a_substitution_group_beside_a_note_group_adds_no_second_original() -> None:
    """§8a:212: the `01` group is what the note cancels; a `04` group is a different claim.

    Both documents are the same note; the second one also names a substitution the engine can
    resolve. The reversal must be the one the `01` group alone produces — same legs, same flag
    — because a reader that counted every relation group would find two originals and either
    pick one (booking against a document the note did not say it corrected) or refuse a note it
    can read.
    """
    note_group = (RawCfdiRelacionados("01", (_ORIGINAL_UUID.value,)),)
    both_groups = (
        RawCfdiRelacionados("01", (_ORIGINAL_UUID.value,)),
        RawCfdiRelacionados("04", (_SUBSTITUTE_UUID.value,)),
    )

    alone = _reviewed(propose(_context(_document(cfdi_relacionados=note_group))))
    beside = _reviewed(
        propose(
            _context(
                _document(cfdi_relacionados=both_groups),
                _original(),
                _original(_SUBSTITUTE_UUID),
            )
        )
    )

    expected = [ReviewFlagType.EGRESO_IVA_EVIDENCE_MISSING]
    assert _flag_types(alone) == expected
    assert _flag_types(beside) == expected
    assert _legs(beside) == _legs(alone)
