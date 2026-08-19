"""SAT catalogs used to build a query."""

from __future__ import annotations

from enum import StrEnum


class ServiceType(StrEnum):
    CFDI = "cfdi"
    RETENCIONES = "retenciones"


class RequestType(StrEnum):
    CFDI = "CFDI"  # full XML
    METADATA = "Metadata"  # summary incl. vigente/cancelado status


class Direction(StrEnum):
    EMITIDOS = "emitidos"
    RECIBIDOS = "recibidos"


class DocumentStatus(StrEnum):
    # TODO: confirm exact catalog values against the SAT Solicitud doc.
    TODOS = "0"
    VIGENTE = "1"
    CANCELADO = "2"
