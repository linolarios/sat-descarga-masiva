"""M3 step 4e: rules 4.5a/4.5b — the REP (`pago20`), §8:184/185/192.

A REP states payments, not sales: each `Pago` names the `DoctoRelacionado`s it settles, and each
related document states what it was paid and which taxes that payment carries. §8:192 turns that
into a *reclassification* — per related document, cash against the receivable (or payable) the
original created, and the IVA from pending to realized — and these tests pin every part of it that
could go wrong quietly:

- the **line key** is the leg's identity: role + related-document UUID + `NumParcialidad`, so two
  roles of one document and two partialidades of one document are different facts (§8:194), and
  the side is never part of it;
- the **IVA** is the sum of every `002` traslado the related document states, never the first one;
- the **ledger**, not the rule, says whether an original was ever posted — and only `POSTED` counts,
  because a `PROPOSED` or `SKIPPED` record moved nothing to settle;
- **§8:192's invariants** (`ImpSaldoAnt − ImpPagado == ImpSaldoInsoluto`, `Σ ImpPagado == Monto`,
  one key per fact) are checked rather than assumed, and a broken one refuses the document whole;
- a **blocking flag** keeps the deterministic legs and forbids the posting, while a **refusal**
  proposes no legs at all;
- **FX** (§8:193) is never invented: a payment or related document in another currency is flagged
  and not valued, and the `P` header's own currency is not what that test looks at.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pytest

from sat_descarga_masiva.contabilidad.journal import (
    LineSide,
    PostingState,
    ProposedJournalEntry,
)
from sat_descarga_masiva.contabilidad.roles import AccountRole
from sat_descarga_masiva.contabilidad.rules.contract import (
    PostingContext,
    ReviewRequest,
    Skip,
)
from sat_descarga_masiva.contabilidad.rules.posting import SUPPORTED_RULES, propose
from sat_descarga_masiva.contabilidad.rules.reposting import (
    RULE_4_5A,
    RULE_4_5A_VERSION,
    RULE_4_5B,
    RULE_4_5B_VERSION,
    rule_4_5a,
    rule_4_5b,
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
    RawDoctoRelacionado,
    RawImpuestos,
    RawImpuestosDR,
    RawImpuestosP,
    RawPago,
    RawPagos20,
    RawRetencionDR,
    RawRetencionP,
    RawTrasladoDR,
    RawTrasladoP,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.mapping.yaml_mapping import YamlMappingProvider

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_MAPPINGS = _FIXTURES / "mappings"
_SOURCE_HASH = "d" * 64
CONTRIBUTOR = Rfc("AAA010101AAA")
RECEPTOR = Rfc("BBB010101BBB")
#: The REP's own TFD UUID — what the *entry* is keyed by (§8:194).
REP_UUID = Uuid("333E4567-E89B-12D3-A456-426614174000")
#: Two CFDI the payment settles — what each *leg* is keyed by (§8:194).
PAID = Uuid("111E4567-E89B-12D3-A456-426614174000")
OTHER_PAID = Uuid("222E4567-E89B-12D3-A456-426614174000")
FECHA = "2024-03-15T10:30:00"
#: The payment's own date, deliberately different from the comprobante's `Fecha`: §8:192 dates the
#: entry by the document, so a key or date taken from here would be visible in these tests.
PAID_AT = "2024-04-01T09:00:00"


# --- the source: a REP built as the CFDI states it, then parsed by M2 -------------------


def _traslado_dr(
    importe: str | None = "16.00",
    *,
    impuesto: str = "002",
    tipo_factor: str = "Tasa",
    base: str = "100.00",
) -> RawTrasladoDR:
    return RawTrasladoDR(
        base_dr=base, impuesto_dr=impuesto, tipo_factor_dr=tipo_factor, importe_dr=importe
    )


def _impuestos_dr(
    *traslados: RawTrasladoDR, retenciones: tuple[RawRetencionDR, ...] = ()
) -> RawImpuestosDR:
    return RawImpuestosDR(traslados_dr=traslados, retenciones_dr=retenciones)


def _retencion_dr() -> RawRetencionDR:
    return RawRetencionDR(
        base_dr="100.00",
        impuesto_dr="002",
        tipo_factor_dr="Tasa",
        tasa_o_cuota_dr="0.106667",
        importe_dr="10.67",
    )


def _docto(**overrides: object) -> RawDoctoRelacionado:
    """One `DoctoRelacionado`: a fully paid MXN invoice carrying 16.00 of IVA.

    The outstanding balance is derived from the other two by default, so the fixture is always
    self-consistent (§8:192's `ImpSaldoAnt − ImpPagado == ImpSaldoInsoluto`) and a test that wants
    to break it says so explicitly.
    """
    saldo_ant = str(overrides.pop("imp_saldo_ant", "116.00"))
    pagado = overrides.pop("imp_pagado", "116.00")
    insoluto = str(overrides.pop("imp_saldo_insoluto", _remaining(saldo_ant, pagado)))
    base: dict[str, object] = {
        "id_documento": PAID.value,
        "moneda_dr": "MXN",
        "num_parcialidad": "1",
        "imp_saldo_ant": saldo_ant,
        "imp_pagado": pagado,
        "objeto_imp_dr": "01",
        "imp_saldo_insoluto": insoluto,
        "impuestos_dr": _impuestos_dr(_traslado_dr()),
    }
    base.update(overrides)
    return RawDoctoRelacionado(**base)  # type: ignore[arg-type]


def _remaining(saldo_ant: str, pagado: object) -> str:
    """What the outstanding balance would be, or 0.00 when the paid amount is unreadable."""
    try:
        return str(Decimal(saldo_ant) - Decimal(str(pagado)))
    except InvalidOperation:
        return "0.00"


def _pagos_with(*pagos: RawPago) -> RawPagos20:
    """A `Pagos` tree stating exactly these payments — the shape a REP's complemento carries."""
    return RawPagos20(version="2.0", pagos=pagos)


def _pago(**overrides: object) -> RawPago:
    """One `Pago`, settling exactly the related documents it states."""
    doctos = overrides.pop("doctos_relacionados", (_docto(),))
    base: dict[str, object] = {
        "fecha_pago": PAID_AT,
        "forma_de_pago_p": "03",
        "moneda_p": "MXN",
        "monto": "116.00",
        "doctos_relacionados": doctos,
    }
    base.update(overrides)
    return RawPago(**base)  # type: ignore[arg-type]


def _raw(**overrides: object) -> RawCfd:
    """A coherent MXN REP as *source* facts: one payment, one fully-paid related document."""
    pagos = overrides.pop("pagos", RawPagos20(version="2.0", pagos=(_pago(),)))
    base: dict[str, object] = {
        "tipo": "P",
        "version": "4.0",
        "moneda": "MXN",
        "tipo_cambio": None,
        "emisor_rfc": CONTRIBUTOR.value,
        "receptor_rfc": RECEPTOR.value,
        "conceptos": (RawConcepto("84111506", "1", "0.00", "0.00", None),),
        "impuestos": RawImpuestos(),
        "total": "0.00",
        "uuid": REP_UUID.value,
        "subtotal": "0.00",
        "fecha": FECHA,
        "metodo_pago": None,
        "pagos": pagos,
    }
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _document(**overrides: object) -> FiscalDocument:
    result = build_fiscal_document(_raw(**overrides), _SOURCE_HASH)
    assert result.document is not None
    return result.document


def _with_first_pago(document: FiscalDocument, **changes: object) -> FiscalDocument:
    """A copy of the document whose only payment is changed.

    For facts the parser cannot emit: `MoneyPolicy` nulls an amount only for a currency it cannot
    normalize (M2), so a `Monto` a REP never states is a source fact the rule must read directly.
    """
    pagos = document.pagos
    assert pagos is not None
    return replace(document, pagos=replace(pagos, pagos=(replace(pagos.pagos[0], **changes),)))


def _with_first_docto(document: FiscalDocument, **changes: object) -> FiscalDocument:
    """A copy of the document whose only related document is changed — see `_with_first_pago`."""
    pago = document.pagos
    assert pago is not None
    related = pago.pagos[0].doctos_relacionados
    return _with_first_pago(document, doctos_relacionados=(replace(related[0], **changes),))


def _context(
    document: FiscalDocument | None = None,
    *,
    perspective: Perspective = Perspective.EMITIDO,
    posted: tuple[Uuid, ...] = (),
) -> PostingContext:
    """The engine's view: whose books, which document, as whom, and what the ledger already holds.

    ``posted`` is §8:192's presence seam as the application layer supplies it: the related
    documents the client's books hold as `POSTED` entries.
    """
    return PostingContext(
        contributor_rfc=CONTRIBUTOR,
        document=document if document is not None else _document(),
        perspective=perspective,
        posted_source_uuids=frozenset(posted),
    )


def _settled(document: FiscalDocument | None = None, **overrides: object) -> PostingContext:
    """A collection whose original *is* in the ledger — the ordinary, reclassifying case."""
    return _context(document, posted=(PAID,), **overrides)  # type: ignore[arg-type]


def _legs(proposal: ProposedJournalEntry) -> list[tuple[AccountRole, LineSide, str]]:
    return [(line.account_role, line.side, str(line.amount.amount)) for line in proposal.lines]


def _keys(proposal: ProposedJournalEntry) -> list[str]:
    return [line.line_key for line in proposal.lines]


def _entry(proposal: ProposedJournalEntry | Skip | ReviewRequest) -> ProposedJournalEntry:
    assert isinstance(proposal, ProposedJournalEntry), proposal
    return proposal


def _refused(proposal: ProposedJournalEntry | Skip | ReviewRequest) -> ReviewRequest:
    assert isinstance(proposal, ReviewRequest), proposal
    return proposal


def _flag_types(proposal: ProposedJournalEntry | Skip | ReviewRequest) -> list[ReviewFlagType]:
    return [flag.flag_type for flag in _refused(proposal).flags]


# --- 4.5a: collecting, per related document (§8:184/192) --------------------------------


def test_rule_4_5a_reclassifies_a_collection_of_a_posted_original() -> None:
    """§8:184: cash in debits clearing, settles the receivable, and realizes the IVA (§8:202)."""
    entry = _entry(rule_4_5a(_settled()))
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
        (AccountRole.CLIENTES, LineSide.HABER, "116.00"),
        (AccountRole.IVA_TRASLADADO_NO_COBRADO, LineSide.DEBE, "16.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "16.00"),
    ]
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_5A
    assert entry.rule_version == RULE_4_5A_VERSION


def test_rule_4_5a_keys_every_leg_by_its_role_the_uuid_and_the_partialidad() -> None:
    """§8:194: the key names the *fact*, and the fact here is "role, document, partialidad"."""
    entry = _entry(rule_4_5a(_settled()))
    assert _keys(entry) == [
        f"clearing:{PAID.value}:1",
        f"clientes:{PAID.value}:1",
        f"iva_trasladado_no_cobrado:{PAID.value}:1",
        f"iva_trasladado_cobrado:{PAID.value}:1",
    ]


def test_the_entry_carries_the_reps_own_provenance_not_the_payments() -> None:
    """§8:169/194 + §8a:209: the entry is the comprobante's, dated by its own Fecha (§8:192)."""
    document = _document()
    entry = _entry(rule_4_5a(_settled(document)))
    assert entry.contributor_rfc == CONTRIBUTOR
    assert entry.source_uuid == document.source_uuid == REP_UUID
    assert entry.source_hash == document.source_hash
    assert entry.entry_date == date(2024, 3, 15)
    assert entry.entry_date != date(2024, 4, 1)  # the Pago's own FechaPago, deliberately not used


def test_one_comprobante_is_one_entry_however_many_payments_it_states() -> None:
    """§8:192: every `Pago` and every `DoctoRelacionado` contributes to the *one* entry.

    Two payments of one document (partialidades 1 and 2), plus a second document settled by the
    second: six money legs and six IVA legs, one entry.
    """
    first = _docto(num_parcialidad="1", imp_pagado="58.00", imp_saldo_insoluto="58.00")
    second = _docto(num_parcialidad="2", imp_saldo_ant="58.00", imp_pagado="58.00")
    other = _docto(id_documento=OTHER_PAID.value, num_parcialidad="1", imp_pagado="116.00")
    pagos = RawPagos20(
        version="2.0",
        pagos=(
            _pago(monto="58.00", doctos_relacionados=(first,)),
            _pago(monto="174.00", doctos_relacionados=(second, other)),
        ),
    )
    context = _context(_document(pagos=pagos), posted=(PAID, OTHER_PAID))
    entry = _entry(rule_4_5a(context))
    assert [line.line_key for line in entry.lines if line.account_role is AccountRole.CLIENTES] == [
        f"clientes:{PAID.value}:1",
        f"clientes:{PAID.value}:2",
        f"clientes:{OTHER_PAID.value}:1",
    ]
    assert [
        str(line.amount.amount) for line in entry.lines if line.account_role is AccountRole.CLEARING
    ] == ["58.00", "58.00", "116.00"]
    assert [
        str(line.amount.amount)
        for line in entry.lines
        if line.account_role is AccountRole.IVA_TRASLADADO_COBRADO
    ] == ["16.00", "16.00", "16.00"]
    assert len(entry.lines) == 12  # three related documents, four legs each
    assert entry.is_balanced()


def test_the_same_role_of_two_documents_and_two_partialidades_are_four_facts() -> None:
    """The corrected key scheme: role + UUID + NumParcialidad, and never a side (§8:194).

    Two related documents paid in one partialidad each, and one of them paid twice: the *same* role
    with different UUIDs, and the same role with the same UUID and different partialidades, all
    coexist — which a side-suffixed or UUID-only key could not express.
    """
    again = _docto(num_parcialidad="2", imp_saldo_ant="58.00", imp_pagado="58.00")
    other = _docto(id_documento=OTHER_PAID.value, imp_pagado="50.00")
    pagos = RawPagos20(
        version="2.0",
        pagos=(
            _pago(
                monto="58.00",
                doctos_relacionados=(_docto(imp_pagado="58.00", imp_saldo_insoluto="58.00"),),
            ),
            _pago(monto="108.00", doctos_relacionados=(again, other)),
        ),
    )
    entry = _entry(rule_4_5a(_context(_document(pagos=pagos), posted=(PAID, OTHER_PAID))))
    clearing = [line.line_key for line in entry.lines if line.account_role is AccountRole.CLEARING]
    assert clearing == [
        f"clearing:{PAID.value}:1",
        f"clearing:{PAID.value}:2",
        f"clearing:{OTHER_PAID.value}:1",
    ]
    assert len(set(_keys(entry))) == len(_keys(entry))  # §8:194: one key, one fact
    assert entry.is_balanced()


# --- 4.5b: paying — the mirror, per related document (§8:185/192) ------------------------


def test_rule_4_5b_reclassifies_a_payment_of_a_posted_original() -> None:
    """§8:185: cash out credits clearing, settles the payable, and realizes the IVA (§8:202)."""
    entry = _entry(
        rule_4_5b(
            _context(
                perspective=Perspective.RECIBIDO,
                posted=(PAID,),
            )
        )
    )
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.HABER, "116.00"),
        (AccountRole.PROVEEDORES, LineSide.DEBE, "116.00"),
        (AccountRole.IVA_ACRED_PENDIENTE, LineSide.HABER, "16.00"),
        (AccountRole.IVA_ACRED_PAGADO, LineSide.DEBE, "16.00"),
    ]
    assert entry.is_balanced()
    assert entry.rule_id == RULE_4_5B
    assert entry.rule_version == RULE_4_5B_VERSION


