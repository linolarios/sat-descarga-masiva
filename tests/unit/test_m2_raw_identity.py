"""M2.3b: raw CFDI identity, relation and REP preservation (AGENT.md §11 / §7a).

The parser must preserve source facts (UUID, relations, REP structure) without
interpreting them: no accounting meaning, no aggregation, no fabrication.
"""

from pathlib import Path

from sat_descarga_masiva.domain.model.raw_cfd import RawCfd, RawImpuestos, RawPagos20
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SOURCE_HASH = "a" * 64

_TFD_UUID = "123e4567-e89b-12d3-a456-426614174000"
_RELACION_UUID_1 = "11111111-1111-1111-1111-111111111111"
_RELACION_UUID_2 = "22222222-2222-2222-2222-222222222222"
_PAGO_UUID_1 = "33333333-3333-3333-3333-333333333333"
_PAGO_UUID_2 = "44444444-4444-4444-4444-444444444444"
_PAGO_UUID_3 = "55555555-5555-5555-5555-555555555555"

_parser = SatcfdiFiscalParser()


def _parse(name: str) -> RawCfd:
    return _parser.parse((_FIXTURES / name).read_bytes(), _SOURCE_HASH)


def _pagos_detallados() -> RawPagos20:
    pagos = _parse("cfdi_pago_detallado_4_0.xml").pagos
    assert pagos is not None
    return pagos


# --- identity -------------------------------------------------------------


def test_uuid_is_extracted_from_the_timbre_fiscal_digital() -> None:
    assert _parse("cfdi_tfd_4_0.xml").uuid == _TFD_UUID


def test_document_without_tfd_has_no_uuid() -> None:
    """The raw layer records the absence; it never invents an identity."""
    assert _parse("cfdi_ingreso_4_0.xml").uuid is None


def test_uuid_is_not_derived_from_hash_or_filename() -> None:
    raw = _parse("cfdi_ingreso_4_0.xml")
    assert raw.uuid is None
    assert raw.uuid != _SOURCE_HASH
    assert raw.uuid != "cfdi_ingreso_4_0"


# --- document fields required by the approved accounting rules -------------


def test_document_fields_are_preserved_as_source_text() -> None:
    raw = _parse("cfdi_ingreso_4_0.xml")
    assert raw.subtotal == "100.00"
    assert raw.forma_pago == "03"
    assert raw.metodo_pago == "PUE"
    assert raw.regimen_fiscal_receptor == "601"
    assert raw.descuento is None


def test_document_descuento_is_preserved_when_present() -> None:
    raw = _parse("cfdi_egreso_4_0.xml")
    assert raw.subtotal == "100.00"
    assert raw.descuento == "10.00"


def test_absent_optional_document_field_stays_none() -> None:
    """A CFDI 3.3 source carries no RegimenFiscalReceptor: None, not a default."""
    raw = _parse("cfdi_relacion_3_3.xml")
    assert raw.version == "3.3"
    assert raw.regimen_fiscal_receptor is None


# --- relations ------------------------------------------------------------


def test_document_without_relations_has_an_empty_tuple() -> None:
    assert _parse("cfdi_ingreso_4_0.xml").cfdi_relacionados == ()


def test_relation_preserves_tipo_relacion_as_the_bare_catalog_code() -> None:
    relacion = _parse("cfdi_relacion_4_0.xml").cfdi_relacionados[0]
    assert relacion.tipo_relacion == "01"
    assert "Nota" not in relacion.tipo_relacion


def test_relation_preserves_every_related_uuid_in_source_order() -> None:
    relacion = _parse("cfdi_relacion_4_0.xml").cfdi_relacionados[0]
    assert relacion.uuids == (_RELACION_UUID_1, _RELACION_UUID_2)


def test_relation_is_not_flattened_to_a_single_uuid() -> None:
    relacion = _parse("cfdi_relacion_4_0.xml").cfdi_relacionados[0]
    assert len(relacion.uuids) == 2
    assert _RELACION_UUID_1 in relacion.uuids
    assert _RELACION_UUID_2 in relacion.uuids


def test_cfdi_33_scalar_relation_container_normalizes_like_cfdi_40() -> None:
    """satcfdi yields a scalar container for 3.3 and a list for 4.0."""
    raw_33 = _parse("cfdi_relacion_3_3.xml")
    raw_40 = _parse("cfdi_relacion_4_0.xml")
    assert raw_33.version == "3.3"
    assert raw_40.version == "4.0"
    assert raw_33.cfdi_relacionados == raw_40.cfdi_relacionados


# --- pago complement ------------------------------------------------------


def test_non_pago_document_carries_no_pagos_complement() -> None:
    assert _parse("cfdi_ingreso_4_0.xml").pagos is None


def test_pago_document_carries_pagos_20() -> None:
    pagos = _pagos_detallados()
    assert pagos.version == "2.0"
    assert _parse("cfdi_pago_detallado_4_0.xml").tipo == "P"


def test_multiple_pagos_are_not_aggregated() -> None:
    pagos = _pagos_detallados()
    assert len(pagos.pagos) == 2
    assert tuple(p.num_operacion for p in pagos.pagos) == ("OP-0001", "OP-0002")


def test_multiple_doctos_relacionados_are_not_flattened() -> None:
    pagos = _pagos_detallados()
    assert tuple(d.id_documento for d in pagos.pagos[0].doctos_relacionados) == (
        _PAGO_UUID_1,
        _PAGO_UUID_2,
    )
    assert tuple(d.id_documento for d in pagos.pagos[1].doctos_relacionados) == (_PAGO_UUID_3,)


