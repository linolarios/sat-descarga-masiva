"""Fiscal build policy: RawCfd -> FiscalDocument via MoneyPolicy (M2.3).

Owns fiscal interpretation: supported-version check, MoneyPolicy application,
review flags, and fiscal status. The infra parser only deserializes XML.
"""

from __future__ import annotations

from decimal import Decimal

from sat_descarga_masiva.domain.model.fiscal_document import (
    CfdiRelacionados,
    Concepto,
    DoctoRelacionado,
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalParseResult,
    Impuestos,
    ImpuestosDR,
    ImpuestosP,
    Pago,
    Pagos20,
    ParseOutcome,
    Retencion,
    RetencionDR,
    RetencionP,
    TotalesPagos,
    Traslado,
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
from sat_descarga_masiva.domain.model.review import ReviewFlag, ReviewFlags, ReviewFlagType
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import (
    MoneyPolicy,
    NormalizedAmount,
    UnsupportedCurrency,
)

_NO_CURRENCY = "XXX"  # ISO 4217 "no currency", mandated on type-P comprobantes
_NATIONAL_CURRENCY = "MXN"  # REP 2.0 expresses Totales in national currency


def build_fiscal_document(
    raw: RawCfd, source_hash: str, money: MoneyPolicy | None = None
) -> FiscalParseResult:
    """Translate a raw CFDI into a typed FiscalDocument (CFDI 4.0 only)."""
    policy = money if money is not None else MoneyPolicy()
    if raw.version != "4.0":
        return FiscalParseResult(outcome=ParseOutcome.FAILED, document=None)

    # Identity, header and REP facts are non-monetary: they survive a header
    # currency that cannot be normalized.
    source_uuid = _source_uuid(raw)
    relations = _cfdi_relacionados(raw)
    pagos, rep_currency_reason = _build_pagos(raw.pagos, policy)

    if _is_non_monetary_header(raw):
        # SubTotal=0/Total=0 under the ISO "no currency" code XXX is required on a
        # type-P comprobante: a payment has no monetary total of its own. That is a
        # defined CFDI condition, not an unsupported currency, so it opens no flag.
        # The predicate verifies those zero amounts first, so a contradictory P+XXX
        # header falls through to the ordinary review path below rather than having
        # its monetary facts dropped here.
        flags = _currency_flags(rep_currency_reason)
        document = FiscalDocument(
            tipo=raw.tipo,
            version=raw.version,
            moneda=raw.moneda,
            tipo_cambio=_decimal(raw.tipo_cambio),
            emisor_rfc=Rfc(raw.emisor_rfc),
            receptor_rfc=Rfc(raw.receptor_rfc) if raw.receptor_rfc is not None else None,
            conceptos=(),
            impuestos=Impuestos(),
            total=None,  # a type-P comprobante has no monetary total of its own
            status=FiscalDocumentStatus.UNKNOWN,
            review_flags=flags,
            source_hash=source_hash,
            source_uuid=source_uuid,
            subtotal=None,  # nor any monetary subtotal
            descuento=None,
            forma_pago=raw.forma_pago,
            metodo_pago=raw.metodo_pago,
            regimen_fiscal_receptor=raw.regimen_fiscal_receptor,
            cfdi_relacionados=relations,
            pagos=pagos,
        )
        return FiscalParseResult(
            outcome=(
                ParseOutcome.PARTIAL if rep_currency_reason is not None else ParseOutcome.PARSED
            ),
            document=document,
            review_flags=flags,
        )

    total = policy.normalize(raw.total, raw.moneda)
    if isinstance(total, UnsupportedCurrency):
        # The header failure reports first: one UNSUPPORTED_CURRENCY flag per document.
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
            source_uuid=source_uuid,
            subtotal=None,  # unnormalizable under an unsupported currency
            descuento=None,
            forma_pago=raw.forma_pago,
            metodo_pago=raw.metodo_pago,
            regimen_fiscal_receptor=raw.regimen_fiscal_receptor,
            cfdi_relacionados=relations,
            pagos=pagos,
        )
        return FiscalParseResult(
            outcome=ParseOutcome.PARTIAL, document=document, review_flags=flags
        )

    conceptos = _build_conceptos(raw.conceptos, raw.moneda, policy)
    impuestos = _build_impuestos(raw.impuestos, raw.moneda, policy)
    # A supported header can still carry an unsupported MonedaP/MonedaDR in its REP.
    flags = _currency_flags(rep_currency_reason)
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
        review_flags=flags,
        source_hash=source_hash,
        source_uuid=source_uuid,
        subtotal=_optional_amount(raw.subtotal, raw.moneda, policy),
        descuento=_optional_amount(raw.descuento, raw.moneda, policy),
        forma_pago=raw.forma_pago,
        metodo_pago=raw.metodo_pago,
        regimen_fiscal_receptor=raw.regimen_fiscal_receptor,
        cfdi_relacionados=relations,
        pagos=pagos,
    )
    return FiscalParseResult(
        outcome=ParseOutcome.PARTIAL if rep_currency_reason is not None else ParseOutcome.PARSED,
        document=document,
        review_flags=flags,
    )