def test_rule_4_5b_keys_every_leg_by_its_role_the_uuid_and_the_partialidad() -> None:
    """§8:194: the other side's roles, the same identity — role, document, partialidad."""
    entry = _entry(rule_4_5b(_context(perspective=Perspective.RECIBIDO, posted=(PAID,))))
    assert _keys(entry) == [
        f"clearing:{PAID.value}:1",
        f"proveedores:{PAID.value}:1",
        f"iva_acred_pendiente:{PAID.value}:1",
        f"iva_acred_pagado:{PAID.value}:1",
    ]


def test_the_two_rows_move_the_same_four_role_families_on_opposite_sides() -> None:
    """§8:202: 4.5a/4.5b add no role — the same four, oriented by the direction of the money."""
    collected = _entry(rule_4_5a(_settled()))
    paid = _entry(rule_4_5b(_context(perspective=Perspective.RECIBIDO, posted=(PAID,))))
    assert {line.account_role for line in collected.lines} == {
        AccountRole.CLEARING,
        AccountRole.CLIENTES,
        AccountRole.IVA_TRASLADADO_NO_COBRADO,
        AccountRole.IVA_TRASLADADO_COBRADO,
    }
    assert {line.account_role for line in paid.lines} == {
        AccountRole.CLEARING,
        AccountRole.PROVEEDORES,
        AccountRole.IVA_ACRED_PENDIENTE,
        AccountRole.IVA_ACRED_PAGADO,
    }


