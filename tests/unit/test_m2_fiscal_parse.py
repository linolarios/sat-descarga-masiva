"""M2.3: SatcfdiFiscalParser (XML -> RawCfd) + build_fiscal_document pipeline."""

from decimal import Decimal
from pathlib import Path

from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocumentStatus,
    ParseOutcome,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document
from sat_descarga_masiva.infrastructure.fiscal.satcfdi_fiscal_parser import SatcfdiFiscalParser

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SOURCE_HASH = "a" * 64
_parser = SatcfdiFiscalParser()


def _parse(name: str):
    xml = (_FIXTURES / name).read_bytes()
    return _parser.parse(xml, _SOURCE_HASH)


def _parse_build(name: str):
    raw = _parse(name)
    return raw, build_fiscal_document(raw, _SOURCE_HASH)


def test_parser_maps_core_fields_ingreso() -> None:
    raw = _parse("cfdi_ingreso_4_0.xml")
    assert raw.tipo == "I"
    assert raw.version == "4.0"
    assert raw.moneda == "MXN"
    assert raw.emisor_rfc == "AAA010101AAA"
    assert raw.receptor_rfc == "BBB010101BBB"
    assert raw.total == "116.00"
    assert raw.conceptos[0].clave_prod_serv == "01010101"
    assert raw.conceptos[0].valor_unitario == "100.00"
    assert raw.impuestos.traslados[0].impuesto == "002"
    assert raw.impuestos.total_traslados == "16.00"


def test_ingreso_builds_parsed_document() -> None:
    _, result = _parse_build("cfdi_ingreso_4_0.xml")
    assert result.outcome is ParseOutcome.PARSED
    doc = result.document
    assert doc is not None
    assert doc.total == NormalizedAmount(Decimal("116.00"))
    assert doc.status is FiscalDocumentStatus.UNKNOWN
    assert doc.source_hash == _SOURCE_HASH
    assert doc.impuestos.traslados[0].importe == NormalizedAmount(Decimal("16.00"))


def test_egreso_parses_with_descuento() -> None:
    raw, result = _parse_build("cfdi_egreso_4_0.xml")
    assert raw.tipo == "E"
    assert raw.conceptos[0].descuento == "10.00"
    assert result.outcome is ParseOutcome.PARSED
    assert result.document is not None
    assert result.document.total == NormalizedAmount(Decimal("90.00"))


def test_traslado_parses() -> None:
    raw, result = _parse_build("cfdi_traslado_4_0.xml")
    assert raw.tipo == "T"
    assert result.outcome is ParseOutcome.PARSED
    assert result.document is not None
    assert result.document.total == NormalizedAmount(Decimal("100.00"))


def test_nomina_parses() -> None:
    raw, result = _parse_build("cfdi_nomina_4_0.xml")
    assert raw.tipo == "N"
    assert result.outcome is ParseOutcome.PARSED
    assert result.document is not None
    assert result.document.total == NormalizedAmount(Decimal("8500.00"))


def test_pago_receipt_xxx_header_is_not_an_unsupported_currency() -> None:
    """M2.4b: CFDI mandates Moneda=XXX on a type-P comprobante, which has no total."""
    raw, result = _parse_build("cfdi_pago_4_0.xml")
    assert raw.tipo == "P"
    assert raw.moneda == "XXX"
    assert result.outcome is ParseOutcome.PARSED
    doc = result.document
    assert doc is not None
    assert doc.total is None
    assert doc.subtotal is None
    assert doc.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is False


def test_unsupported_currency_eur_flags_and_nulls_total() -> None:
    raw, result = _parse_build("cfdi_eur_4_0.xml")
    assert raw.moneda == "EUR"
    assert raw.tipo_cambio == "17.8456"
    assert result.outcome is ParseOutcome.PARTIAL
    doc = result.document
    assert doc is not None
    assert doc.total is None
    assert doc.status is FiscalDocumentStatus.UNKNOWN
    assert doc.tipo_cambio == Decimal("17.8456")
    assert doc.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is True


def test_3_3_unsupported_returns_failed() -> None:
    raw, result = _parse_build("cfdi_ingreso_3_3.xml")
    assert raw.version == "3.3"
    assert result.outcome is ParseOutcome.FAILED
    assert result.document is None