def _source_uuid(raw: RawCfd) -> Uuid | None:
    """The TFD UUID, canonicalized. Never the source hash, filename or a generated id."""
    return Uuid(raw.uuid) if raw.uuid is not None else None


def _is_non_monetary_header(raw: RawCfd) -> bool:
    """True for a type-P comprobante whose header is the CFDI "no total" condition.

    CFDI mandates SubTotal=0/Total=0 with Moneda=XXX on a payment receipt, because a
    REP has no monetary total: the tax-relevant amounts live in the Pagos 2.0
    complemento. Treating that as an exotic currency would flag every P comprobante
    and make it unpostable downstream.

    The zero amounts are part of the condition, not decoration: a P+XXX header that
    carries a non-zero amount contradicts it and is therefore NOT non-monetary — it
    must reach the ordinary review path rather than have those facts dropped here.
    """
    return (
        raw.tipo == "P"
        and raw.moneda.upper() == _NO_CURRENCY
        and all(_is_zero_amount(value) for value in (raw.total, raw.subtotal, raw.descuento))
    )


def _is_zero_amount(value: str | None) -> bool:
    """A header monetary fact that is absent, or present as exactly zero."""
    return value is None or Decimal(value) == 0


def _currency_flags(reason: str | None) -> ReviewFlags:
    """At most one UNSUPPORTED_CURRENCY flag: with_flag appends without deduping."""
    if reason is None:
        return ReviewFlags()
    return ReviewFlags().with_flag(ReviewFlag(ReviewFlagType.UNSUPPORTED_CURRENCY, reason))


def _cfdi_relacionados(raw: RawCfd) -> tuple[CfdiRelacionados, ...]:
    """Promote relation groups verbatim: group/uuid order kept, TipoRelacion as code."""
    return tuple(
        CfdiRelacionados(
            tipo_relacion=group.tipo_relacion,
            uuids=tuple(Uuid(value) for value in group.uuids),
        )
        for group in raw.cfdi_relacionados
    )


def _optional_amount(
    value: str | None, currency: str, policy: MoneyPolicy
) -> NormalizedAmount | None:
    """An optional monetary fact. Absent stays absent; the caller guarantees support."""
    return _amount(value, currency, policy) if value is not None else None


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


def _build_pagos(raw: RawPagos20 | None, policy: MoneyPolicy) -> tuple[Pagos20 | None, str | None]:
    """Promote the REP tree, plus the reason for a currency the policy cannot normalize.

    REP amounts are denominated in MonedaP / MonedaDR, which are independent of the
    comprobante header currency, so they are normalized against their own currency and
    an unsupported one yields None amounts plus a single document-level flag.
    """
    if raw is None:
        return None, None
    unsupported: list[str] = []
    pagos = Pagos20(
        version=raw.version,
        pagos=tuple(_build_pago(p, policy, unsupported) for p in raw.pagos),
        totales=_build_totales(raw.totales, policy, unsupported),
    )
    reason = f"unsupported REP currency {unsupported[0]}" if unsupported else None
    return pagos, reason


def _build_totales(
    raw: RawTotalesPagos | None, policy: MoneyPolicy, unsupported: list[str]
) -> TotalesPagos | None:
    if raw is None:
        return None
    return TotalesPagos(
        monto_total_pagos=_rep_optional_amount(
            raw.monto_total_pagos, _NATIONAL_CURRENCY, policy, unsupported
        ),
    )


def _build_pago(raw: RawPago, policy: MoneyPolicy, unsupported: list[str]) -> Pago:
    return Pago(
        fecha_pago=raw.fecha_pago,
        forma_de_pago_p=raw.forma_de_pago_p,
        moneda_p=raw.moneda_p,
        monto=_rep_amount(raw.monto, raw.moneda_p, policy, unsupported),
        tipo_cambio_p=_decimal(raw.tipo_cambio_p),
        num_operacion=raw.num_operacion,
        doctos_relacionados=tuple(
            _build_docto_relacionado(d, policy, unsupported) for d in raw.doctos_relacionados
        ),
        impuestos_p=_build_impuestos_p(raw.impuestos_p, raw.moneda_p, policy, unsupported),
    )