# --- the IVA comes from the document's own impuestos_dr[], aggregated (§8:192) -----------


def test_the_iva_is_the_sum_of_every_iva_traslado_not_the_first() -> None:
    """§8:192: aggregate the `002` traslados — `impuestos_dr[0]` books one rate as all of them."""
    document = _document(
        pagos=RawPagos20(
            version="2.0",
            pagos=(
                _pago(
                    doctos_relacionados=(
                        _docto(
                            impuestos_dr=_impuestos_dr(
                                _traslado_dr("16.00", base="100.00"),
                                _traslado_dr("8.00", base="100.00"),
                            )
                        ),
                    ),
                ),
            ),
        )
    )
    entry = _entry(rule_4_5a(_settled(document)))
    realized = [
        str(line.amount.amount)
        for line in entry.lines
        if line.account_role is AccountRole.IVA_TRASLADADO_COBRADO
    ]
    assert realized == ["24.00"]
    assert entry.is_balanced()


def test_an_exempt_traslado_contributes_nothing() -> None:
    """An exempt traslado moves no amount, so it adds no leg and no cent (§8:192)."""
    document = _document(
        pagos=RawPagos20(
            version="2.0",
            pagos=(
                _pago(
                    doctos_relacionados=(
                        _docto(
                            impuestos_dr=_impuestos_dr(
                                _traslado_dr(None, tipo_factor="Exento"),
                                _traslado_dr("16.00"),
                            )
                        ),
                    ),
                ),
            ),
        )
    )
    entry = _entry(rule_4_5a(_settled(document)))
    assert [
        str(line.amount.amount)
        for line in entry.lines
        if line.account_role is AccountRole.IVA_TRASLADADO_COBRADO
    ] == ["16.00"]


