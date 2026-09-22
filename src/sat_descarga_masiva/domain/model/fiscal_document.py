"""Typed fiscal document + parse result (M2.3). Domain-only.

FiscalDocumentStatus is the SAT fiscal fact (UNKNOWN in CFDI-only M2); review
flags are additive review state; amounts are MoneyPolicy-normalized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from sat_descarga_masiva.domain.model.review import ReviewFlags
from sat_descarga_masiva.domain.model.value_objects import Rfc, Uuid
from sat_descarga_masiva.domain.policy.money import NormalizedAmount


class FiscalDocumentStatus(StrEnum):
    UNKNOWN = "unknown"
    VIGENTE = "vigente"
    CANCELLED = "cancelled"


class ParseOutcome(StrEnum):
    PARSED = "parsed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class Concepto:
    clave_prod_serv: str
    cantidad: Decimal
    valor_unitario: NormalizedAmount
    importe: NormalizedAmount
    descuento: NormalizedAmount | None = None


@dataclass(frozen=True)
class Traslado:
    impuesto: str
    tipo_factor: str
    tasa_o_cuota: Decimal | None
    importe: NormalizedAmount | None


@dataclass(frozen=True)
class Retencion:
    impuesto: str
    tasa_o_cuota: Decimal
    importe: NormalizedAmount


@dataclass(frozen=True)
class Impuestos:
    traslados: tuple[Traslado, ...] = ()
    retenciones: tuple[Retencion, ...] = ()
    total_traslados: NormalizedAmount | None = None
    total_retenciones: NormalizedAmount | None = None


@dataclass(frozen=True)
class CfdiRelacionados:
    """One CfdiRelacionados group: a relation code plus its related UUIDs.

    `tipo_relacion` stays the bare source code (01/04/07); what it means for
    cancellation, substitution or accounting is decided downstream, not here.
    """

    tipo_relacion: str
    uuids: tuple[Uuid, ...] = ()


@dataclass(frozen=True)
class TrasladoDR:
    """One DoctoRelacionado transfer. Amounts are denominated in MonedaDR."""

    base_dr: NormalizedAmount | None
    impuesto_dr: str
    tipo_factor_dr: str
    tasa_o_cuota_dr: Decimal | None = None
    importe_dr: NormalizedAmount | None = None


@dataclass(frozen=True)
class RetencionDR:
    """One DoctoRelacionado retention. REP 2.0 omits BaseDR on this level."""

    base_dr: NormalizedAmount | None
    impuesto_dr: str
    tipo_factor_dr: str
    tasa_o_cuota_dr: Decimal
    importe_dr: NormalizedAmount | None


@dataclass(frozen=True)
class ImpuestosDR:
    """Document-level taxes for one related document (REP 2.0 ImpuestosDR).

    Every element is kept in source order: aggregating by tax type/rate/factor
    is downstream policy, never a blind `impuestos_dr[0]` selection.
    """

    traslados_dr: tuple[TrasladoDR, ...] = ()
    retenciones_dr: tuple[RetencionDR, ...] = ()


@dataclass(frozen=True)
class TrasladoP:
    """One payment-level transfer. Amounts are denominated in MonedaP."""

    base_p: NormalizedAmount | None
    impuesto_p: str
    tipo_factor_p: str
    tasa_o_cuota_p: Decimal | None = None
    importe_p: NormalizedAmount | None = None


@dataclass(frozen=True)
class RetencionP:
    """One RetencionesP entry: REP 2.0 carries ImpuestoP and ImporteP only."""

    impuesto_p: str
    importe_p: NormalizedAmount | None


@dataclass(frozen=True)
class ImpuestosP:
    """Payment-level tax summary (REP 2.0 ImpuestosP).

    Preserved as a source fact alongside the per-document ImpuestosDR; which
    level is authoritative for accounting is a downstream policy decision.
    """

    traslados_p: tuple[TrasladoP, ...] = ()
    retenciones_p: tuple[RetencionP, ...] = ()


@dataclass(frozen=True)
class DoctoRelacionado:
    """One CFDI related to one REP payment.

    `uuid` is the UUID of the document being paid — distinct from
    FiscalDocument.source_uuid, which identifies the REP itself. Amounts are
    denominated in MonedaDR and are preserved exactly as sourced: the
    ImpSaldoAnt - ImpPagado == ImpSaldoInsoluto invariant is not checked here.
    """

    uuid: Uuid
    moneda_dr: str
    num_parcialidad: int
    imp_saldo_ant: NormalizedAmount | None
    imp_pagado: NormalizedAmount | None
    imp_saldo_insoluto: NormalizedAmount | None
    objeto_imp_dr: str
    equivalencia_dr: Decimal | None = None
    serie: str | None = None
    folio: str | None = None
    impuestos_dr: ImpuestosDR | None = None


@dataclass(frozen=True)
class Pago:
    """One Pago element. Multiple payments are never flattened into one.

    `monto` and all ImpuestosP amounts are denominated in `moneda_p`, which is
    independent of the comprobante header currency.
    """

    fecha_pago: str
    forma_de_pago_p: str
    moneda_p: str
    monto: NormalizedAmount | None
    tipo_cambio_p: Decimal | None = None
    num_operacion: str | None = None
    doctos_relacionados: tuple[DoctoRelacionado, ...] = ()
    impuestos_p: ImpuestosP | None = None


@dataclass(frozen=True)
class TotalesPagos:
    """REP 2.0 Totales. MontoTotalPagos is expressed in national currency."""

    monto_total_pagos: NormalizedAmount | None = None


@dataclass(frozen=True)
class Pagos20:
    """The Pagos 2.0 complemento carried by a P (pago) comprobante."""

    version: str
    pagos: tuple[Pago, ...] = ()
    totales: TotalesPagos | None = None


@dataclass(frozen=True)
class FiscalDocument:
    tipo: str
    version: str
    moneda: str
    tipo_cambio: Decimal | None
    emisor_rfc: Rfc
    receptor_rfc: Rfc | None
    conceptos: tuple[Concepto, ...]
    impuestos: Impuestos
    total: NormalizedAmount | None
    source_hash: str
    status: FiscalDocumentStatus = FiscalDocumentStatus.UNKNOWN
    review_flags: ReviewFlags = field(default_factory=ReviewFlags)
    source_uuid: Uuid | None = None
    subtotal: NormalizedAmount | None = None
    descuento: NormalizedAmount | None = None
    forma_pago: str | None = None
    metodo_pago: str | None = None
    regimen_fiscal_receptor: str | None = None
    cfdi_relacionados: tuple[CfdiRelacionados, ...] = ()
    pagos: Pagos20 | None = None


@dataclass(frozen=True)
class FiscalParseResult:
    outcome: ParseOutcome
    document: FiscalDocument | None
    review_flags: ReviewFlags = field(default_factory=ReviewFlags)
