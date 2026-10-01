"""M2.3: build_fiscal_document — RawCfd -> FiscalDocument via MoneyPolicy."""

from datetime import date
from decimal import Decimal

from sat_descarga_masiva.domain.model.fiscal_document import (
    FiscalDocumentStatus,
    ParseOutcome,
)
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawImpuestos,
    RawRetencion,
    RawTraslado,
)
from sat_descarga_masiva.domain.model.review import ReviewFlagType
from sat_descarga_masiva.domain.policy.money import NormalizedAmount
from sat_descarga_masiva.fiscal.parse import build_fiscal_document

SOURCE_HASH = "abc"


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
            traslados=(RawTraslado("002", "Tasa", "0.16", "16.00"),),
            retenciones=(),
            total_traslados="16.00",
            total_retenciones=None,
        ),
        total="116.00",
    )
    base.update(overrides)
    return RawCfd(**base)  # type: ignore[arg-type]


def test_parsed_document_applies_moneypolicy_and_keeps_unknown() -> None:
    result = build_fiscal_document(_raw(), SOURCE_HASH)
    assert result.outcome is ParseOutcome.PARSED
    doc = result.document
    assert doc is not None
    assert doc.tipo == "I"
    assert doc.version == "4.0"
    assert doc.total == NormalizedAmount(Decimal("116.00"))
    assert doc.conceptos[0].importe == NormalizedAmount(Decimal("100.00"))
    assert doc.impuestos.traslados[0].importe == NormalizedAmount(Decimal("16.00"))
    assert doc.status is FiscalDocumentStatus.UNKNOWN
    assert doc.source_hash == SOURCE_HASH
    assert doc.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is False


def test_unsupported_version_3_3_is_failed_and_no_document() -> None:
    result = build_fiscal_document(_raw(version="3.3"), SOURCE_HASH)
    assert result.outcome is ParseOutcome.FAILED
    assert result.document is None


def test_unsupported_currency_opens_flag_and_nulls_total() -> None:
    result = build_fiscal_document(_raw(moneda="EUR", total="10.00"), SOURCE_HASH)
    assert result.outcome is ParseOutcome.PARTIAL
    doc = result.document
    assert doc is not None
    assert doc.total is None
    assert doc.status is FiscalDocumentStatus.UNKNOWN
    assert doc.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is True
    assert result.review_flags.has_open(ReviewFlagType.UNSUPPORTED_CURRENCY) is True
    assert doc.conceptos == ()  # no unnormalized monetary values retained


def test_tipo_cambio_preserved_with_source_precision() -> None:
    result = build_fiscal_document(_raw(tipo_cambio="17.8456"), SOURCE_HASH)
    doc = result.document
    assert doc is not None
    assert doc.tipo_cambio == Decimal("17.8456")  # not quantized to 2


def test_amounts_round_half_up() -> None:
    result = build_fiscal_document(_raw(total="116.005"), SOURCE_HASH)
    doc = result.document
    assert doc is not None
    assert doc.total == NormalizedAmount(Decimal("116.01"))


def test_retention_amounts_normalized() -> None:
    raw = _raw(
        impuestos=RawImpuestos(
            traslados=(),
            retenciones=(RawRetencion("001", "0.10", "10.00"),),
            total_traslados=None,
            total_retenciones="10.00",
        )
    )
    result = build_fiscal_document(raw, SOURCE_HASH)
    doc = result.document
    assert doc is not None
    assert doc.impuestos.retenciones[0].importe == NormalizedAmount(Decimal("10.00"))


# --- the header Fecha (§8a:209) ---------------------------------------------


def test_fecha_is_dated_from_the_source_text() -> None:
    """`Fecha` becomes the calendar date a journal entry is dated by — offset and all."""
    doc = build_fiscal_document(_raw(fecha="2026-01-15T12:00:00+00:00"), SOURCE_HASH).document
    assert doc is not None
    assert doc.fecha == date(2026, 1, 15)


def test_the_written_calendar_date_survives_a_source_offset() -> None:
    """The offset is not applied: a journal date is the document's date, not a UTC instant."""
    doc = build_fiscal_document(_raw(fecha="2026-01-15T23:30:00-06:00"), SOURCE_HASH).document
    assert doc is not None
    assert doc.fecha == date(2026, 1, 15)  # 2026-01-16 in UTC, and not what a ledger wants


def test_an_absent_or_malformed_fecha_stays_absent() -> None:
    """No date is invented here: an undatable document is a downstream review case (§8:159)."""
    for fecha in (None, "", "2026-13-45", "not a date"):
        doc = build_fiscal_document(_raw(fecha=fecha), SOURCE_HASH).document
        assert doc is not None
        assert doc.fecha is None
