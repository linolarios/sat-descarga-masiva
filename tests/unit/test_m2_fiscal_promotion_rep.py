"""M2.4b: promote REP (Pagos 2.0) source facts into FiscalDocument.

The typed layer records what the complemento says — every payment, every related
document, every tax element, in source order and in its own currency — without
deriving any accounting meaning: no balance invariant, no tax aggregation, no FX
translation, no ledger or eligibility decision.
"""

from dataclasses import MISSING, fields
from decimal import Decimal
from pathlib import Path

import pytest

from sat_descarga_masiva.domain.model.fiscal_document import (
    DoctoRelacionado,
    FiscalDocument,
    Impuestos,
    ImpuestosDR,
    ImpuestosP,
    Pago,
    Pagos20,
    ParseOutcome,
    RetencionDR,
    RetencionP,
    TotalesPagos,
    TrasladoDR,
    TrasladoP,
)
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
    RawTotalesPagos,
    RawTrasladoDR,
    RawTrasladoP,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SOURCE_HASH = "a" * 64
_DETALLADO = "cfdi_pago_detallado_4_0.xml"
_SIMPLE_P = "cfdi_pago_4_0.xml"

_UUID_1 = "33333333-3333-3333-3333-333333333333"
_UUID_2 = "44444444-4444-4444-4444-444444444444"
_UUID_3 = "55555555-5555-5555-5555-555555555555"
_UUID_SIMPLE = "123e4567-e89b-12d3-a456-426614174000"

_parser = SatcfdiFiscalParser()


def _build(name: str):
    raw = _parser.parse((_FIXTURES / name).read_bytes(), _SOURCE_HASH)
    return raw, build_fiscal_document(raw, _SOURCE_HASH)


def _doc(name: str) -> FiscalDocument:
    _, result = _build(name)
    assert result.document is not None
    return result.document


def _pagos(name: str) -> Pagos20:
    pagos = _doc(name).pagos
    assert pagos is not None
    return pagos


def _amount(value: str) -> NormalizedAmount:
    return NormalizedAmount(Decimal(value))


def _raw(**overrides: object) -> RawCfd:
    base = dict(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc="AAA010101AAA",
        receptor_rfc="BBB010101BBB",
        conceptos=(RawConcepto("01010101", "1", "100.00", "100.00", None),),
        impuestos=RawImpuestos(
            traslados=(), retenciones=(), total_traslados=None, total_retenciones=None
        ),
        total="116.00",
    )
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def _docto(**overrides: object) -> RawDoctoRelacionado:
    base = dict(
        id_documento=_UUID_1,
        moneda_dr="MXN",
        num_parcialidad="1",
        imp_saldo_ant="116.00",
        imp_pagado="116.00",
        objeto_imp_dr="02",
        imp_saldo_insoluto="0.00",
    )
    base.update(overrides)
    return RawDoctoRelacionado(**base)  # type: ignore[arg-type]


def _pago(**overrides: object) -> RawPago:
    base = dict(
        fecha_pago="2024-02-10T10:30:00+00:00",
        forma_de_pago_p="03",
        moneda_p="MXN",
        monto="116.00",
    )
    base.update(overrides)
    return RawPago(**base)  # type: ignore[arg-type]


def _pagos_raw(*pagos: RawPago) -> RawPagos20:
    return RawPagos20(version="2.0", pagos=pagos)


def _p_receipt(**overrides: object) -> RawCfd:
    """A type-P comprobante: header Moneda=XXX plus a Pagos 2.0 complemento."""
    base = dict(
        tipo="P",
        moneda="XXX",
        total="0",
        pagos=_pagos_raw(_pago(doctos_relacionados=(_docto(),))),
    )
    base.update(overrides)
    return _raw(**base)


def _build_p(raw: RawCfd):
    return build_fiscal_document(raw, _SOURCE_HASH)


def _built_from(raw: RawCfd) -> FiscalDocument:
    result = _build_p(raw)
    assert result.document is not None
    return result.document


def _built_p(**overrides: object) -> FiscalDocument:
    return _built_from(_p_receipt(**overrides))