def test_zero_iva_proposes_no_reclassification_legs() -> None:
    """§8:192 + §8:159: a zero leg is not a leg, so a tax-free payment reclassifies none."""
    document = _document(
        pagos=RawPagos20(
            version="2.0",
            pagos=(
                _pago(
                    monto="116.00",
                    doctos_relacionados=(
                        _docto(
                            impuestos_dr=_impuestos_dr(_traslado_dr(None, tipo_factor="Exento"))
                        ),
                    ),
                ),
            ),
        )
    )
    entry = _entry(rule_4_5a(_settled(document)))
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
        (AccountRole.CLIENTES, LineSide.HABER, "116.00"),
    ]
    assert entry.is_balanced()


def test_an_absent_impuestos_dr_is_not_a_missing_reading_but_a_statement() -> None:
    """§8:192: `impuestos_dr[]` is where a REP states taxes, so its absence states none."""
    document = _document(
        pagos=RawPagos20(
            version="2.0",
            pagos=(_pago(monto="116.00", doctos_relacionados=(_docto(impuestos_dr=None),)),),
        )
    )
    entry = _entry(rule_4_5a(_settled(document)))
    assert {line.account_role for line in entry.lines} == {
        AccountRole.CLEARING,
        AccountRole.CLIENTES,
    }
    assert entry.is_balanced()


