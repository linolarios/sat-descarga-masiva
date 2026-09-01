"""Fiscal build policy: RawCfd -> FiscalDocument via MoneyPolicy (M2.3).

Owns fiscal interpretation: supported-version check, MoneyPolicy application,
review flags, and fiscal status. The infra parser only deserializes XML.
"""

from __future__ import annotations

from decimal import Decimal

from sat_descarga_masiva.domain.model.fiscal_document import (
    Concepto,
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalParseResult,
    Impuestos,
    ParseOutcome,
    Retencion,
    Traslado,
)
from sat_descarga_masiva.domain.model.raw_cfd import (
    RawCfd,
    RawConcepto,
    RawImpuestos,
)
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlags, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.domain.policy.money import (
    MoneyPolicy,
    NormalizedAmount,
    UnsupportedCurrency,
)


def build_fiscal_document(
    raw: RawCfd, source_hash: str, money: MoneyPolicy | None = None
) -> FiscalParseResult:
    """Translate a raw CFDI into a typed FiscalDocument (CFDI 4.0 only)."""
    policy = money if money is not None else MoneyPolicy()
    if raw.version != "4.0":
        return FiscalParseResult(outcome=ParseOutcome.FAILED, document=None)

    total = policy.normalize(raw.total, raw.moneda)
    if isinstance(total, UnsupportedCurrency):
        flag = ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, total.reason)
        flags = ReviewFlags().with_flag(flag)
        document = FiscalDocument(
            tipo=raw.tipo,
            version=raw.version,
            moneda=raw.moneda,
            tipo_cambio=_decimal(raw.tipo_cambio),
            emisor_rfc=Rfc(raw.emisor_rfc),
            receptor_rfc=Rfc(raw.receptor_rfc) if raw.receptor_rfc is not None else None,
            conceptos=(),
            impuestos=Impuestos(),
            total=None,  # cannot be financially normalized; never zero, never unnormalized
            status=FiscalDocumentStatus.UNKNOWN,
            review_flags=flags,
            source_hash=source_hash,
        )
        return FiscalParseResult(
            outcome=ParseOutcome.PARTIAL, document=document, review_flags=flags
        )

    conceptos = _build_conceptos(raw.conceptos, raw.moneda, policy)
    impuestos = _build_impuestos(raw.impuestos, raw.moneda, policy)
    document = FiscalDocument(
        tipo=raw.tipo,
        version=raw.version,
        moneda=raw.moneda,
        tipo_cambio=_decimal(raw.tipo_cambio),
        emisor_rfc=Rfc(raw.emisor_rfc),
        receptor_rfc=Rfc(raw.receptor_rfc) if raw.receptor_rfc is not None else None,
        conceptos=conceptos,
        impuestos=impuestos,
        total=total,
        status=FiscalDocumentStatus.UNKNOWN,
        review_flags=ReviewFlags(),
        source_hash=source_hash,
    )
    return FiscalParseResult(outcome=ParseOutcome.PARSED, document=document)


def _decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _amount(value: str, currency: str, policy: MoneyPolicy) -> NormalizedAmount:
    result = policy.normalize(value, currency)
    # Currency was already validated via total; per-amount must normalize.
    if isinstance(result, UnsupportedCurrency):
        raise AssertionError("supported-currency document hit an unsupported amount")
    return result


def _build_conceptos(
    raw: tuple[RawConcepto, ...], currency: str, policy: MoneyPolicy
) -> tuple[Concepto, ...]:
    return tuple(
        Concepto(
            clave_prod_serv=c.clave_prod_serv,
            cantidad=Decimal(c.cantidad),
            valor_unitario=_amount(c.valor_unitario, currency, policy),
            importe=_amount(c.importe, currency, policy),
            descuento=_amount(c.descuento, currency, policy) if c.descuento is not None else None,
        )
        for c in raw
    )


def _build_impuestos(raw: RawImpuestos, currency: str, policy: MoneyPolicy) -> Impuestos:
    traslados = tuple(
        Traslado(
            impuesto=t.impuesto,
            tipo_factor=t.tipo_factor,
            tasa_o_cuota=_decimal(t.tasa_o_cuota),
            importe=_amount(t.importe, currency, policy) if t.importe is not None else None,
        )
        for t in raw.traslados
    )
    retenciones = tuple(
        Retencion(
            impuesto=r.impuesto,
            tasa_o_cuota=Decimal(r.tasa_o_cuota),
            importe=_amount(r.importe, currency, policy),
        )
        for r in raw.retenciones
    )
    return Impuestos(
        traslados=traslados,
        retenciones=retenciones,
        total_traslados=_amount(raw.total_traslados, currency, policy)
        if raw.total_traslados is not None
        else None,
        total_retenciones=_amount(raw.total_retenciones, currency, policy)
        if raw.total_retenciones is not None
        else None,
    )