def _docto_from_raw(**overrides: object) -> DoctoRelacionado:
    """The single related document of a synthetic one-payment REP receipt."""
    receipt = _p_receipt(pagos=_pagos_raw(_pago(doctos_relacionados=(_docto(**overrides),))))
    pagos = _built_from(receipt).pagos
    assert pagos is not None
    return pagos.pagos[0].doctos_relacionados[0]


def _impuestos_dr_of_two_rates() -> ImpuestosDR:
    """Promoted ImpuestosDR for a related document carrying two distinct IVA rates."""
    docto = _docto_from_raw(
        impuestos_dr=RawImpuestosDR(
            traslados_dr=(
                RawTrasladoDR("100.00", "002", "Tasa", "0.160000", "16.00"),
                RawTrasladoDR("100.00", "003", "Tasa", "0.080000", "8.00"),
            )
        )
    )
    assert docto.impuestos_dr is not None
    return docto.impuestos_dr


# --- structure --------------------------------------------------------------


def test_non_p_document_has_no_pagos() -> None:
    assert _doc("cfdi_ingreso_4_0.xml").pagos is None


def test_pago_document_promotes_pagos() -> None:
    assert _pagos(_SIMPLE_P) is not None


def test_pagos_version_is_preserved() -> None:
    assert _pagos(_SIMPLE_P).version == "2.0"
    assert _pagos(_DETALLADO).version == "2.0"


def test_all_payments_are_promoted_in_source_order() -> None:
    pagos = _pagos(_DETALLADO)
    assert [p.fecha_pago for p in pagos.pagos] == [
        "2024-02-10T10:30:00+00:00",
        "2024-03-10T09:15:00+00:00",
    ]


def test_multiple_payments_are_not_merged() -> None:
    assert len(_pagos(_DETALLADO).pagos) == 2


def test_doctos_relacionados_keep_source_order() -> None:
    payment = _pagos(_DETALLADO).pagos[0]
    assert [d.uuid for d in payment.doctos_relacionados] == [Uuid(_UUID_1), Uuid(_UUID_2)]


def test_doctos_of_each_payment_stay_with_that_payment() -> None:
    pagos = _pagos(_DETALLADO)
    assert [d.uuid for d in pagos.pagos[0].doctos_relacionados] == [Uuid(_UUID_1), Uuid(_UUID_2)]
    assert [d.uuid for d in pagos.pagos[1].doctos_relacionados] == [Uuid(_UUID_3)]


# --- Pago -------------------------------------------------------------------


def test_pago_fecha_pago_is_promoted_as_source_text() -> None:
    assert _pagos(_DETALLADO).pagos[0].fecha_pago == "2024-02-10T10:30:00+00:00"


def test_pago_forma_de_pago_p_is_promoted_as_source_code() -> None:
    pagos = _pagos(_DETALLADO)
    assert pagos.pagos[0].forma_de_pago_p == "03"
    assert pagos.pagos[1].forma_de_pago_p == "01"


def test_pago_num_operacion_present_is_preserved() -> None:
    assert _pagos(_DETALLADO).pagos[0].num_operacion == "OP-0001"


def test_pago_num_operacion_absent_stays_none() -> None:
    assert _pagos(_SIMPLE_P).pagos[0].num_operacion is None


def test_pago_moneda_p_is_preserved() -> None:
    pagos = _pagos(_DETALLADO)
    assert pagos.pagos[0].moneda_p == "MXN"
    assert pagos.pagos[1].moneda_p == "USD"


def test_pago_tipo_cambio_p_is_decimal() -> None:
    pagos = _pagos(_DETALLADO)
    assert pagos.pagos[0].tipo_cambio_p == Decimal("1")
    assert pagos.pagos[1].tipo_cambio_p == Decimal("17.5000")  # source precision kept


def test_pago_monto_is_normalized_in_moneda_p() -> None:
    assert _pagos(_DETALLADO).pagos[0].monto == _amount("166.00")


def test_usd_payment_uses_usd_not_the_xxx_header_currency() -> None:
    """The header is XXX; the payment amount is USD 100 — never converted."""
    payment = _pagos(_DETALLADO).pagos[1]
    assert _doc(_DETALLADO).moneda == "XXX"
    assert payment.moneda_p == "USD"
    assert payment.monto == _amount("100.00")