# --- blocking flags: the deterministic legs are kept, and nothing posts (§8:192) ---------


def test_a_non_iva_traslado_keeps_the_iva_legs_and_blocks_the_entry() -> None:
    """§8:192: IVA is what these rows reclassify, so another impuesto is named — and never dropped.

    The IVA the document *does* state is still perfectly deterministic, so its legs are proposed
    beside the flag that stops them from posting: what the document says is never quietly reduced.
    """
    impuestos_dr = _impuestos_dr(
        _traslado_dr("16.00"), _traslado_dr("10.00", impuesto="003", base="100.00")
    )
    document = _document(
        pagos=RawPagos20(
            version="2.0",
            pagos=(
                _pago(monto="116.00", doctos_relacionados=(_docto(impuestos_dr=impuestos_dr),)),
            ),
        )
    )
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.UNSUPPORTED_RULE]
    entry = _refused(proposal).entry
    assert entry is not None
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
        (AccountRole.CLIENTES, LineSide.HABER, "116.00"),
        (AccountRole.IVA_TRASLADADO_NO_COBRADO, LineSide.DEBE, "16.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "16.00"),
    ]
    assert entry.is_balanced()


def test_retenciones_dr_keeps_the_deterministic_legs_and_blocks_the_entry() -> None:
    """§8:192: a withholding the reclassification does not book is a *blocking* flag, not a loss.

    The reclassification itself is fully determined by what the related document states, so every
    leg survives; the withholding keeps them from posting, and no advisory flag is involved.
    """
    impuestos_dr = _impuestos_dr(_traslado_dr(), retenciones=(_retencion_dr(),))
    document = _document(
        pagos=RawPagos20(
            version="2.0",
            pagos=(
                _pago(monto="116.00", doctos_relacionados=(_docto(impuestos_dr=impuestos_dr),)),
            ),
        )
    )
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.UNSUPPORTED_RULE]
    entry = _refused(proposal).entry
    assert entry is not None
    assert len(entry.lines) == 4
    assert entry.is_balanced()


def test_impuestos_p_disagreeing_with_the_related_documents_is_never_silently_chosen() -> None:
    """§8:192: the payment's own summary and its related documents are both evidence.

    §8:192 books the related documents' own taxes, so the legs are theirs — but a payment whose
    summary states something else is flagged rather than resolved quietly.
    """
    impuestos_p = RawImpuestosP(
        traslados_p=(
            RawTrasladoP(
                base_p="100.00", impuesto_p="002", tipo_factor_p="Tasa", importe_p="99.00"
            ),
        )
    )
    document = _document(
        pagos=RawPagos20(version="2.0", pagos=(_pago(monto="116.00", impuestos_p=impuestos_p),))
    )
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.REP_INVARIANT_VIOLATION]
    entry = _refused(proposal).entry
    assert entry is not None
    assert [
        str(line.amount.amount)
        for line in entry.lines
        if line.account_role is AccountRole.IVA_TRASLADADO_COBRADO
    ] == ["16.00"]  # the related document's own evidence, never the summary's


def test_retenciones_p_keeps_the_legs_and_blocks_the_entry() -> None:
    """The payment-level twin of `retenciones_dr`: stated, booked nowhere, and never ignored."""
    impuestos_p = RawImpuestosP(retenciones_p=(RawRetencionP(impuesto_p="002", importe_p="10.67"),))
    document = _document(
        pagos=RawPagos20(version="2.0", pagos=(_pago(monto="116.00", impuestos_p=impuestos_p),))
    )
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.UNSUPPORTED_RULE]
    entry = _refused(proposal).entry
    assert entry is not None
    assert len(entry.lines) == 4