def test_payment_fields_are_preserved_as_source_text() -> None:
    pagos = _pagos_detallados()
    first = pagos.pagos[0]
    assert first.fecha_pago == "2024-02-10T10:30:00+00:00"
    assert first.forma_de_pago_p == "03"
    assert first.moneda_p == "MXN"
    assert first.monto == "166.00"
    assert first.tipo_cambio_p == "1"
    assert first.num_operacion == "OP-0001"


def test_payment_date_uses_the_iso_8601_t_separator() -> None:
    """str(datetime) would be space-separated, which is not the SAT source form."""
    fecha = _pagos_detallados().pagos[0].fecha_pago
    assert "T" in fecha
    assert " " not in fecha


def test_each_payment_keeps_its_own_currency_and_exchange_rate() -> None:
    pagos = _pagos_detallados()
    assert pagos.pagos[0].moneda_p == "MXN"
    assert pagos.pagos[1].moneda_p == "USD"
    assert pagos.pagos[1].tipo_cambio_p == "17.5000"
    assert pagos.pagos[1].forma_de_pago_p == "01"
    assert pagos.pagos[1].fecha_pago == "2024-03-10T09:15:00+00:00"


def test_docto_relacionado_balances_keep_the_source_scale() -> None:
    pagos = _pagos_detallados()
    full, partial = pagos.pagos[0].doctos_relacionados
    assert full.num_parcialidad == "1"
    assert full.imp_saldo_ant == "116.00"
    assert full.imp_pagado == "116.00"
    assert full.imp_saldo_insoluto == "0.00"
    assert partial.num_parcialidad == "2"
    assert partial.imp_saldo_ant == "200.00"
    assert partial.imp_pagado == "50.00"
    assert partial.imp_saldo_insoluto == "150.00"


def test_docto_relacionado_optional_fields_stay_none_when_absent() -> None:
    pagos = _pagos_detallados()
    full = pagos.pagos[0].doctos_relacionados[0]
    assert full.serie is None
    assert full.folio is None
    assert full.equivalencia_dr is None


def test_docto_relacionado_optional_fields_are_preserved_when_present() -> None:
    tercero = _pagos_detallados().pagos[1].doctos_relacionados[0]
    assert tercero.moneda_dr == "USD"
    assert tercero.serie == "A"
    assert tercero.folio == "100"


def test_impuestos_dr_is_preserved_per_docto_relacionado() -> None:
    pagos = _pagos_detallados()
    full, partial = pagos.pagos[0].doctos_relacionados
    assert full.impuestos_dr is not None
    assert full.impuestos_dr.traslados_dr[0].base_dr == "100.00"
    assert full.impuestos_dr.traslados_dr[0].impuesto_dr == "002"
    assert full.impuestos_dr.traslados_dr[0].tipo_factor_dr == "Tasa"
    assert full.impuestos_dr.traslados_dr[0].tasa_o_cuota_dr == "0.160000"
    assert full.impuestos_dr.traslados_dr[0].importe_dr == "16.00"
    assert full.impuestos_dr.retenciones_dr == ()
    assert partial.impuestos_dr is not None
    assert partial.impuestos_dr.traslados_dr[0].importe_dr == "8.00"
    assert partial.impuestos_dr.retenciones_dr[0].importe_dr == "5.33"


def test_absent_impuestos_dr_stays_none() -> None:
    assert _pagos_detallados().pagos[1].doctos_relacionados[0].impuestos_dr is None


def test_impuestos_p_is_preserved_without_replacing_impuestos_dr() -> None:
    """Both payment-level and document-level tax facts are kept."""
    first = _pagos_detallados().pagos[0]
    assert first.impuestos_p is not None
    assert first.impuestos_p.traslados_p[0].base_p == "150.00"
    assert first.impuestos_p.traslados_p[0].impuesto_p == "002"
    assert first.impuestos_p.traslados_p[0].tipo_factor_p == "Tasa"
    assert first.impuestos_p.traslados_p[0].tasa_o_cuota_p == "0.160000"
    assert first.impuestos_p.traslados_p[0].importe_p == "24.00"
    assert first.impuestos_p.retenciones_p[0].importe_p == "5.33"
    assert first.doctos_relacionados[0].impuestos_dr is not None


def test_absent_impuestos_p_stays_none() -> None:
    assert _pagos_detallados().pagos[1].impuestos_p is None


def test_pagos_totales_are_preserved() -> None:
    totales = _pagos_detallados().totales
    assert totales is not None
    assert totales.monto_total_pagos == "1916.000000"


# --- backward compatibility of the raw contract ---------------------------


def test_new_raw_fields_default_to_none_or_empty() -> None:
    """The M2.3 contract keeps working for callers that omit the new fields."""
    raw = RawCfd(
        tipo="I",
        version="4.0",
        moneda="MXN",
        tipo_cambio=None,
        emisor_rfc="AAA010101AAA",
        receptor_rfc="BBB010101BBB",
        conceptos=(),
        impuestos=RawImpuestos(),
        total="116.00",
    )
    assert raw.uuid is None
    assert raw.subtotal is None
    assert raw.descuento is None
    assert raw.forma_pago is None
    assert raw.metodo_pago is None
    assert raw.regimen_fiscal_receptor is None
    assert raw.cfdi_relacionados == ()
    assert raw.pagos is None