# --- DoctoRelacionado -------------------------------------------------------


def test_docto_uuid_comes_from_id_documento() -> None:
    assert _pagos(_SIMPLE_P).pagos[0].doctos_relacionados[0].uuid == Uuid(_UUID_SIMPLE)


def test_docto_uuid_is_canonicalized() -> None:
    uuid = _pagos(_SIMPLE_P).pagos[0].doctos_relacionados[0].uuid
    assert uuid.value == _UUID_SIMPLE.upper()


def test_docto_malformed_uuid_raises_value_error() -> None:
    docto = _docto(id_documento="not-a-uuid")
    with pytest.raises(ValueError):
        _build_p(_p_receipt(pagos=_pagos_raw(_pago(doctos_relacionados=(docto,)))))


def test_docto_moneda_dr_is_preserved() -> None:
    pagos = _pagos(_DETALLADO)
    assert pagos.pagos[0].doctos_relacionados[0].moneda_dr == "MXN"
    assert pagos.pagos[1].doctos_relacionados[0].moneda_dr == "USD"


def test_docto_num_parcialidad_is_int() -> None:
    parcialidad = _pagos(_DETALLADO).pagos[0].doctos_relacionados[1].num_parcialidad
    assert parcialidad == 2
    assert isinstance(parcialidad, int)


def test_docto_num_parcialidad_non_integral_raises() -> None:
    docto = _docto(num_parcialidad="1.5")
    with pytest.raises(ValueError):
        _build_p(_p_receipt(pagos=_pagos_raw(_pago(doctos_relacionados=(docto,)))))


def test_docto_amounts_are_normalized_in_moneda_dr() -> None:
    docto = _pagos(_DETALLADO).pagos[1].doctos_relacionados[0]
    assert docto.moneda_dr == "USD"
    assert docto.imp_saldo_ant == _amount("100.00")
    assert docto.imp_pagado == _amount("100.00")
    assert docto.imp_saldo_insoluto == _amount("0.00")


def test_full_payment_amounts_are_preserved() -> None:
    docto = _pagos(_DETALLADO).pagos[0].doctos_relacionados[0]
    assert docto.imp_saldo_ant == _amount("116.00")
    assert docto.imp_pagado == _amount("116.00")
    assert docto.imp_saldo_insoluto == _amount("0.00")


def test_partial_payment_amounts_are_preserved() -> None:
    docto = _pagos(_DETALLADO).pagos[0].doctos_relacionados[1]
    assert docto.imp_saldo_ant == _amount("200.00")
    assert docto.imp_pagado == _amount("50.00")
    assert docto.imp_saldo_insoluto == _amount("150.00")


def test_docto_objeto_imp_dr_is_preserved() -> None:
    pagos = _pagos(_DETALLADO)
    assert pagos.pagos[0].doctos_relacionados[0].objeto_imp_dr == "02"
    assert pagos.pagos[1].doctos_relacionados[0].objeto_imp_dr == "01"


def test_docto_optional_fields_absent_stay_none() -> None:
    docto = _pagos(_DETALLADO).pagos[0].doctos_relacionados[0]
    assert docto.equivalencia_dr is None
    assert docto.serie is None
    assert docto.folio is None


def test_docto_serie_and_folio_present_are_preserved() -> None:
    docto = _pagos(_DETALLADO).pagos[1].doctos_relacionados[0]
    assert docto.serie == "A"
    assert docto.folio == "100"


def test_docto_equivalencia_dr_present_is_decimal() -> None:
    assert _docto_from_raw(equivalencia_dr="1.000000").equivalencia_dr == Decimal("1.000000")


def test_docto_without_impuestos_dr_keeps_none() -> None:
    assert _pagos(_SIMPLE_P).pagos[0].doctos_relacionados[0].impuestos_dr is None


# --- taxes ------------------------------------------------------------------