# --- the ledger, not the rule, decides whether an original was posted (§8:192) ----------


def test_a_related_document_whose_original_is_not_posted_lands_on_the_unapplied_account() -> None:
    """§8:192: an unbooked original leaves the money nothing to settle, so it is flagged.

    The cash movement is still a fact — it is booked against the configured unapplied-payment
    account — and the IVA is deliberately not realized or reclassified, so the reclassification is
    never recorded half-done.
    """
    proposal = rule_4_5a(_context())
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_REP_ORIGINAL]
    entry = _refused(proposal).entry
    assert entry is not None
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
        (AccountRole.PAGOS_NO_APLICADOS, LineSide.HABER, "116.00"),
    ]
    assert _keys(entry) == [
        f"clearing:{PAID.value}:1",
        f"pagos_no_aplicados:{PAID.value}:1",
    ]
    assert entry.is_balanced()


def test_a_payment_of_an_unbooked_purchase_debits_the_unapplied_account() -> None:
    """The mirror for 4.5b: money out against an unbooked purchase is a prepayment, and flagged."""
    proposal = rule_4_5b(_context(perspective=Perspective.RECIBIDO))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_REP_ORIGINAL]
    entry = _refused(proposal).entry
    assert entry is not None
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.HABER, "116.00"),
        (AccountRole.PAGOS_NO_APLICADOS, LineSide.DEBE, "116.00"),
    ]
    assert entry.is_balanced()


def test_a_mixed_known_and_unknown_original_stays_one_balanced_entry() -> None:
    """§8:192's mandatory mix: one related document is booked, the other is not.

    The known one reclassifies normally; the unknown one books its cash to the unapplied-payment
    account with a flag; and the single entry they share still balances, because each related
    document contributes the same two money legs.
    """
    known = _docto(num_parcialidad="1", imp_pagado="116.00")
    unknown = _docto(id_documento=OTHER_PAID.value, num_parcialidad="1", imp_pagado="100.00")
    document = _document(
        pagos=RawPagos20(
            version="2.0",
            pagos=(_pago(monto="216.00", doctos_relacionados=(known, unknown)),),
        )
    )
    proposal = rule_4_5a(_context(document, posted=(PAID,)))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_REP_ORIGINAL]
    entry = _refused(proposal).entry
    assert entry is not None
    assert _legs(entry) == [
        (AccountRole.CLEARING, LineSide.DEBE, "116.00"),
        (AccountRole.CLIENTES, LineSide.HABER, "116.00"),
        (AccountRole.IVA_TRASLADADO_NO_COBRADO, LineSide.DEBE, "16.00"),
        (AccountRole.IVA_TRASLADADO_COBRADO, LineSide.HABER, "16.00"),
        (AccountRole.CLEARING, LineSide.DEBE, "100.00"),
        (AccountRole.PAGOS_NO_APLICADOS, LineSide.HABER, "100.00"),
    ]
    assert _keys(entry) == [
        f"clearing:{PAID.value}:1",
        f"clientes:{PAID.value}:1",
        f"iva_trasladado_no_cobrado:{PAID.value}:1",
        f"iva_trasladado_cobrado:{PAID.value}:1",
        f"clearing:{OTHER_PAID.value}:1",
        f"pagos_no_aplicados:{OTHER_PAID.value}:1",
    ]
    assert entry.is_balanced()


# --- refusals: the document contradicts itself, so no legs are proposed (§8:192) ---------


def test_a_saldo_that_does_not_reconcile_is_refused_whole() -> None:
    """§8:192's invariant: `ImpSaldoAnt − ImpPagado == ImpSaldoInsoluto`, or nothing is proposed."""
    docto = _docto(imp_saldo_insoluto="99.00")
    document = _document(pagos=_pagos_with(_pago(doctos_relacionados=(docto,))))
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.REP_INVARIANT_VIOLATION]
    entry = _refused(proposal).entry
    assert entry is not None and entry.lines == ()


def test_a_payment_whose_related_documents_do_not_sum_to_its_monto_is_refused() -> None:
    """§8:192: the payment's own arithmetic must hold before any of it is booked."""
    proposal = rule_4_5a(_settled(_document(pagos=_pagos_with(_pago(monto="50.00")))))
    assert _flag_types(proposal) == [ReviewFlagType.REP_INVARIANT_VIOLATION]


def test_the_same_partialidad_of_the_same_document_twice_is_refused() -> None:
    """§8:194: one key names one fact, so a repeat is a contradiction — not a crash."""
    twice = _pagos_with(
        _pago(monto="232.00", doctos_relacionados=(_docto(), _docto())),
    )
    proposal = rule_4_5a(_settled(_document(pagos=twice)))
    assert _flag_types(proposal) == [ReviewFlagType.REP_INVARIANT_VIOLATION]


