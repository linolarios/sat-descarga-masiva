"""Typed fiscal document + parse result (M2.3). Domain-only.

FiscalDocumentStatus is the SAT fiscal fact (UNKNOWN in CFDI-only M2); review
flags are additive review state; amounts are MoneyPolicy-normalized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from sat_descarga_masiva.domain.model.review import ReviewFlags
from sat_descarga_masiva.domain.model.value_objects import Rfc
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


@dataclass(frozen=True)
class FiscalParseResult:
    outcome: ParseOutcome
    document: FiscalDocument | None
    review_flags: ReviewFlags = field(default_factory=ReviewFlags)