def _build_docto_relacionado(
    raw: RawDoctoRelacionado, policy: MoneyPolicy, unsupported: list[str]
) -> DoctoRelacionado:
    """Promote one related document verbatim: no balance invariant is checked here."""
    currency = raw.moneda_dr
    return DoctoRelacionado(
        uuid=Uuid(raw.id_documento),
        moneda_dr=currency,
        num_parcialidad=int(raw.num_parcialidad),
        imp_saldo_ant=_rep_amount(raw.imp_saldo_ant, currency, policy, unsupported),
        imp_pagado=_rep_amount(raw.imp_pagado, currency, policy, unsupported),
        imp_saldo_insoluto=_rep_optional_amount(
            raw.imp_saldo_insoluto, currency, policy, unsupported
        ),
        objeto_imp_dr=raw.objeto_imp_dr,
        equivalencia_dr=_decimal(raw.equivalencia_dr),
        serie=raw.serie,
        folio=raw.folio,
        impuestos_dr=_build_impuestos_dr(raw.impuestos_dr, currency, policy, unsupported),
    )


def _build_impuestos_dr(
    raw: RawImpuestosDR | None, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> ImpuestosDR | None:
    """Document-level taxes, every element kept in source order: no aggregation."""
    if raw is None:
        return None
    return ImpuestosDR(
        traslados_dr=tuple(
            _build_traslado_dr(t, currency, policy, unsupported) for t in raw.traslados_dr
        ),
        retenciones_dr=tuple(
            _build_retencion_dr(r, currency, policy, unsupported) for r in raw.retenciones_dr
        ),
    )


def _build_traslado_dr(
    raw: RawTrasladoDR, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> TrasladoDR:
    return TrasladoDR(
        base_dr=_rep_amount(raw.base_dr, currency, policy, unsupported),
        impuesto_dr=raw.impuesto_dr,
        tipo_factor_dr=raw.tipo_factor_dr,
        tasa_o_cuota_dr=_decimal(raw.tasa_o_cuota_dr),
        importe_dr=_rep_optional_amount(raw.importe_dr, currency, policy, unsupported),
    )


def _build_retencion_dr(
    raw: RawRetencionDR, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> RetencionDR:
    return RetencionDR(
        base_dr=_rep_amount(raw.base_dr, currency, policy, unsupported),
        impuesto_dr=raw.impuesto_dr,
        tipo_factor_dr=raw.tipo_factor_dr,
        tasa_o_cuota_dr=Decimal(raw.tasa_o_cuota_dr),
        importe_dr=_rep_amount(raw.importe_dr, currency, policy, unsupported),
    )


def _build_impuestos_p(
    raw: RawImpuestosP | None, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> ImpuestosP | None:
    """Payment-level taxes, kept alongside (never merged with) the per-document ones."""
    if raw is None:
        return None
    return ImpuestosP(
        traslados_p=tuple(
            _build_traslado_p(t, currency, policy, unsupported) for t in raw.traslados_p
        ),
        retenciones_p=tuple(
            _build_retencion_p(r, currency, policy, unsupported) for r in raw.retenciones_p
        ),
    )


def _build_traslado_p(
    raw: RawTrasladoP, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> TrasladoP:
    return TrasladoP(
        base_p=_rep_amount(raw.base_p, currency, policy, unsupported),
        impuesto_p=raw.impuesto_p,
        tipo_factor_p=raw.tipo_factor_p,
        tasa_o_cuota_p=_decimal(raw.tasa_o_cuota_p),
        importe_p=_rep_optional_amount(raw.importe_p, currency, policy, unsupported),
    )


def _build_retencion_p(
    raw: RawRetencionP, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> RetencionP:
    return RetencionP(
        impuesto_p=raw.impuesto_p,
        importe_p=_rep_amount(raw.importe_p, currency, policy, unsupported),
    )


def _rep_amount(
    value: str, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> NormalizedAmount | None:
    """A REP amount in its own currency. Unsupported -> None.

    Unlike `_amount`, the currency is NOT already proven supported: on a type-P
    comprobante the header is XXX while MonedaP/MonedaDR are real currencies.
    """
    result = policy.normalize(value, currency)
    if isinstance(result, UnsupportedCurrency):
        unsupported.append(currency)
        return None
    return result


def _rep_optional_amount(
    value: str | None, currency: str, policy: MoneyPolicy, unsupported: list[str]
) -> NormalizedAmount | None:
    """An optional REP monetary fact. Absent stays absent."""
    return _rep_amount(value, currency, policy, unsupported) if value is not None else None