def test_a_rep_without_a_pago20_complemento_is_reviewed() -> None:
    """§8:192: the REP *is* its `Pagos` tree, so a `P` without one states nothing to book."""
    proposal = rule_4_5a(_settled(_document(pagos=None)))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    assert _refused(proposal).entry is not None


def test_a_payment_that_states_no_related_document_is_refused() -> None:
    """§8:192's row is per related document, so a payment naming none proposes no legs."""
    proposal = rule_4_5a(_settled(_document(pagos=_pagos_with(_pago(doctos_relacionados=())))))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    entry = _refused(proposal).entry
    assert entry is not None and entry.lines == ()


@pytest.mark.parametrize("pagado", ["0.00", "-10.00", "NaN"])
def test_an_unreadable_imp_pagado_is_refused(pagado: str) -> None:
    """§8:159/§12: a leg moves a positive, finite amount, so an unusable one is a reason."""
    docto = _docto(imp_pagado=pagado, imp_saldo_insoluto="0.00")
    document = _document(pagos=_pagos_with(_pago(doctos_relacionados=(docto,))))
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.INVALID_AMOUNT]
    entry = _refused(proposal).entry
    assert entry is not None and entry.lines == ()


def test_a_missing_imp_pagado_is_refused() -> None:
    """§8:192 reads what a document was paid, so an absent amount is a reason, not a zero."""
    proposal = rule_4_5a(_settled(_with_first_docto(_document(), imp_pagado=None)))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    entry = _refused(proposal).entry
    assert entry is not None and entry.lines == ()


def test_a_payment_that_states_no_monto_is_refused() -> None:
    """§8:192 checks what the payment applied against what it says it moved (§8:159)."""
    proposal = rule_4_5a(_settled(_with_first_pago(_document(), monto=None)))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_an_iva_traslado_without_an_importe_is_refused() -> None:
    """A rate-bearing traslado the rule cannot compute from is a reason, not a zero (§8:192)."""
    impuestos_dr = _impuestos_dr(_traslado_dr(None, tipo_factor="Tasa"))
    document = _document(
        pagos=_pagos_with(_pago(doctos_relacionados=(_docto(impuestos_dr=impuestos_dr),)))
    )
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_impuestos_p_without_an_importe_is_refused() -> None:
    """A summary that cannot be read is a reason: §8:192 never silently prefers one statement."""
    impuestos_p = RawImpuestosP(
        traslados_p=(RawTrasladoP(base_p="100.00", impuesto_p="002", tipo_factor_p="Tasa"),)
    )
    document = _document(pagos=_pagos_with(_pago(impuestos_p=impuestos_p)))
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]


def test_an_undatable_rep_cannot_name_an_entry() -> None:
    """§8a:209: a document with no Fecha is flagged on itself, never dated by 'today'."""
    proposal = rule_4_5a(_context(_document(fecha=None), posted=(PAID,)))
    assert _flag_types(proposal) == [ReviewFlagType.MISSING_SOURCE_FIELD]
    assert _refused(proposal).entry is None


# --- FX: never invented, and never about the `P` header (§8:192/193) ----------------------


def test_a_payment_in_another_currency_is_flagged_and_cannot_post() -> None:
    """§8:193: a payment in a currency nobody valued keeps the entry un-postable.

    The related documents' own amounts *are* valued — they are stated in `MXN` — so their legs are
    the deterministic part of the calculation and are recorded; what is missing is the FX decision
    the rule must not invent, so no FX gain or loss leg is ever computed and the flag stops the
    posting.
    """
    document = _document(
        moneda="USD",
        tipo_cambio="17.50",
        pagos=_pagos_with(_pago(moneda_p="USD")),
    )
    proposal = rule_4_5a(_settled(document))
    assert _flag_types(proposal) == [ReviewFlagType.FX_DIFFERENCE_UNCONFIRMED]
    entry = _refused(proposal).entry
    assert entry is not None
    assert {line.account_role for line in entry.lines} == {
        AccountRole.CLEARING,
        AccountRole.CLIENTES,
        AccountRole.IVA_TRASLADADO_NO_COBRADO,
        AccountRole.IVA_TRASLADADO_COBRADO,
    }
    assert entry.is_balanced()
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(
        replace(_settled(replace(document, status=FiscalDocumentStatus.VIGENTE))),
        proposal,
    )
    assert decision.posting_state is PostingState.PROPOSED


