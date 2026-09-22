"""RawCfd — neutral, source-level CFDI representation (M2.3, extended in M2.3b).

Contains only raw string values as found in the XML. No MoneyPolicy, no
Decimal normalization, no satcfdi types. Fiscal interpretation happens later in
fiscal/parse.build_fiscal_document.

M2.3b adds the raw identity, relation and REP facts (UUID, CfdiRelacionados,
Pagos 2.0) plus the document fields the approved accounting rules already
require. Every value stays an uninterpreted source fact: no accounting meaning
is assigned here, and relation/payment catalog codes are never translated into
fiscal semantics.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RawConcepto:
    clave_prod_serv: str
    cantidad: str
    valor_unitario: str
    importe: str
    descuento: str | None = None


@dataclass(frozen=True)
class RawTraslado:
    impuesto: str
    tipo_factor: str
    tasa_o_cuota: str | None
    importe: str | None


@dataclass(frozen=True)
class RawRetencion:
    impuesto: str
    tasa_o_cuota: str
    importe: str


@dataclass(frozen=True)
class RawImpuestos:
    traslados: tuple[RawTraslado, ...] = ()
    retenciones: tuple[RawRetencion, ...] = ()
    total_traslados: str | None = None
    total_retenciones: str | None = None


@dataclass(frozen=True)
class RawCfdiRelacionados:
    """One CfdiRelacionados node: a relation code plus its related UUIDs.

    `tipo_relacion` is the bare catalog code (01), never the expanded
    description. What the code means (01 credit note, 04 substitution, 07
    advance) is interpreted downstream, not here.
    """

    tipo_relacion: str
    uuids: tuple[str, ...]


@dataclass(frozen=True)
class RawTrasladoDR:
    base_dr: str
    impuesto_dr: str
    tipo_factor_dr: str
    tasa_o_cuota_dr: str | None = None
    importe_dr: str | None = None


@dataclass(frozen=True)
class RawRetencionDR:
    base_dr: str
    impuesto_dr: str
    tipo_factor_dr: str
    tasa_o_cuota_dr: str
    importe_dr: str


@dataclass(frozen=True)
class RawImpuestosDR:
    """Document-level taxes for one DoctoRelacionado (REP 2.0)."""

    traslados_dr: tuple[RawTrasladoDR, ...] = ()
    retenciones_dr: tuple[RawRetencionDR, ...] = ()


@dataclass(frozen=True)
class RawTrasladoP:
    base_p: str
    impuesto_p: str
    tipo_factor_p: str
    tasa_o_cuota_p: str | None = None
    importe_p: str | None = None


@dataclass(frozen=True)
class RawRetencionP:
    """One RetencionesP entry: REP 2.0 carries ImpuestoP and ImporteP only."""

    impuesto_p: str
    importe_p: str


@dataclass(frozen=True)
class RawImpuestosP:
    """Payment-level tax summary (REP 2.0 ImpuestosP).

    Preserved as a source fact alongside the per-DoctoRelacionado ImpuestosDR.
    Which level is authoritative for accounting is a downstream policy decision.
    """

    traslados_p: tuple[RawTrasladoP, ...] = ()
    retenciones_p: tuple[RawRetencionP, ...] = ()


@dataclass(frozen=True)
class RawDoctoRelacionado:
    """One DoctoRelacionado: one CFDI related to one REP payment."""

    id_documento: str
    moneda_dr: str
    num_parcialidad: str
    imp_saldo_ant: str
    imp_pagado: str
    objeto_imp_dr: str
    imp_saldo_insoluto: str | None = None
    equivalencia_dr: str | None = None
    serie: str | None = None
    folio: str | None = None
    impuestos_dr: RawImpuestosDR | None = None


@dataclass(frozen=True)
class RawPago:
    """One Pago element. Multiple payments are never flattened into one."""

    fecha_pago: str
    forma_de_pago_p: str
    moneda_p: str
    monto: str
    tipo_cambio_p: str | None = None
    num_operacion: str | None = None
    doctos_relacionados: tuple[RawDoctoRelacionado, ...] = ()
    impuestos_p: RawImpuestosP | None = None


@dataclass(frozen=True)
class RawTotalesPagos:
    monto_total_pagos: str | None = None


@dataclass(frozen=True)
class RawPagos20:
    """The Pagos 2.0 complemento carried by a P (pago) comprobante."""

    version: str
    pagos: tuple[RawPago, ...] = ()
    totales: RawTotalesPagos | None = None


@dataclass(frozen=True)
class RawCfd:
    tipo: str
    version: str
    moneda: str
    tipo_cambio: str | None
    emisor_rfc: str
    receptor_rfc: str | None
    conceptos: tuple[RawConcepto, ...]
    impuestos: RawImpuestos
    total: str
    uuid: str | None = None
    subtotal: str | None = None
    descuento: str | None = None
    forma_pago: str | None = None
    metodo_pago: str | None = None
    regimen_fiscal_receptor: str | None = None
    cfdi_relacionados: tuple[RawCfdiRelacionados, ...] = ()
    pagos: RawPagos20 | None = None