def test_all_dr_traslados_are_preserved() -> None:
    docto = _pagos(_DETALLADO).pagos[0].doctos_relacionados[0]
    assert docto.impuestos_dr is not None
    assert docto.impuestos_dr.traslados_dr == (
        TrasladoDR(
            base_dr=_amount("100.00"),
            impuesto_dr="002",
            tipo_factor_dr="Tasa",
            tasa_o_cuota_dr=Decimal("0.160000"),
            importe_dr=_amount("16.00"),
        ),
    )


def test_all_dr_retenciones_are_preserved() -> None:
    docto = _pagos(_DETALLADO).pagos[0].doctos_relacionados[1]
    assert docto.impuestos_dr is not None
    assert docto.impuestos_dr.retenciones_dr == (
        RetencionDR(
            base_dr=_amount("50.00"),
            impuesto_dr="002",
            tipo_factor_dr="Tasa",
            tasa_o_cuota_dr=Decimal("0.106667"),
            importe_dr=_amount("5.33"),
        ),
    )


def test_empty_traslados_dr_keeps_empty_tuple() -> None:
    docto = _pagos(_DETALLADO).pagos[0].doctos_relacionados[0]
    assert docto.impuestos_dr is not None
    assert docto.impuestos_dr.retenciones_dr == ()


def test_all_payment_level_traslados_are_preserved() -> None:
    payment = _pagos(_DETALLADO).pagos[0]
    assert payment.impuestos_p is not None
    assert payment.impuestos_p.traslados_p == (
        TrasladoP(
            base_p=_amount("150.00"),
            impuesto_p="002",
            tipo_factor_p="Tasa",
            tasa_o_cuota_p=Decimal("0.160000"),
            importe_p=_amount("24.00"),
        ),
    )


def test_all_payment_level_retenciones_are_preserved() -> None:
    payment = _pagos(_DETALLADO).pagos[0]
    assert payment.impuestos_p is not None
    assert payment.impuestos_p.retenciones_p == (
        RetencionP(impuesto_p="002", importe_p=_amount("5.33")),
    )


def test_impuestos_p_absent_stays_none() -> None:
    assert _pagos(_DETALLADO).pagos[1].impuestos_p is None
    assert _pagos(_SIMPLE_P).pagos[0].impuestos_p is None


def test_impuestos_dr_and_impuestos_p_remain_independent() -> None:
    """Document-level and payment-level taxes are both kept, neither replaces the other."""
    payment = _pagos(_DETALLADO).pagos[0]
    docto = payment.doctos_relacionados[0]
    assert docto.impuestos_dr is not None
    assert payment.impuestos_p is not None
    assert docto.impuestos_dr.traslados_dr[0].importe_dr == _amount("16.00")
    assert payment.impuestos_p.traslados_p[0].importe_p == _amount("24.00")


def test_multiple_dr_rates_are_not_collapsed_to_the_first() -> None:
    """AGENT.md §8: never impuestos_dr[0]; every element survives, in source order."""
    impuestos = _impuestos_dr_of_two_rates()
    assert [t.impuesto_dr for t in impuestos.traslados_dr] == ["002", "003"]
    assert [t.tasa_o_cuota_dr for t in impuestos.traslados_dr] == [
        Decimal("0.160000"),
        Decimal("0.080000"),
    ]
    assert [t.importe_dr for t in impuestos.traslados_dr] == [_amount("16.00"), _amount("8.00")]


def test_multiple_dr_traslados_are_not_summed() -> None:
    impuestos = _impuestos_dr_of_two_rates()
    assert len(impuestos.traslados_dr) == 2
    assert sum(t.importe_dr.amount for t in impuestos.traslados_dr if t.importe_dr) == Decimal(
        "24.00"
    )  # 16.00 + 8.00 — the tuple is retained, not replaced by the sum


def test_multiple_payment_level_traslados_are_not_collapsed() -> None:
    raw = RawImpuestosP(
        traslados_p=(
            RawTrasladoP("100.00", "002", "Tasa", "0.160000", "16.00"),
            RawTrasladoP("100.00", "003", "Tasa", "0.080000", "8.00"),
        )
    )
    payment = _built_p(pagos=_pagos_raw(_pago(impuestos_p=raw))).pagos
    assert payment is not None
    assert len(payment.pagos[0].impuestos_p.traslados_p) == 2


