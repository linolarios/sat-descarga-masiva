"""RawCfd — neutral, source-level CFDI representation (M2.3).

Contains only raw string values as found in the XML. No MoneyPolicy, no
Decimal normalization, no satcfdi types. Fiscal interpretation happens later
in fiscal/parse.build_fiscal_document.
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
