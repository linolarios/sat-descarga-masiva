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
    """The **query** side's status filter for a Solicitud — not received status.

    These are our own codes for the *filter* a download query asks for, and they
    are what a persisted query record carries (``infrastructure/source/codec.py``).
    The SOAP boundary maps them to satcfdi's word vocabulary
    (``'Todos'``/``'Vigente'``/``'Cancelado'``); those words never reach the
    domain (§5).

    A downloaded document's own status is a **different** vocabulary:
    ``domain.model.fiscal_document.FiscalDocumentStatus`` (§6a).
    """

    TODOS = "0"
    VIGENTE = "1"
    CANCELADO = "2"