def test_multiple_dr_retenciones_are_not_collapsed_to_the_first() -> None:
    docto = _docto_from_raw(
        impuestos_dr=RawImpuestosDR(
            retenciones_dr=(
                RawRetencionDR("200.00", "002", "Tasa", "0.106667", "21.33"),
                RawRetencionDR("200.00", "003", "Tasa", "0.012500", "2.50"),
            )
        )
    )
    assert docto.impuestos_dr is not None
    assert [r.impuesto_dr for r in docto.impuestos_dr.retenciones_dr] == ["002", "003"]
    assert [r.importe_dr for r in docto.impuestos_dr.retenciones_dr] == [
        _amount("21.33"),
        _amount("2.50"),
    ]
    assert docto.impuestos_dr.traslados_dr == ()


def test_multiple_payment_level_retenciones_are_not_collapsed() -> None:
    raw = RawImpuestosP(
        retenciones_p=(
            RawRetencionP("002", "21.33"),
            RawRetencionP("003", "2.50"),
        )
    )
    payment = _built_p(pagos=_pagos_raw(_pago(impuestos_p=raw))).pagos
    assert payment is not None
    assert payment.pagos[0].impuestos_p.retenciones_p == (
        RetencionP(impuesto_p="002", importe_p=_amount("21.33")),
        RetencionP(impuesto_p="003", importe_p=_amount("2.50")),
    )
    assert payment.pagos[0].impuestos_p.traslados_p == ()


# --- TotalesPagos -----------------------------------------------------------


def test_monto_total_pagos_is_promoted() -> None:
    totales = _pagos(_DETALLADO).totales
    assert totales is not None
    assert totales.monto_total_pagos == _amount("1916.00")
    assert _pagos(_SIMPLE_P).totales.monto_total_pagos == _amount("116.00")


def test_monto_total_pagos_is_in_national_currency_not_moneda_p() -> None:
    """166.00 MXN + 100.00 USD x 17.5000 = 1916.00: the total is a MXN figure."""
    pagos = _pagos(_DETALLADO)
    assert pagos.totales is not None
    mxn_payment = pagos.pagos[0].monto
    usd_payment = pagos.pagos[1].monto
    rate = pagos.pagos[1].tipo_cambio_p
    assert mxn_payment is not None
    assert usd_payment is not None
    assert rate is not None
    assert pagos.totales.monto_total_pagos.amount == mxn_payment.amount + usd_payment.amount * rate


def test_absent_totales_stay_none() -> None:
    assert _built_p().pagos.totales is None


def test_totales_present_is_promoted() -> None:
    doc = _built_p(pagos=RawPagos20(version="2.0", pagos=(), totales=RawTotalesPagos("250.50")))
    assert doc.pagos is not None
    assert doc.pagos.totales == TotalesPagos(monto_total_pagos=_amount("250.50"))


def test_totales_without_monto_total_pagos_stays_none() -> None:
    doc = _built_p(pagos=RawPagos20(version="2.0", pagos=(), totales=RawTotalesPagos()))
    assert doc.pagos is not None
    assert doc.pagos.totales == TotalesPagos(monto_total_pagos=None)


def test_re_complement_with_no_payments_is_still_promoted() -> None:
    doc = _built_p(pagos=RawPagos20(version="2.0"))
    assert doc.pagos == Pagos20(version="2.0")


# --- type-P Moneda=XXX ------------------------------------------------------


def test_type_p_xxx_header_nulls_the_monetary_header_facts() -> None:
    doc = _doc(_SIMPLE_P)
    assert doc.moneda == "XXX"
    assert doc.total is None
    assert doc.subtotal is None
    assert doc.descuento is None
    assert doc.conceptos == ()


def test_type_p_xxx_header_opens_no_flag() -> None:
    """XXX on a P comprobante is a defined CFDI condition, not an exotic currency."""
    result = _build(_SIMPLE_P)[1]
    assert result.outcome is ParseOutcome.PARSED
    assert result.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is False
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 0


def test_type_p_xxx_receipt_still_promotes_re_currency_and_structure() -> None:
    """The XXX header must not suppress the REP facts it makes possible."""
    doc = _doc(_SIMPLE_P)
    assert doc.pagos is not None
    assert doc.pagos.version == "2.0"
    assert doc.pagos.pagos[0].moneda_p == "MXN"
    assert doc.pagos.pagos[0].monto == _amount("116.00")