def test_a_related_document_in_another_currency_is_not_valued_but_its_sibling_is() -> None:
    """§8:193 per related document: the ones that can be valued are, the one that cannot is not."""
    foreign = _docto(id_documento=OTHER_PAID.value, moneda_dr="USD", imp_pagado="100.00")
    document = _document(
        pagos=_pagos_with(
            _pago(
                monto="216.00",
                doctos_relacionados=(_docto(imp_pagado="116.00"), foreign),
            )
        )
    )
    proposal = rule_4_5a(_context(document, posted=(PAID, OTHER_PAID)))
    assert _flag_types(proposal) == [ReviewFlagType.FX_DIFFERENCE_UNCONFIRMED]
    entry = _refused(proposal).entry
    assert entry is not None
    assert _keys(entry) == [
        f"clearing:{PAID.value}:1",
        f"clientes:{PAID.value}:1",
        f"iva_trasladado_no_cobrado:{PAID.value}:1",
        f"iva_trasladado_cobrado:{PAID.value}:1",
    ]
    assert entry.is_balanced()


# --- the selector, and the whole seam (§8:159/184/185) -----------------------------------


@pytest.mark.parametrize(
    "perspective,rule_id",
    [(Perspective.EMITIDO, RULE_4_5A), (Perspective.RECIBIDO, RULE_4_5B)],
)
def test_the_one_door_routes_a_rep_to_its_side(perspective: Perspective, rule_id: str) -> None:
    """§8:161/184/185: a `P` comprobante is claimed by its side's REP row and by nothing else."""
    proposal = propose(_context(perspective=perspective, posted=(PAID,)))
    entry = _entry(proposal)
    assert entry.rule_id == rule_id
    assert entry.is_balanced()


def test_a_rep_is_never_silently_skipped_and_never_needs_a_metodo_pago() -> None:
    """§8:161/192: a REP is not out of scope, and it carries no header method to select on."""
    document = _document()
    assert document.metodo_pago is None
    proposal = propose(_context(document, posted=(PAID,)))
    assert not isinstance(proposal, Skip)


def test_the_collection_posts_end_to_end_through_the_committed_chart() -> None:
    """§8:159/§8:184/§8a:207: source facts → the rule → the validator → the chart a human wrote."""
    document = replace(_document(), status=FiscalDocumentStatus.VIGENTE)
    context = _settled(document)
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, propose(context))
    assert decision.posting_state is PostingState.POSTED
    assert decision.account_for(AccountRole.CLEARING) == "102-099"
    assert decision.account_for(AccountRole.CLIENTES) == "105-001"
    assert decision.account_for(AccountRole.IVA_TRASLADADO_NO_COBRADO) == "209-001"
    assert decision.account_for(AccountRole.IVA_TRASLADADO_COBRADO) == "208-001"
    entry = decision.entry
    assert entry is not None
    assert entry.fingerprint(decision.mapping_version).canonical()


def test_a_blocked_rep_stays_proposed_however_balanced_its_legs_are() -> None:
    """§8:192 + §8:159: a blocking flag is a reason, and a reason outranks arithmetic.

    The entry balances and every role resolves, and the retenciones the reclassification cannot book
    still keep it from posting — which is exactly why the flag is not advisory.
    """
    impuestos_dr = _impuestos_dr(_traslado_dr(), retenciones=(_retencion_dr(),))
    document = replace(
        _document(
            pagos=_pagos_with(
                _pago(monto="116.00", doctos_relacionados=(_docto(impuestos_dr=impuestos_dr),))
            )
        ),
        status=FiscalDocumentStatus.VIGENTE,
    )
    context = _settled(document)
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, propose(context))
    assert decision.posting_state is PostingState.PROPOSED
    assert ReviewFlagType.UNSUPPORTED_RULE in [flag.flag_type for flag in decision.review_flags]
    entry = decision.entry
    assert entry is not None and entry.is_balanced()


def test_an_unbooked_original_is_recorded_and_refused_with_a_reason() -> None:
    """§8:192: the cash movement is written down, the reclassification is not, and a human decides.

    The rule refuses; the validator honours that verdict verbatim and adds nothing (§8:159's
    conditions are the rule's to run, not the validator's), so the single reason on the decision is
    the one the rule gave. The chart names no account for `PAGOS_NO_APLICADOS` either — which is
    why the recorded leg carries no resolved account rather than a guessed one.
    """
    document = replace(_document(), status=FiscalDocumentStatus.VIGENTE)
    context = _context(document)
    decision = PostingEligibilityValidator(
        YamlMappingProvider(_MAPPINGS), supported_rules=SUPPORTED_RULES
    ).decide(context, propose(context))
    assert decision.posting_state is PostingState.PROPOSED
    assert [flag.flag_type for flag in decision.review_flags] == [
        ReviewFlagType.MISSING_REP_ORIGINAL,
    ]
    assert decision.account_for(AccountRole.PAGOS_NO_APLICADOS) is None
    entry = decision.entry
    assert entry is not None
    assert {line.account_role for line in entry.lines} == {
        AccountRole.CLEARING,
        AccountRole.PAGOS_NO_APLICADOS,
    }