def test_type_p_xxx_header_keeps_non_monetary_header_facts() -> None:
    doc = _doc(_DETALLADO)
    assert doc.regimen_fiscal_receptor == "601"
    assert doc.source_uuid == Uuid(_UUID_SIMPLE)


def test_type_p_xxx_with_zero_header_amounts_is_parsed_without_flag() -> None:
    """The valid condition: zero (or absent) header amounts, so nothing is discarded."""
    result = _build_p(_p_receipt())
    assert result.outcome is ParseOutcome.PARSED
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 0
    doc = result.document
    assert doc is not None
    assert doc.total is None
    assert doc.subtotal is None
    assert doc.descuento is None


def test_type_p_xxx_with_explicit_zero_amounts_stays_non_monetary() -> None:
    """Explicit "0.00" is the same condition as an absent amount."""
    doc = _built_p(total="0.00", subtotal="0.00", descuento="0.00")
    assert doc.total is None
    assert doc.subtotal is None
    assert doc.descuento is None


def test_type_p_xxx_with_non_zero_total_is_reviewed_not_discarded() -> None:
    """A non-zero total contradicts the XXX condition: the header is not non-monetary."""
    result = _build_p(_p_receipt(total="100.00"))
    assert result.outcome is ParseOutcome.PARTIAL
    flags = result.review_flags.open_of(ReviewFlagType.UNSUPPORTED_CURRENCY)
    assert len(flags) == 1
    assert flags[0].reason == "unsupported currency"  # existing reason, no new flag type


def test_type_p_xxx_with_non_zero_subtotal_is_reviewed() -> None:
    result = _build_p(_p_receipt(subtotal="100.00"))
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1


def test_type_p_xxx_with_non_zero_descuento_is_reviewed() -> None:
    result = _build_p(_p_receipt(descuento="10.00"))
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1


def test_contradictory_p_xxx_header_keeps_the_rep_facts_it_carries() -> None:
    """Only the unnormalizable header amounts become None; the REP structure survives."""
    doc = _built_p(
        total="100.00",
        pagos=_pagos_raw(_pago(moneda_p="MXN", monto="116.00", doctos_relacionados=(_docto(),))),
    )
    assert doc.moneda == "XXX"  # the contradictory source header fact is not rewritten
    assert doc.pagos is not None
    assert doc.pagos.pagos[0].monto == _amount("116.00")
    assert doc.pagos.pagos[0].doctos_relacionados[0].imp_pagado == _amount("116.00")


def test_contradictory_p_xxx_source_stays_recoverable_from_the_raw_cfd() -> None:
    """The typed layer refuses to normalize; it never rewrites the source facts.

    `RawCfd` (what the infra parser deserializes) is the audit trail: the values a
    posting consumer cannot see as a Decimal here are still verbatim in the source.
    """
    xml = (
        (_FIXTURES / _SIMPLE_P)
        .read_bytes()
        .replace(b'Moneda="XXX" Total="0"', b'Moneda="XXX" Total="100.00"')
    )
    raw = _parser.parse(xml, _SOURCE_HASH)
    assert raw.moneda == "XXX"  # the contradictory header, kept as sourced
    assert raw.total == "100.00"
    assert raw.subtotal == "0"

    result = build_fiscal_document(raw, _SOURCE_HASH)
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    doc = result.document
    assert doc is not None
    assert doc.moneda == "XXX"
    assert doc.total is None  # refused, never rewritten as a number
    assert doc.subtotal is None
    assert doc.pagos is not None  # and the REP facts are still promoted


def test_contradictory_p_xxx_header_plus_bad_rep_currency_opens_one_flag() -> None:
    """One UNSUPPORTED_CURRENCY per document: the header failure reports first."""
    result = _build_p(
        _p_receipt(total="100.00", pagos=_pagos_raw(_pago(moneda_p="GBP", monto="1.00")))
    )
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1


def test_non_p_xxx_header_is_still_an_unsupported_currency() -> None:
    """The XXX exception is scoped to type P; an I comprobante in XXX is still reviewed."""
    result = _build_p(_raw(moneda="XXX", total="0"))
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1


def test_type_p_xxx_with_unsupported_moneda_p_opens_exactly_one_flag() -> None:
    result = _build_p(_p_receipt(pagos=_pagos_raw(_pago(moneda_p="GBP", monto="100.00"))))
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    assert result.document is not None
    assert result.document.total is None  # the header is still non-monetary


def test_unsupported_rep_currency_nulls_only_the_rep_amounts() -> None:
    doc = _built_p(
        pagos=_pagos_raw(_pago(moneda_p="GBP", monto="100.00", doctos_relacionados=(_docto(),)))
    )
    assert doc.pagos is not None
    payment = doc.pagos.pagos[0]
    assert payment.monto is None
    assert payment.moneda_p == "GBP"  # the source fact is still recorded
    assert payment.doctos_relacionados[0].imp_pagado == _amount("116.00")  # MXN, unaffected


def test_unsupported_rep_currency_keeps_the_source_structure_with_one_flag() -> None:
    """Every REP amount in the offending currency becomes None; nothing else is lost."""
    result = _build_p(
        _p_receipt(
            pagos=_pagos_raw(
                _pago(
                    moneda_p="GBP",
                    monto="100.00",
                    num_operacion="OP-9",
                    doctos_relacionados=(_docto(moneda_dr="GBP", serie="A", folio="7"),),
                )
            )
        )
    )
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    doc = result.document
    assert doc is not None
    assert doc.pagos is not None
    pago = doc.pagos.pagos[0]
    assert (pago.fecha_pago, pago.forma_de_pago_p, pago.moneda_p, pago.num_operacion) == (
        "2024-02-10T10:30:00+00:00",
        "03",
        "GBP",
        "OP-9",
    )
    assert pago.monto is None
    docto = pago.doctos_relacionados[0]
    assert docto.uuid == Uuid(_UUID_1)
    assert (docto.moneda_dr, docto.serie, docto.folio) == ("GBP", "A", "7")
    assert docto.num_parcialidad == 1
    assert docto.objeto_imp_dr == "02"
    assert docto.imp_saldo_ant is None
    assert docto.imp_pagado is None
    assert docto.imp_saldo_insoluto is None


def test_each_rep_level_uses_its_own_currency() -> None:
    """MonedaP and MonedaDR are independent: USD payment, MXN related document."""
    doc = _built_p(
        pagos=_pagos_raw(_pago(moneda_p="USD", monto="100.00", doctos_relacionados=(_docto(),)))
    )
    assert doc.pagos is not None
    payment = doc.pagos.pagos[0]
    assert payment.moneda_p == "USD"
    assert payment.monto == _amount("100.00")
    assert payment.doctos_relacionados[0].moneda_dr == "MXN"
    assert payment.doctos_relacionados[0].imp_pagado == _amount("116.00")


def test_supported_header_with_unsupported_rep_currency_flags() -> None:
    """A supported header does not vouch for MonedaP/MonedaDR inside the complemento."""
    raw = _raw(pagos=_pagos_raw(_pago(moneda_p="GBP", monto="100.00")))
    result = _build_p(raw)
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    assert result.document is not None
    assert result.document.total == _amount("116.00")  # the header still normalized


def test_non_p_unsupported_currency_keeps_its_generic_behaviour() -> None:
    """M2.4b corrects XXX-on-P only; the generic unsupported-currency path is unchanged."""
    result = _build_p(_raw(moneda="EUR", total="10.00"))
    assert result.outcome is ParseOutcome.PARTIAL
    assert result.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 1
    assert result.document is not None
    assert result.document.total is None
    assert result.document.pagos is None


# --- no accounting interpretation -------------------------------------------


def test_inconsistent_balance_is_promoted_verbatim_without_any_flag() -> None:
    """ImpSaldoAnt - ImpPagado != ImpSaldoInsoluto is an M3 invariant, not an M2 check."""
    doc = _built_from(
        _p_receipt(
            pagos=_pagos_raw(
                _pago(
                    doctos_relacionados=(
                        _docto(
                            imp_saldo_ant="200.00", imp_pagado="50.00", imp_saldo_insoluto="7.77"
                        ),
                    )
                )
            )
        )
    )
    docto = doc.pagos.pagos[0].doctos_relacionados[0]
    assert docto.imp_saldo_ant == _amount("200.00")
    assert docto.imp_pagado == _amount("50.00")
    assert docto.imp_saldo_insoluto == _amount("7.77")  # no rejection, no correction
    assert doc.review_flags.count(ReviewFlagType.UNSUPPORTED_CURRENCY) == 0


def test_no_fx_conversion_is_applied_to_rep_amounts() -> None:
    """USD 100 with TipoCambioP 17.5000 stays 100.00, not 1750.00."""
    payment = _pagos(_DETALLADO).pagos[1]
    assert payment.monto == _amount("100.00")
    assert payment.monto != _amount("1750.00")


def test_new_types_carry_no_derived_accounting_fields() -> None:
    """Every field is a source fact: no accounting base, IVA transfer or tax total."""
    assert [f.name for f in fields(TrasladoDR)] == [
        "base_dr",
        "impuesto_dr",
        "tipo_factor_dr",
        "tasa_o_cuota_dr",
        "importe_dr",
    ]
    assert [f.name for f in fields(RetencionDR)] == [
        "base_dr",
        "impuesto_dr",
        "tipo_factor_dr",
        "tasa_o_cuota_dr",
        "importe_dr",
    ]
    assert [f.name for f in fields(ImpuestosDR)] == ["traslados_dr", "retenciones_dr"]
    assert [f.name for f in fields(TrasladoP)] == [
        "base_p",
        "impuesto_p",
        "tipo_factor_p",
        "tasa_o_cuota_p",
        "importe_p",
    ]
    assert [f.name for f in fields(RetencionP)] == ["impuesto_p", "importe_p"]
    assert [f.name for f in fields(ImpuestosP)] == ["traslados_p", "retenciones_p"]
    assert [f.name for f in fields(DoctoRelacionado)] == [
        "uuid",
        "moneda_dr",
        "num_parcialidad",
        "imp_saldo_ant",
        "imp_pagado",
        "imp_saldo_insoluto",
        "objeto_imp_dr",
        "equivalencia_dr",
        "serie",
        "folio",
        "impuestos_dr",
    ]
    assert [f.name for f in fields(Pago)] == [
        "fecha_pago",
        "forma_de_pago_p",
        "moneda_p",
        "monto",
        "tipo_cambio_p",
        "num_operacion",
        "doctos_relacionados",
        "impuestos_p",
    ]
    assert [f.name for f in fields(TotalesPagos)] == ["monto_total_pagos"]
    assert [f.name for f in fields(Pagos20)] == ["version", "pagos", "totales"]


def test_payments_are_never_flattened_into_one() -> None:
    pagos = _pagos(_DETALLADO)
    assert len(pagos.pagos) == 2
    assert pagos.pagos[0].monto != pagos.pagos[1].monto


# --- backward compatibility -------------------------------------------------


def test_pagos_is_defaulted() -> None:
    by_name = {f.name: f for f in fields(FiscalDocument)}
    assert by_name["pagos"].default is None
    assert by_name["pagos"].default is not MISSING


def test_pagos_is_the_final_field() -> None:
    assert [f.name for f in fields(FiscalDocument)][-1] == "pagos"


def test_existing_field_positions_are_unchanged() -> None:
    names = [f.name for f in fields(FiscalDocument)]
    assert names[:10] == [
        "tipo",
        "version",
        "moneda",
        "tipo_cambio",
        "emisor_rfc",
        "receptor_rfc",
        "conceptos",
        "impuestos",
        "total",
        "source_hash",
    ]
    assert names[10:12] == ["status", "review_flags"]


def test_construction_without_pagos_remains_valid() -> None:
    doc = FiscalDocument(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc=Rfc("AAA010101AAA"),
        receptor_rfc=None,
        conceptos=(),
        impuestos=Impuestos(),
        total=None,
        source_hash="abc",
    )
    assert doc.pagos is None
    assert doc.source_uuid is None
    assert doc.cfdi_relacionados == ()
